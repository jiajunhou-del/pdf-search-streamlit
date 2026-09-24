
"""
File storage backend for the search app.
 
Real deployments store everything in a shared Google Drive folder, so the
PDF library lives in your company's Drive (not on Streamlit's disk, which
is wiped whenever the app sleeps or redeploys) -- that's what makes this
safe to run on Streamlit Community Cloud.
 
Two modes, chosen automatically:
 
  - Google Drive mode: used when st.secrets["gcp_oauth"] and
    st.secrets["drive_folder_id"] are set (see README.md for how to create
    these). This uses a real Google account's own Drive (via OAuth, the
    same kind of "Sign in with Google" flow you've seen elsewhere) rather
    than a service account -- that's the deliberate choice here, because
    an organization's Workspace admin can block sharing files with an
    outside/robot account (a service account's email looks external to
    Workspace), which a normal person's own Drive access doesn't run into.
    One person (whoever runs get_refresh_token.py once, see README.md)
    authorizes the app to act as them; after that, the app always acts as
    that person's Drive identity -- nobody else needs to log in to use
    the search tool itself.
 
  - Local mode: used when those secrets are absent (e.g. while developing
    on your own machine without a Google Cloud project yet). Files are
    just kept in ./local_drive_cache/ instead. Everything else in the app
    behaves identically, so you can build/test the whole thing locally
    before wiring up real Drive credentials.
 
Either way, the search index itself (search_index.pkl -- the extracted
text chunks with page numbers) is stored as one file *inside the same
folder*. That's the key trick that keeps cold starts fast even with
hundreds of PDFs: on startup the app downloads that one small index file,
not every PDF. Full PDF bytes / page thumbnails are only fetched lazily,
the first time something actually needs to view that page or file, and
cached locally for the life of the running instance.
"""
import io
import os
 
try:
    import streamlit as st
except ImportError:  # pragma: no cover - only relevant when unit testing
    st = None
 
INDEX_FILENAME = "search_index.pkl"
LOCAL_CACHE_DIR = os.path.join(os.path.dirname(__file__), "local_drive_cache")
 
 
def _has_drive_secrets() -> bool:
    if st is None:
        return False
    try:
        return "gcp_oauth" in st.secrets and "drive_folder_id" in st.secrets
    except Exception:
        return False
 
 
class LocalStore:
    """Fallback used when no Google Drive credentials are configured."""
 
    mode = "local"
 
    def __init__(self):
        os.makedirs(LOCAL_CACHE_DIR, exist_ok=True)
 
    def _path(self, name):
        return os.path.join(LOCAL_CACHE_DIR, name)
 
    def list_files(self):
        out = []
        for name in os.listdir(LOCAL_CACHE_DIR):
            if name == INDEX_FILENAME:
                continue
            out.append({"id": name, "name": name})
        return out
 
    def upload_bytes(self, filename: str, data: bytes) -> str:
        """Returns the file's id (here, just its filename)."""
        with open(self._path(filename), "wb") as f:
            f.write(data)
        return filename
 
    def download_bytes(self, file_id: str) -> bytes:
        with open(self._path(file_id), "rb") as f:
            return f.read()
 
    def delete_file(self, file_id: str):
        path = self._path(file_id)
        if os.path.exists(path):
            os.remove(path)
 
    def load_index_bytes(self):
        path = self._path(INDEX_FILENAME)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return f.read()
 
    def save_index_bytes(self, data: bytes):
        with open(self._path(INDEX_FILENAME), "wb") as f:
            f.write(data)
 
 
class DriveStore:
    """
    Real Google Drive-backed storage, scoped to one folder in the Drive of
    whichever person ran get_refresh_token.py once (see README.md). Uses
    that person's own stored OAuth refresh token rather than a service
    account, so nothing needs to be "shared with an external account" --
    the app is just acting as that person, the same as if they were
    clicking around Drive themselves.
    """
 
    mode = "drive"
 
    def __init__(self):
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
 
        oauth = st.secrets["gcp_oauth"]
        self.folder_id = st.secrets["drive_folder_id"]
        creds = Credentials(
            token=None,
            refresh_token=oauth["refresh_token"],
            client_id=oauth["client_id"],
            client_secret=oauth["client_secret"],
            token_uri="https://oauth2.googleapis.com/token",
            scopes=["https://www.googleapis.com/auth/drive"],
        )
        self.service = build("drive", "v3", credentials=creds, cache_discovery=False)
        self._index_file_id_cache = None
 
    # Every .execute()/.next_chunk() call below passes num_retries=3.
    # Without it, googleapiclient's default is 0 retries -- so a single
    # transient network hiccup talking to Google's servers (observed in
    # production as "SSLError: [SSL: RECORD_LAYER_FAILURE] record layer
    # failure", a one-off blip rather than anything actually wrong with
    # the credentials or the request) was propagating straight up as an
    # uncaught exception and crashing the whole app with Streamlit's
    # generic "Oh no. Error running app" page. num_retries makes
    # googleapiclient retry that kind of transient failure internally
    # with exponential backoff before giving up.
 
    def _find(self, name: str):
        q = (
            f"'{self.folder_id}' in parents and name = '{name}' "
            "and trashed = false"
        )
        res = self.service.files().list(
            q=q, fields="files(id, name)", spaces="drive"
        ).execute(num_retries=3)
        files = res.get("files", [])
        return files[0]["id"] if files else None
 
    def list_files(self):
        q = f"'{self.folder_id}' in parents and trashed = false"
        res = self.service.files().list(
            q=q, fields="files(id, name)", spaces="drive", pageSize=1000
        ).execute(num_retries=3)
        return [
            f for f in res.get("files", []) if f["name"] != INDEX_FILENAME
        ]
 
    def upload_bytes(self, filename: str, data: bytes) -> str:
        from googleapiclient.http import MediaIoBaseUpload
 
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype="application/pdf")
        meta = {"name": filename, "parents": [self.folder_id]}
        created = self.service.files().create(
            body=meta, media_body=media, fields="id"
        ).execute(num_retries=3)
        return created["id"]
 
    def download_bytes(self, file_id: str) -> bytes:
        from googleapiclient.http import MediaIoBaseDownload
 
        request = self.service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk(num_retries=3)
        return buf.getvalue()
 
    def delete_file(self, file_id: str):
        self.service.files().delete(fileId=file_id).execute(num_retries=3)
 
    def load_index_bytes(self):
        file_id = self._find(INDEX_FILENAME)
        if not file_id:
            return None
        return self.download_bytes(file_id)
 
    def save_index_bytes(self, data: bytes):
        from googleapiclient.http import MediaIoBaseUpload
 
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype="application/octet-stream")
        existing_id = self._find(INDEX_FILENAME)
        if existing_id:
            self.service.files().update(
                fileId=existing_id, media_body=media
            ).execute(num_retries=3)
        else:
            meta = {"name": INDEX_FILENAME, "parents": [self.folder_id]}
            self.service.files().create(
                body=meta, media_body=media
            ).execute(num_retries=3)
 
 
def get_store():
    """Picks Drive mode if configured, otherwise falls back to local disk."""
    if _has_drive_secrets():
        return DriveStore()
    return LocalStore()
 
