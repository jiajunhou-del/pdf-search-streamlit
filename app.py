"""
Streamlit version of the PDF technical-manual search tool.
 
Why this exists alongside the original FastAPI+HTML version: deploying
this on Streamlit Community Cloud gives every user a real, trusted HTTPS
address automatically (no self-signed certificate, no dependence on one
PC's IP address staying the same) -- which is exactly the pair of
problems that kept coming up with the original self-hosted setup. The
PDF library itself is *not* stored on Streamlit's disk (which is wiped
whenever the app sleeps or is redeployed); see drive_store.py for how it
is kept in a shared Google Drive folder instead.
 
Run locally with:   streamlit run app.py
Deploy: push this folder to a GitHub repo and connect it on
share.streamlit.io -- see README.md for the full checklist (Google Drive
service account, secrets.toml, restricting who can open the app).
"""
import html
import re
 
import streamlit as st
 
from drive_store import get_store
from pdf_processor import extract_pages, chunk_pages
from search_engine import SearchIndex
 
st.set_page_config(page_title="Technical Manual Search", page_icon="🔧", layout="wide")
 
 
# ---------------------------------------------------------------------------
# Shared index: one instance for the whole running app (all users share the
# same library), backed by whatever store.load_index_bytes() returns -- so
# even a fresh instance rebuilds itself from Google Drive on cold start.
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner="Connecting to storage and loading the search index…")
def get_index():
    store = get_store()
    return SearchIndex(store)
 
 
index = get_index()
store = index.store
 
 
@st.cache_data(show_spinner=False)
def _download_bytes(_store, file_id: str) -> bytes:
    return _store.download_bytes(file_id)
 
 
@st.cache_data(show_spinner=False)
def _render_thumbnail(_store, file_id: str, page_number: int) -> bytes:
    import pymupdf as fitz
 
    data = _download_bytes(_store, file_id)
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        page = doc[page_number - 1]
        pix = page.get_pixmap(matrix=fitz.Matrix(0.6, 0.6))
        return pix.tobytes("png")
    finally:
        doc.close()
 
 
def _highlight(snippet: str, terms: list) -> str:
    escaped = html.escape(snippet)
    terms = sorted({t for t in terms if len(t) > 1}, key=len, reverse=True)
    for term in terms:
        pattern = re.compile(r"(" + re.escape(html.escape(term)) + r")", re.IGNORECASE)
        escaped = pattern.sub(r"**\1**", escaped)
    return escaped
 
 
# ---------------------------------------------------------------------------
# Voice input: Streamlit has no built-in microphone widget, so this embeds
# the same browser Web Speech API used in the original prototype. Recognized
# text is written into the page's URL query string, which triggers a normal
# Streamlit rerun that Python then reads back via st.query_params. This
# needs a real HTTPS origin to access the microphone at all -- which is
# exactly what Streamlit Community Cloud provides automatically, no
# certificate hassle required.
# ---------------------------------------------------------------------------
_VOICE_HTML = """
<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;">
  <button id="micBtn" style="padding:8px 16px;border-radius:8px;border:none;
    background:#5b5bd6;color:white;font-size:14px;cursor:pointer;">
    🎤 Speak your query
  </button>
  <div id="voiceStatus" style="margin-top:6px;font-size:13px;color:#555;"></div>
</div>
<script>
const btn = document.getElementById('micBtn');
const statusEl = document.getElementById('voiceStatus');
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
if (!SR) {
  statusEl.innerText = 'Voice input needs Chrome/Edge (Web Speech API not available here).';
  btn.disabled = true;
} else {
  const recog = new SR();
  recog.lang = 'en-US';
  recog.interimResults = true;
  recog.maxAlternatives = 1;
  recog.onstart = () => { statusEl.innerText = '🎙️ Listening… speak now'; };
  recog.onerror = (e) => {
    const messages = {
      'no-speech': 'No speech detected -- try again.',
      'not-allowed': 'Microphone access blocked. Allow it in the browser site settings.',
      'service-not-allowed': 'Microphone access blocked. Allow it in the browser site settings.',
      'audio-capture': 'No microphone found.',
      'network': 'Network error during speech recognition.'
    };
    statusEl.innerText = messages[e.error] || ('Voice error: ' + e.error);
  };
  recog.onresult = (e) => {
    let text = '';
    for (let i = 0; i < e.results.length; i++) { text += e.results[i][0].transcript; }
    statusEl.innerText = text;
    if (e.results[e.results.length - 1].isFinal && text.trim()) {
      const url = new URL(window.parent.location.href);
      url.searchParams.set('voice_query', text.trim());
      window.parent.location.href = url.toString();
    }
  };
  btn.onclick = () => { statusEl.innerText = 'Starting…'; recog.start(); };
}
</script>
"""
 
# ---------------------------------------------------------------------------
# Header + search
# ---------------------------------------------------------------------------
st.title("🔧 Technical Manual Search")
st.caption(
    "Search across every service manual in the shared library, by keyword or by voice. "
    f"Storage: {'Google Drive' if store.mode == 'drive' else 'local (dev mode, no Drive configured)'}"
)
 
voice_query = st.query_params.get("voice_query", "")
if voice_query:
    del st.query_params["voice_query"]
 
docs = index.list_documents()
doc_options = {"All documents": None}
for d in docs:
    doc_options[d["filename"]] = d["doc_id"]
 
col_q, col_scope = st.columns([3, 1])
with col_q:
    query = st.text_input(
        "Search",
        value=voice_query,
        placeholder="e.g. overcurrent fault on PCB, HMI calibration, inverter parameter…",
        label_visibility="collapsed",
    )
with col_scope:
    scope_label = st.selectbox(
        "Search within", list(doc_options.keys()), label_visibility="collapsed"
    )
 
st.components.v1.html(_VOICE_HTML, height=70)
 
# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
if query.strip():
    hits = index.search(query, top_k=8, doc_id=doc_options[scope_label])
    if not hits:
        st.info("No matching pages found. Try a different phrasing or check the synonym list.")
    for hit in hits:
        with st.container(border=True):
            c1, c2 = st.columns([1, 5])
            with c1:
                try:
                    thumb = _render_thumbnail(store, hit["doc_id"], hit["page_number"])
                    st.image(thumb, width=100)
                except Exception:
                    st.write("📄")
            with c2:
                st.markdown(f"**{hit['filename']}** — page {hit['page_number']}")
                st.markdown(_highlight(hit["snippet"], hit["highlight_terms"]))
                pdf_bytes = _download_bytes(store, hit["doc_id"])
                st.download_button(
                    "Open / download PDF",
                    data=pdf_bytes,
                    file_name=hit["filename"],
                    mime="application/pdf",
                    key=f"dl_{hit['doc_id']}_{hit['page_number']}",
                )
else:
    st.caption("Type a query above, or tap the mic and speak.")
 
# ---------------------------------------------------------------------------
# Library management
# ---------------------------------------------------------------------------
with st.expander(f"📚 Document library ({len(docs)} files)"):
    # A plain st.file_uploader keeps holding its uploaded file across
    # reruns -- without resetting it, calling st.rerun() after a
    # successful upload would just re-trigger the same upload again on
    # the very next run (and the run after that...), silently creating
    # duplicate copies. Giving the widget a key that changes after each
    # successful upload forces it back to empty.
    if "uploader_key" not in st.session_state:
        st.session_state.uploader_key = 0
    if "last_uploaded_signature" not in st.session_state:
        st.session_state.last_uploaded_signature = None
 
    uploaded = st.file_uploader(
        "Add a PDF to the shared library",
        type=["pdf"],
        key=f"uploader_{st.session_state.uploader_key}",
    )
    if uploaded is not None:
        data = uploaded.getvalue()
        signature = (uploaded.name, len(data))
        if signature == st.session_state.last_uploaded_signature:
            # Same file as the last successful upload -- the widget
            # hasn't been swapped out yet, ignore this rerun's copy.
            pass
        else:
            import pymupdf as fitz
 
            doc = fitz.open(stream=data, filetype="pdf")
            pages = [
                {"page_number": i + 1, "text": p.get_text("text")}
                for i, p in enumerate(doc)
            ]
            doc.close()
            if not any(p["text"].strip() for p in pages):
                st.error(
                    "No extractable text found -- this looks like a scanned/image-only "
                    "PDF, which needs OCR before it can be indexed (not enabled here)."
                )
            else:
                file_id = store.upload_bytes(uploaded.name, data)
                chunks = chunk_pages(pages)
                index.add_document(file_id, uploaded.name, chunks)
                st.session_state.last_uploaded_signature = signature
                st.session_state.uploader_key += 1
                st.success(f"Indexed {uploaded.name} ({len(pages)} pages).")
                st.rerun()
 
    for d in docs:
        row1, row2 = st.columns([5, 1])
        row1.write(d["filename"])
        if row2.button("Delete", key=f"del_{d['doc_id']}"):
            index.remove_document(d["doc_id"])
            store.delete_file(d["doc_id"])
            st.rerun()
 
