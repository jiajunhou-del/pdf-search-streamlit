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
# Look and feel. The color palette itself lives in .streamlit/config.toml
# (Streamlit's own [theme] section -- the supported way to theme buttons,
# inputs, etc. so it keeps working across Streamlit upgrades). This block
# only adds the handful of things that theming alone can't do: a nicer
# font, and some polish on elements this file builds directly (so there's
# no risk of it fighting Streamlit's own internals if they change).
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@500;600;700&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', -apple-system, sans-serif; }
 
    /* Buttons, downloads, and links read like console controls: sharp
       corners, thin amber-tinted border, uppercase monospace label --
       consistent with the rest of the "instrument panel" look instead of
       Streamlit's default soft rounded buttons. */
    .stButton > button, .stDownloadButton > button, .stLinkButton > a {
        border-radius: 4px;
        border: 1px solid rgba(245,166,35,0.35);
        font-family: 'IBM Plex Mono', monospace;
        font-weight: 600;
        font-size: 13px;
        letter-spacing: 0.3px;
        text-transform: uppercase;
        transition: border-color 0.1s ease-in-out, transform 0.05s ease-in-out;
    }
    .stButton > button:hover, .stDownloadButton > button:hover, .stLinkButton > a:hover {
        border-color: #F5A623;
    }
    .stButton > button:active, .stDownloadButton > button:active {
        transform: scale(0.98);
    }
    .stTextInput input, div[data-baseweb="select"] > div {
        border-radius: 4px !important;
    }
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 4px !important;
    }
    /* Expander headers (Document library) get the same console treatment. */
    [data-testid="stExpander"] summary {
        font-family: 'IBM Plex Mono', monospace;
        letter-spacing: 0.3px;
        text-transform: uppercase;
        font-size: 13px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
 
 
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
 
 
# Community Cloud's free tier caps this whole process at ~1GB RAM, and
# every distinct PDF ever opened by any user was being kept in memory
# forever (st.cache_data has no eviction by default) -- with enough
# different manuals opened across a session or two, that alone was enough
# to blow the limit. max_entries + ttl bound how many full PDFs stay
# cached at once, evicting the least-recently-used ones instead of
# growing without limit.
@st.cache_data(show_spinner=False, max_entries=25, ttl=3600)
def _download_bytes(_store, file_id: str) -> bytes:
    return _store.download_bytes(file_id)
 
 
@st.cache_data(show_spinner=False, max_entries=50, ttl=3600)
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
 
 
@st.cache_data(show_spinner=False, max_entries=50, ttl=3600)
def _render_page_image(_store, file_id: str, page_number: int) -> bytes:
    """A larger, actually-readable render of one page.
 
    The first version of "Preview this page" embedded the whole PDF as a
    base64 data: URI inside an iframe (st.components.v1.html) so the
    browser's own PDF viewer could jump to the right page. That worked
    for small files, but for a large manual (tens of MB, the same file
    Google Drive's own preview refused to open for being "too large") the
    encoded page just rendered blank -- almost certainly hitting a size
    ceiling either in the browser's data: URI handling or in Streamlit's
    component message size. Rendering only the *one requested page* as an
    image sidesteps that completely: its size depends only on that page's
    content, never on how large or how many pages the source PDF has.
    """
    import pymupdf as fitz
 
    data = _download_bytes(_store, file_id)
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        page = doc[page_number - 1]
        # Higher resolution than the thumbnail -- meant to actually be
        # read, not just recognized at a glance.
        pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
        return pix.tobytes("png")
    finally:
        doc.close()
 
 
@st.cache_data(show_spinner=False, max_entries=25, ttl=3600)
def _page_count(_store, file_id: str) -> int:
    import pymupdf as fitz
 
    data = _download_bytes(_store, file_id)
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        return doc.page_count
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
<div style="font-family:'IBM Plex Mono',monospace;">
  <button id="micBtn" style="padding:9px 18px;border-radius:4px;
    border:1px solid #F5A623;background:#141A21;color:#F5A623;
    font-size:13px;font-weight:600;letter-spacing:0.5px;text-transform:uppercase;
    cursor:pointer;">
    ● Speak Query
  </button>
  <div id="voiceStatus" style="margin-top:8px;font-size:12px;color:#8B98A5;
    font-family:'Inter',-apple-system,sans-serif;"></div>
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
st.markdown(
    """
    <div style="display:flex;align-items:center;gap:16px;margin-bottom:2px;">
      <div style="width:50px;height:50px;border:1px solid #F5A623;border-radius:4px;
        background:#141A21;display:flex;align-items:center;justify-content:center;
        font-size:24px;flex-shrink:0;">
        🔧
      </div>
      <div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:22px;font-weight:700;
          color:#F1F5F9;letter-spacing:1px;line-height:1.2;text-transform:uppercase;">
          Technical Manual Search
        </div>
        <div style="font-size:13px;color:#8B98A5;margin-top:3px;">
          Search across every service manual in the shared library, by keyword or by voice.
        </div>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)
storage_ok = store.mode == "drive"
st.markdown(
    f"""
    <div style="display:inline-flex;align-items:center;gap:7px;margin:14px 0 20px 0;
      padding:4px 10px;border-radius:4px;font-family:'IBM Plex Mono',monospace;
      font-size:11px;font-weight:600;letter-spacing:0.5px;text-transform:uppercase;
      background:{'rgba(34,197,94,0.10)' if storage_ok else 'rgba(245,166,35,0.10)'};
      border:1px solid {'rgba(34,197,94,0.35)' if storage_ok else 'rgba(245,166,35,0.35)'};
      color:{'#4ADE80' if storage_ok else '#F5A623'};">
      <span style="width:6px;height:6px;border-radius:50%;
        background:{'#22C55E' if storage_ok else '#F5A623'};
        box-shadow:0 0 6px {'#22C55E' if storage_ok else '#F5A623'};"></span>
      Storage // {'Google Drive' if storage_ok else 'Local (dev mode)'}
    </div>
    """,
    unsafe_allow_html=True,
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
    try:
        hits = index.search(query, top_k=8, doc_id=doc_options[scope_label])
    except Exception as e:
        st.error(
            f"Search failed: {e}. If this keeps happening, use the app's "
            "'Reboot app' option (or ask whoever manages it to) to clear "
            "the search index and rebuild it fresh."
        )
        hits = []
    if not hits:
        st.info("No matching pages found. Try a different phrasing or check the synonym list.")
    for result_index, hit in enumerate(hits):
        with st.container(border=True):
            c1, c2 = st.columns([1, 5])
            with c1:
                try:
                    thumb = _render_thumbnail(store, hit["doc_id"], hit["page_number"])
                    st.image(thumb, width=100)
                except Exception:
                    st.write("📄")
            with c2:
                score_pct = round(hit["score"] * 100)
                if score_pct >= 70:
                    badge_bg = "rgba(34,197,94,0.12)"
                    badge_border = "rgba(34,197,94,0.4)"
                    badge_fg = "#4ADE80"
                elif score_pct >= 40:
                    badge_bg = "rgba(245,166,35,0.12)"
                    badge_border = "rgba(245,166,35,0.4)"
                    badge_fg = "#F5A623"
                else:
                    badge_bg = "rgba(148,163,184,0.12)"
                    badge_border = "rgba(148,163,184,0.35)"
                    badge_fg = "#94A3B8"
                st.markdown(
                    "<div style='font-size:15px;display:flex;align-items:center;"
                    "gap:10px;flex-wrap:wrap;'>"
                    "<span style=\"font-family:'IBM Plex Mono',monospace;"
                    "font-weight:600;color:#F1F5F9;\">"
                    f"{html.escape(hit['filename'])}</span>"
                    "<span style=\"font-family:'IBM Plex Mono',monospace;"
                    f"color:#8B98A5;font-size:13px;\">P.{hit['page_number']}</span>"
                    "<span style=\"font-family:'IBM Plex Mono',monospace;"
                    f"background:{badge_bg};border:1px solid {badge_border};"
                    f"color:{badge_fg};padding:2px 8px;border-radius:4px;"
                    f"font-size:11px;font-weight:600;letter-spacing:0.3px;\">"
                    f"MATCH {score_pct}%</span>"
                    "</div>",
                    unsafe_allow_html=True,
                )
                st.markdown(_highlight(hit["snippet"], hit["highlight_terms"]))
                btn1, btn2 = st.columns([1, 1])
                with btn1:
                    # Google Drive's own web preview has an undocumented
                    # file-size ceiling and just refuses to render bigger
                    # manuals ("このファイルはサイズが大きすぎるため、プレ
                    # ビューできません") -- that's a Drive limitation we
                    # can't work around by asking it nicely. Instead this
                    # renders the page using the *browser's own* built-in
                    # PDF viewer (the same one Chrome/Edge/Firefox use for
                    # any PDF you open), fed the file's actual bytes
                    # directly -- no size ceiling, and it also means
                    # colleagues don't need their own Drive access to the
                    # folder just to preview a page; only this app's own
                    # Drive connection is used.
                    pv_ready_key = f"pvready_{hit['doc_id']}_{hit['page_number']}_{result_index}"
                    if not st.session_state.get(pv_ready_key):
                        if st.button(
                            "👀 Preview this page",
                            key=f"pvprep_{hit['doc_id']}_{hit['page_number']}_{result_index}",
                        ):
                            st.session_state[pv_ready_key] = True
                            st.rerun()
                with btn2:
                    # Every displayed result used to eagerly download its
                    # full PDF just to have the bytes ready for this
                    # button -- for up to 8 results on every single
                    # search, whether or not anyone actually wanted to
                    # download them. That was the main driver of the
                    # memory blowup. Now the bytes are only fetched once
                    # someone actually asks to download that specific
                    # file.
                    dl_ready_key = f"dlready_{hit['doc_id']}_{hit['page_number']}_{result_index}"
                    if st.session_state.get(dl_ready_key):
                        try:
                            pdf_bytes = _download_bytes(store, hit["doc_id"])
                        except Exception as e:
                            # A Drive API call that still fails after its
                            # built-in retries shouldn't take down the
                            # whole page for every result -- show it
                            # inline and let the person try again.
                            st.error(f"Couldn't fetch this file: {e}")
                        else:
                            st.download_button(
                                "⬇️ Save PDF",
                                data=pdf_bytes,
                                file_name=hit["filename"],
                                mime="application/pdf",
                                # result_index guards against any future
                                # duplicate (doc_id, page_number) pair
                                # still producing a clash.
                                key=f"dl_{hit['doc_id']}_{hit['page_number']}_{result_index}",
                            )
                    else:
                        if st.button(
                            "⬇️ Download PDF",
                            key=f"dlprep_{hit['doc_id']}_{hit['page_number']}_{result_index}",
                        ):
                            st.session_state[dl_ready_key] = True
                            st.rerun()
 
                if st.session_state.get(pv_ready_key):
                    # Tracks which page is currently shown in *this*
                    # preview, separate from hit['page_number'] (the
                    # page that actually matched the search) -- so
                    # Previous/Next can move around without losing
                    # track of where the search result itself pointed.
                    pv_page_key = f"pvpage_{hit['doc_id']}_{hit['page_number']}_{result_index}"
                    if pv_page_key not in st.session_state:
                        st.session_state[pv_page_key] = hit["page_number"]
                    current_page = st.session_state[pv_page_key]
                    try:
                        total_pages = _page_count(store, hit["doc_id"])
                        page_img = _render_page_image(
                            store, hit["doc_id"], current_page
                        )
                    except Exception as e:
                        st.error(f"Couldn't load preview: {e}")
                    else:
                        nav1, nav2, nav3 = st.columns([1, 2, 1])
                        with nav1:
                            if st.button(
                                "◀ Previous page",
                                key=f"pvprev_{hit['doc_id']}_{hit['page_number']}_{result_index}",
                                disabled=current_page <= 1,
                            ):
                                st.session_state[pv_page_key] = current_page - 1
                                st.rerun()
                        with nav2:
                            st.markdown(
                                "<div style=\"text-align:center;padding-top:8px;"
                                "font-family:'IBM Plex Mono',monospace;font-size:13px;"
                                "color:#8B98A5;\">"
                                f"P.{current_page} / {total_pages}</div>",
                                unsafe_allow_html=True,
                            )
                        with nav3:
                            if st.button(
                                "Next page ▶",
                                key=f"pvnext_{hit['doc_id']}_{hit['page_number']}_{result_index}",
                                disabled=current_page >= total_pages,
                            ):
                                st.session_state[pv_page_key] = current_page + 1
                                st.rerun()
                        st.image(page_img, use_container_width=True)
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
                try:
                    file_id = store.upload_bytes(uploaded.name, data)
                    chunks = chunk_pages(pages)
                    index.add_document(file_id, uploaded.name, chunks)
                except Exception as e:
                    # Same reasoning as the delete button below: don't
                    # let a transient Drive API hiccup crash the whole
                    # app for everyone -- show it and let them retry.
                    st.error(
                        f"Upload failed: {e}. This is usually a brief "
                        "network hiccup talking to Google Drive -- try again."
                    )
                else:
                    st.session_state.last_uploaded_signature = signature
                    st.session_state.uploader_key += 1
                    st.success(f"Indexed {uploaded.name} ({len(pages)} pages).")
                    st.rerun()
 
    for d in docs:
        row1, row2 = st.columns([5, 1])
        row1.write(d["filename"])
        if row2.button("Delete", key=f"del_{d['doc_id']}"):
            try:
                index.remove_document(d["doc_id"])
                store.delete_file(d["doc_id"])
            except Exception as e:
                st.error(
                    f"Couldn't delete {d['filename']}: {e}. This is usually "
                    "a brief network hiccup talking to Google Drive -- try again."
                )
            else:
                st.rerun()
 
