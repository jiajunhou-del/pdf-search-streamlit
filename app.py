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
from collections import Counter
 
import streamlit as st
 
from drive_store import get_store
from pdf_processor import extract_pages, chunk_pages
from search_engine import SearchIndex
import user_data
 
st.set_page_config(page_title="Technical Manual Search", page_icon="🔧", layout="wide")
 
# Fixed set of manual categories. Each maps to an icon + accent color used
# for the "Popular search categories" cards on the Search home view, and
# doubles as the choice list offered at upload time and in the category
# filter dropdown -- one single source of truth for all three.
CATEGORY_META = {
    "Service Manual": {"icon": "🔧", "bg": "#EFF6FF", "fg": "#2563EB", "sub": "Service & maintenance"},
    "Parts Book": {"icon": "⚙️", "bg": "#ECFDF5", "fg": "#059669", "sub": "Parts & components"},
    "Specifications": {"icon": "📄", "bg": "#F5F3FF", "fg": "#7C3AED", "sub": "Specs & performance"},
    "Error Code": {"icon": "⚠️", "bg": "#FFF7ED", "fg": "#EA580C", "sub": "Troubleshooting"},
    "Procedure": {"icon": "📖", "bg": "#FDF2F8", "fg": "#DB2777", "sub": "Operating procedures"},
    "Installation": {"icon": "🔗", "bg": "#ECFEFF", "fg": "#0891B2", "sub": "Setup & connection"},
}
CATEGORY_NAMES = list(CATEGORY_META.keys())
 
# ---------------------------------------------------------------------------
# Look and feel. The color palette itself lives in .streamlit/config.toml
# (Streamlit's own [theme] section -- the supported way to theme buttons,
# inputs, etc. so it keeps working across Streamlit upgrades). This block
# only adds the handful of things that theming alone can't do: a nicer
# font, polish on elements this file builds directly, and the sidebar
# navigation's "selected item" look.
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', -apple-system, sans-serif; }
 
    .stButton > button, .stDownloadButton > button {
        border-radius: 8px;
        font-weight: 500;
        transition: transform 0.05s ease-in-out;
    }
    .stButton > button:active, .stDownloadButton > button:active {
        transform: scale(0.98);
    }
    .stTextInput input, div[data-baseweb="select"] > div {
        border-radius: 8px !important;
    }
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 12px !important;
    }
 
    /* Sidebar navigation: plain st.button per item, restyled to read as a
       left-aligned nav list rather than a row of centered buttons -- the
       "selected" item uses Streamlit's own primary-button styling (see
       type="primary" below) instead of hand-rolled active-state CSS, so
       it keeps working if Streamlit's internals change. */
    [data-testid="stSidebar"] .stButton > button {
        text-align: left;
        justify-content: flex-start;
        border: none;
        font-weight: 500;
        padding: 8px 14px;
    }
    [data-testid="stSidebar"] .stButton > button[kind="secondary"] {
        background: transparent;
        color: #374151;
    }
    [data-testid="stSidebar"] .stButton > button[kind="secondary"]:hover {
        background: #F3F4F6;
        color: #111827;
    }
    [data-testid="stSidebar"] .stButton > button[kind="primary"] {
        background: #EFF6FF;
        color: #2563EB;
        font-weight: 600;
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
viewer_email = user_data.current_user_email(store.mode)
 
 
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
 
 
def _category_badge_html(category: str) -> str:
    meta = CATEGORY_META.get(category)
    if not meta:
        return (
            "<span style='background:#F3F4F6;color:#4B5563;padding:2px 10px;"
            "border-radius:999px;font-size:12px;font-weight:600;'>Uncategorized</span>"
        )
    return (
        f"<span style='background:{meta['bg']};color:{meta['fg']};padding:2px 10px;"
        f"border-radius:999px;font-size:12px;font-weight:600;'>{meta['icon']} {html.escape(category)}</span>"
    )
 
 
def _favorite_toggle(doc_id: str, filename: str, page_number: int, widget_id: str):
    """Renders a star button that adds/removes this page from the
    signed-in viewer's Favorites. Without a signed-in viewer (no Community
    Cloud email restriction active) there's no identity to save against,
    so this shows a disabled hint instead of a button that would silently
    do nothing."""
    if not viewer_email:
        st.caption("☆ Sign in to save favorites")
        return
    is_fav = user_data.is_favorite(store, viewer_email, doc_id, page_number)
    label = "★ Favorited" if is_fav else "☆ Add to favorites"
    if st.button(label, key=f"fav_{widget_id}"):
        user_data.toggle_favorite(store, viewer_email, doc_id, filename, page_number)
        st.rerun()
 
 
def _preview_download_controls(doc_id: str, filename: str, page_number: int, widget_id: str):
    """The lazy 'fetch only once asked' Preview / Download control pair,
    shared by search results and the Favorites view. Two-step pattern:
    a button first sets a session_state flag + reruns; only on that next
    rerun does the actual Drive fetch happen and the real widget appear --
    this is what keeps every displayed card from eagerly downloading its
    full PDF on every search (the original cause of the memory crash)."""
    btn1, btn2 = st.columns([1, 1])
    pv_ready_key = f"pvready_{widget_id}"
    dl_ready_key = f"dlready_{widget_id}"
    with btn1:
        # Google Drive's own web preview has an undocumented file-size
        # ceiling and refuses to render bigger manuals -- a Drive
        # limitation that can't be worked around. Rendering the page
        # ourselves with PyMuPDF sidesteps it entirely.
        if not st.session_state.get(pv_ready_key):
            if st.button("👀 Preview this page", key=f"pvprep_{widget_id}"):
                st.session_state[pv_ready_key] = True
                st.rerun()
    with btn2:
        if st.session_state.get(dl_ready_key):
            try:
                pdf_bytes = _download_bytes(store, doc_id)
            except Exception as e:
                st.error(f"Couldn't fetch this file: {e}")
            else:
                st.download_button(
                    "⬇️ Save PDF",
                    data=pdf_bytes,
                    file_name=filename,
                    mime="application/pdf",
                    key=f"dl_{widget_id}",
                )
        else:
            if st.button("⬇️ Download PDF", key=f"dlprep_{widget_id}"):
                st.session_state[dl_ready_key] = True
                st.rerun()
 
    if st.session_state.get(pv_ready_key):
        # Tracks which page is currently shown in *this* preview,
        # separate from the page that actually matched the search, so
        # Previous/Next can move around without losing track of it.
        pv_page_key = f"pvpage_{widget_id}"
        if pv_page_key not in st.session_state:
            st.session_state[pv_page_key] = page_number
        current_page = st.session_state[pv_page_key]
        try:
            total_pages = _page_count(store, doc_id)
            page_img = _render_page_image(store, doc_id, current_page)
        except Exception as e:
            st.error(f"Couldn't load preview: {e}")
        else:
            nav1, nav2, nav3 = st.columns([1, 2, 1])
            with nav1:
                if st.button("◀ Previous page", key=f"pvprev_{widget_id}", disabled=current_page <= 1):
                    st.session_state[pv_page_key] = current_page - 1
                    st.rerun()
            with nav2:
                st.markdown(
                    f"<div style='text-align:center;padding-top:6px;'>Page {current_page} / {total_pages}</div>",
                    unsafe_allow_html=True,
                )
            with nav3:
                if st.button("Next page ▶", key=f"pvnext_{widget_id}", disabled=current_page >= total_pages):
                    st.session_state[pv_page_key] = current_page + 1
                    st.rerun()
            st.image(page_img, use_container_width=True)
 
 
def _goto_search(prefill_query: str = None, prefill_category: str = None):
    """Jumps to the Search view with the query box and/or category filter
    pre-set -- used by Recent Searches, Favorites and category cards. Both
    widgets use a dynamic key (search_widget_key / category_widget_key)
    that's bumped here, forcing Streamlit to treat them as brand-new
    widget instances on the next render so the new default actually takes
    -- otherwise Streamlit would keep whatever the widget's own
    session_state already held from before, ignoring the new `value=`."""
    st.session_state.nav = "search"
    if prefill_query is not None:
        st.session_state["prefill_query"] = prefill_query
        st.session_state.search_widget_key = st.session_state.get("search_widget_key", 0) + 1
    if prefill_category is not None:
        st.session_state["prefill_category"] = prefill_category
        st.session_state.category_widget_key = st.session_state.get("category_widget_key", 0) + 1
    st.rerun()
 
 
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
<div style="font-family:'Inter',-apple-system,Segoe UI,Roboto,sans-serif;">
  <button id="micBtn" style="padding:9px 18px;border-radius:999px;border:none;
    background:#2563EB;color:white;font-size:14px;font-weight:500;cursor:pointer;
    box-shadow:0 1px 3px rgba(37,99,235,0.4);">
    🎤 Speak your query
  </button>
  <div id="voiceStatus" style="margin-top:8px;font-size:13px;color:#6b7280;"></div>
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
# Sidebar navigation
# ---------------------------------------------------------------------------
if "nav" not in st.session_state:
    st.session_state.nav = "search"
 
with st.sidebar:
    st.markdown(
        """
        <div style="display:flex;align-items:center;gap:10px;margin:4px 0 18px 0;">
          <div style="width:36px;height:36px;border-radius:10px;background:#EFF6FF;
            display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0;">
            🔧
          </div>
          <div style="font-size:16px;font-weight:700;color:#1B2440;line-height:1.2;">
            Technical Manual<br/>Search
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    nav_items = [
        ("search", "🔍  Search"),
        ("history", "🕐  My History"),
        ("favorites", "☆  Favorites"),
        ("about", "ℹ️  About"),
    ]
    for key, label in nav_items:
        active = st.session_state.nav == key
        if st.button(label, key=f"nav_{key}", type="primary" if active else "secondary", use_container_width=True):
            st.session_state.nav = key
            st.rerun()
 
docs = index.list_documents()
doc_options = {"All documents": None}
for d in docs:
    doc_options[d["filename"]] = d["doc_id"]
 
# ---------------------------------------------------------------------------
# Top header (branded bar within the main content area)
# ---------------------------------------------------------------------------
storage_ok = store.mode == "drive"
if viewer_email:
    initials = "".join(p[0] for p in re.split(r"[.\-_@]", viewer_email) if p)[:2].upper()
    user_badge = f"<div style='width:34px;height:34px;border-radius:50%;background:rgba(255,255,255,0.15);display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;'>{initials}</div>"
else:
    user_badge = "<div style='font-size:13px;opacity:0.8;'>Guest</div>"
 
header_col1, header_col2 = st.columns([5, 1])
with header_col1:
    st.markdown(
        f"""
        <div style="background:#0F1E3D;border-radius:14px;padding:18px 24px;
          display:flex;align-items:center;justify-content:space-between;margin-bottom:18px;">
          <div style="display:flex;align-items:center;gap:14px;">
            <div style="width:42px;height:42px;border-radius:10px;background:rgba(255,255,255,0.1);
              display:flex;align-items:center;justify-content:center;font-size:22px;flex-shrink:0;">
              🔧
            </div>
            <div>
              <div style="font-size:20px;font-weight:700;color:#FFFFFF;line-height:1.2;">
                Technical Manual Search
              </div>
              <div style="font-size:13px;color:#9CA9C7;">
                Find the right document. Faster.
              </div>
            </div>
          </div>
          <div style="display:flex;align-items:center;gap:16px;">
            <div style="display:inline-flex;align-items:center;gap:6px;padding:3px 10px;
              border-radius:999px;font-size:11px;font-weight:600;
              background:{'rgba(16,185,129,0.15)' if storage_ok else 'rgba(249,115,22,0.15)'};
              color:{'#34D399' if storage_ok else '#FB923C'};">
              <span style="width:6px;height:6px;border-radius:50%;
                background:{'#10B981' if storage_ok else '#F97316'};"></span>
              {'Google Drive' if storage_ok else 'local (dev mode)'}
            </div>
            {user_badge}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with header_col2:
    st.write("")
    if st.button("❓ Help", key="help_btn", use_container_width=True):
        st.session_state.nav = "about"
        st.rerun()
 
# ---------------------------------------------------------------------------
# Route to the selected view
# ---------------------------------------------------------------------------
view = st.session_state.nav
 
if view == "search":
    st.markdown(
        """
        <div style="background:#EFF6FF;border-radius:16px;padding:28px 32px;margin-bottom:18px;">
          <div style="font-size:26px;font-weight:700;color:#1B2440;line-height:1.25;">
            Search the shared manual library
          </div>
          <div style="font-size:14px;color:#475569;margin-top:4px;">
            Search across every service manual in the library, by keyword or by voice.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
 
    voice_query = st.query_params.get("voice_query", "")
    if voice_query:
        del st.query_params["voice_query"]
 
    if "search_widget_key" not in st.session_state:
        st.session_state.search_widget_key = 0
    if "category_widget_key" not in st.session_state:
        st.session_state.category_widget_key = 0
 
    if "prefill_query" in st.session_state:
        initial_query = st.session_state.pop("prefill_query")
    else:
        initial_query = voice_query
 
    category_options = ["All categories"] + CATEGORY_NAMES
    if "prefill_category" in st.session_state:
        initial_category = st.session_state.pop("prefill_category")
    else:
        initial_category = "All categories"
    default_cat_index = (
        category_options.index(initial_category) if initial_category in category_options else 0
    )
 
    col_q, col_doc, col_cat, col_btn = st.columns([3, 1, 1, 1])
    with col_q:
        query = st.text_input(
            "Search",
            value=initial_query,
            placeholder="e.g. overcurrent fault on PCB, HMI calibration, inverter parameter…",
            label_visibility="collapsed",
            key=f"search_box_{st.session_state.search_widget_key}",
        )
    with col_doc:
        scope_label = st.selectbox(
            "Search within", list(doc_options.keys()), label_visibility="collapsed"
        )
    with col_cat:
        category_label = st.selectbox(
            "Category",
            category_options,
            index=default_cat_index,
            label_visibility="collapsed",
            key=f"category_select_{st.session_state.category_widget_key}",
        )
    with col_btn:
        st.button("🔍 Search", use_container_width=True)
 
    st.components.v1.html(_VOICE_HTML, height=70)
 
    # -----------------------------------------------------------------
    # Popular search categories
    # -----------------------------------------------------------------
    st.markdown("<div style='font-size:16px;font-weight:700;color:#1B2440;margin:20px 0 10px 0;'>🔥 Popular search categories</div>", unsafe_allow_html=True)
    category_counts = Counter(d.get("category", "Uncategorized") for d in docs)
    cat_rows = [CATEGORY_NAMES[i:i + 3] for i in range(0, len(CATEGORY_NAMES), 3)]
    for row in cat_rows:
        cols = st.columns(3)
        for col, cat_name in zip(cols, row):
            meta = CATEGORY_META[cat_name]
            count = category_counts.get(cat_name, 0)
            with col:
                with st.container(border=True):
                    st.markdown(
                        f"""
                        <div style="display:flex;align-items:center;gap:10px;">
                          <div style="width:38px;height:38px;border-radius:10px;background:{meta['bg']};
                            display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0;">
                            {meta['icon']}
                          </div>
                          <div>
                            <div style="font-size:14px;font-weight:600;color:#1B2440;">{html.escape(cat_name)}</div>
                            <div style="font-size:12px;color:#6b7280;">{meta['sub']} · {count} doc{'s' if count != 1 else ''}</div>
                          </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    if st.button("Browse →", key=f"catcard_{cat_name}", use_container_width=True):
                        _goto_search(prefill_category=cat_name)
 
    st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)
 
    # -----------------------------------------------------------------
    # Results / Recent Searches + Tips
    # -----------------------------------------------------------------
    if query.strip():
        try:
            hits = index.search(
                query, top_k=8, doc_id=doc_options[scope_label], category=category_label
            )
        except Exception as e:
            st.error(
                f"Search failed: {e}. If this keeps happening, use the app's "
                "'Reboot app' option (or ask whoever manages it to) to clear "
                "the search index and rebuild it fresh."
            )
            hits = []
 
        if viewer_email and query.strip() != st.session_state.get("last_logged_query"):
            st.session_state.last_logged_query = query.strip()
            user_data.add_history_entry(store, viewer_email, query.strip(), len(hits))
 
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
                        badge_bg, badge_fg = "#ECFDF5", "#047857"
                    elif score_pct >= 40:
                        badge_bg, badge_fg = "#FFFBEB", "#B45309"
                    else:
                        badge_bg, badge_fg = "#F3F4F6", "#4B5563"
                    st.markdown(
                        f"<div style='font-size:15px;'>"
                        f"<span style='font-weight:600;'>{html.escape(hit['filename'])}</span>"
                        f" — page {hit['page_number']}"
                        f"&nbsp;&nbsp;"
                        f"<span style='background:{badge_bg};color:{badge_fg};"
                        f"padding:2px 10px;border-radius:999px;font-size:12px;"
                        f"font-weight:600;'>{score_pct}% match</span>"
                        f"&nbsp;&nbsp;{_category_badge_html(hit.get('category', 'Uncategorized'))}"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    st.markdown(_highlight(hit["snippet"], hit["highlight_terms"]))
                    widget_id = f"{hit['doc_id']}_{hit['page_number']}_{result_index}"
                    _preview_download_controls(hit["doc_id"], hit["filename"], hit["page_number"], widget_id)
                    _favorite_toggle(hit["doc_id"], hit["filename"], hit["page_number"], widget_id)
    else:
        browse_category = category_label if category_label != "All categories" else None
        if browse_category:
            st.markdown(
                f"<div style='font-size:15px;font-weight:600;color:#1B2440;margin:8px 0;'>"
                f"📁 Documents tagged '{html.escape(browse_category)}'</div>",
                unsafe_allow_html=True,
            )
            matching_docs = [d for d in docs if d.get("category", "Uncategorized") == browse_category]
            if not matching_docs:
                st.caption("No documents tagged with this category yet.")
            else:
                for d in matching_docs:
                    st.write(f"📄 {d['filename']}")
            st.caption("Type a query above to search within this category.")
        else:
            col_recent, col_tips = st.columns([2, 1])
            with col_recent:
                st.markdown("<div style='font-size:15px;font-weight:700;color:#1B2440;margin-bottom:6px;'>Recent Searches</div>", unsafe_allow_html=True)
                if not viewer_email:
                    st.caption("Sign in (open the app's restricted link) to keep a history of your searches.")
                else:
                    recent = user_data.get_history(store, viewer_email, limit=6)
                    if not recent:
                        st.caption("No searches yet -- try typing something above.")
                    for entry in recent:
                        with st.container(border=True):
                            rc1, rc2 = st.columns([4, 1])
                            with rc1:
                                st.markdown(
                                    f"<div style='font-size:14px;font-weight:600;color:#1B2440;'>{html.escape(entry['query'])}</div>"
                                    f"<div style='font-size:12px;color:#6b7280;'>{entry['result_count']} result(s) · {user_data.humanize_ago(entry['ts'])}</div>",
                                    unsafe_allow_html=True,
                                )
                            with rc2:
                                if st.button("Search again", key=f"rerun_{entry['ts']}"):
                                    _goto_search(prefill_query=entry["query"])
            with col_tips:
                st.markdown(
                    """
                    <div style="background:#FFFBEB;border-radius:12px;padding:16px 18px;">
                      <div style="font-size:14px;font-weight:700;color:#92400E;margin-bottom:8px;">💡 Search Tips</div>
                      <div style="font-size:13px;color:#78350F;line-height:1.7;">
                        1. Use specific error codes or part numbers for the tightest matches.<br/>
                        2. Narrow to one document with the "Search within" dropdown.<br/>
                        3. Tap 🎤 and speak your query instead of typing.<br/>
                        4. Browse a category card above to see what's tagged there.<br/>
                        5. Star a result to save it under Favorites for later.
                      </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
 
    # -----------------------------------------------------------------
    # Library management
    # -----------------------------------------------------------------
    with st.expander(f"📚 Document library ({len(docs)} files)"):
        if "uploader_key" not in st.session_state:
            st.session_state.uploader_key = 0
        if "last_uploaded_signature" not in st.session_state:
            st.session_state.last_uploaded_signature = None
 
        upload_col, category_col = st.columns([3, 1])
        with upload_col:
            uploaded = st.file_uploader(
                "Add a PDF to the shared library",
                type=["pdf"],
                key=f"uploader_{st.session_state.uploader_key}",
            )
        with category_col:
            upload_category = st.selectbox("Category", CATEGORY_NAMES, key="upload_category")
 
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
                        index.add_document(file_id, uploaded.name, chunks, category=upload_category)
                    except Exception as e:
                        st.error(
                            f"Upload failed: {e}. This is usually a brief "
                            "network hiccup talking to Google Drive -- try again."
                        )
                    else:
                        st.session_state.last_uploaded_signature = signature
                        st.session_state.uploader_key += 1
                        st.success(f"Indexed {uploaded.name} ({len(pages)} pages, tagged '{upload_category}').")
                        st.rerun()
 
        for d in docs:
            row1, row2, row3 = st.columns([3, 2, 1])
            row1.write(d["filename"])
            with row2:
                doc_category = d.get("category", "Uncategorized")
                current_cat = doc_category if doc_category in CATEGORY_NAMES else CATEGORY_NAMES[0]
                new_cat = st.selectbox(
                    "Category",
                    CATEGORY_NAMES,
                    index=CATEGORY_NAMES.index(current_cat),
                    key=f"cat_{d['doc_id']}",
                    label_visibility="collapsed",
                )
                if new_cat != doc_category:
                    try:
                        index.set_category(d["doc_id"], new_cat)
                    except Exception as e:
                        st.error(f"Couldn't update category: {e}")
                    else:
                        st.rerun()
            if row3.button("Delete", key=f"del_{d['doc_id']}"):
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
 
elif view == "history":
    st.markdown("<div style='font-size:22px;font-weight:700;color:#1B2440;margin-bottom:12px;'>🕐 My History</div>", unsafe_allow_html=True)
    if not viewer_email:
        st.info(
            "History is saved per signed-in person. Open the app through the restricted "
            "link and sign in with your Google account to start building your history."
        )
    else:
        history = user_data.get_history(store, viewer_email)
        if not history:
            st.caption("No searches yet -- head to Search and look something up.")
        for entry in history:
            with st.container(border=True):
                hc1, hc2 = st.columns([4, 1])
                with hc1:
                    st.markdown(
                        f"<div style='font-size:15px;font-weight:600;color:#1B2440;'>{html.escape(entry['query'])}</div>"
                        f"<div style='font-size:12px;color:#6b7280;'>{entry['result_count']} result(s) · {user_data.humanize_ago(entry['ts'])}</div>",
                        unsafe_allow_html=True,
                    )
                with hc2:
                    if st.button("Search again", key=f"hist_{entry['ts']}"):
                        _goto_search(prefill_query=entry["query"])
 
elif view == "favorites":
    st.markdown("<div style='font-size:22px;font-weight:700;color:#1B2440;margin-bottom:12px;'>☆ Favorites</div>", unsafe_allow_html=True)
    if not viewer_email:
        st.info(
            "Favorites are saved per signed-in person. Open the app through the restricted "
            "link and sign in with your Google account to start saving pages."
        )
    else:
        favorites = user_data.get_favorites(store, viewer_email)
        if not favorites:
            st.caption("No favorites yet -- star a result from Search to save it here.")
        for fav in favorites:
            with st.container(border=True):
                fc1, fc2 = st.columns([1, 5])
                with fc1:
                    try:
                        thumb = _render_thumbnail(store, fav["doc_id"], fav["page_number"])
                        st.image(thumb, width=100)
                    except Exception:
                        st.write("📄")
                with fc2:
                    st.markdown(
                        f"<div style='font-size:15px;font-weight:600;'>{html.escape(fav['filename'])}</div>"
                        f"<div style='font-size:12px;color:#6b7280;'>page {fav['page_number']}</div>",
                        unsafe_allow_html=True,
                    )
                    widget_id = f"fav_{fav['doc_id']}_{fav['page_number']}"
                    _preview_download_controls(fav["doc_id"], fav["filename"], fav["page_number"], widget_id)
                    if st.button("★ Remove from favorites", key=f"unfav_{widget_id}"):
                        user_data.toggle_favorite(store, viewer_email, fav["doc_id"], fav["filename"], fav["page_number"])
                        st.rerun()
 
elif view == "about":
    st.markdown("<div style='font-size:22px;font-weight:700;color:#1B2440;margin-bottom:12px;'>ℹ️ About</div>", unsafe_allow_html=True)
    with st.container(border=True):
        st.markdown(
            """
            **Technical Manual Search** indexes every PDF service manual in the shared
            Google Drive library so anyone on the team can find the right page by
            keyword or by voice, instead of opening manuals one at a time.
 
            **Categories** — each uploaded manual is tagged as one of: """
            + ", ".join(f"{m['icon']} {name}" for name, m in CATEGORY_META.items())
            + """. Use the category cards or the Category filter on the Search page to
            browse by type.
 
            **My History / Favorites** — signed-in colleagues get a private, per-person
            search history and a list of starred pages, saved alongside the shared
            library. These need the app to be opened through its access-restricted
            link (Google sign-in); outside of that there's no identity to save them
            under.
 
            **Adding or removing documents** — anyone with access to this app can
            upload or delete PDFs from the Document library (bottom of the Search
            page); uploads go into the same shared Drive folder for everyone.
 
            **Access** — this app is restricted to specific Horizon email addresses.
            To be added, or if something looks broken, contact
            technical.support@horizon.co.jp.
            """
        )
 
