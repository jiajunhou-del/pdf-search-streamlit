"""
Per-viewer "My History" (recent searches) and "Favorites" (bookmarked
results), stored as one small JSON file per person in the same shared
Google Drive folder as the PDFs and the search index -- reusing the
store's generic named-file read/write (drive_store.py) rather than a
separate database.
 
Identity comes from st.user.email, which Streamlit populates once the
app's Community Cloud "Sharing" setting restricts viewers by email --
that's what makes each signed-in colleague's history/favorites their own,
even though everyone's uploads still land in the same shared Drive
identity for storage. Locally, or before that restriction is turned on,
there's no signed-in viewer to key data by, so both features degrade
gracefully to "sign in to use this."
"""
import json
import re
from datetime import datetime, timezone
 
import streamlit as st
 
MAX_HISTORY = 50
 
 
def _safe_filename(email: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", email.lower()).strip("_")
    return f"userdata__{slug}.json"
 
 
def current_user_email(store_mode: str = None):
    """The signed-in viewer's email, or None if there isn't one.
 
    st.user is only populated when Community Cloud's "Sharing" setting
    restricts viewers by email (which requires signing in) -- without
    that, there's no real identity to key History/Favorites by. In local
    dev mode only, fall back to a stable placeholder so the feature can
    still be exercised while developing.
    """
    try:
        if getattr(st.user, "is_logged_in", False):
            email = getattr(st.user, "email", None)
            if email:
                return email
    except Exception:
        pass
    if store_mode == "local":
        return "local-dev@example.com"
    return None
 
 
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
 
 
def humanize_ago(iso_ts: str) -> str:
    try:
        then = datetime.fromisoformat(iso_ts)
    except Exception:
        return ""
    seconds = (datetime.now(timezone.utc) - then).total_seconds()
    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min ago"
    hours = int(minutes // 60)
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(hours // 24)
    return f"{days} day{'s' if days != 1 else ''} ago"
 
 
@st.cache_data(show_spinner=False, ttl=30)
def _load(_store, email: str) -> dict:
    raw = _store.load_named_bytes(_safe_filename(email))
    if not raw:
        return {"history": [], "favorites": []}
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return {"history": [], "favorites": []}
    data.setdefault("history", [])
    data.setdefault("favorites", [])
    return data
 
 
def _save(store, email: str, data: dict):
    store.save_named_bytes(
        _safe_filename(email),
        json.dumps(data).encode("utf-8"),
        mimetype="application/json",
    )
    # The cached _load() result is now stale for every viewer -- clear it
    # so the next read (this rerun or anyone else's) picks up the change
    # instead of waiting up to 30s for the ttl to expire.
    _load.clear()
 
 
def add_history_entry(store, email, query: str, result_count: int):
    if not email:
        return
    data = dict(_load(store, email))
    entry = {"query": query, "result_count": result_count, "ts": _now_iso()}
    # De-dupe: re-searching something already in history just moves it to
    # the top with a fresh timestamp, instead of listing it twice.
    history = [entry] + [h for h in data.get("history", []) if h.get("query") != query]
    data["history"] = history[:MAX_HISTORY]
    try:
        _save(store, email, data)
    except Exception:
        pass  # best-effort -- a failed history write shouldn't block search
 
 
def get_history(store, email, limit: int = None):
    if not email:
        return []
    history = _load(store, email).get("history", [])
    return history[:limit] if limit else history
 
 
def is_favorite(store, email, doc_id: str, page_number: int) -> bool:
    if not email:
        return False
    favorites = _load(store, email).get("favorites", [])
    return any(f["doc_id"] == doc_id and f["page_number"] == page_number for f in favorites)
 
 
def toggle_favorite(store, email, doc_id: str, filename: str, page_number: int) -> bool:
    """Adds or removes a favorite; returns the new state (True = now a favorite)."""
    if not email:
        return False
    data = dict(_load(store, email))
    favorites = list(data.get("favorites", []))
    key = (doc_id, page_number)
    already = any((f["doc_id"], f["page_number"]) == key for f in favorites)
    if already:
        favorites = [f for f in favorites if (f["doc_id"], f["page_number"]) != key]
        now_favorite = False
    else:
        favorites = [
            {
                "doc_id": doc_id,
                "filename": filename,
                "page_number": page_number,
                "ts": _now_iso(),
            }
        ] + favorites
        now_favorite = True
    data["favorites"] = favorites
    _save(store, email, data)
    return now_favorite
 
 
def get_favorites(store, email):
    if not email:
        return []
    return _load(store, email).get("favorites", [])
 
