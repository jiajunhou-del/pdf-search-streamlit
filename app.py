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
    # "bg" is a soft, muted tint used both for small inline badges (search
    # result cards, About page) and as the full-card fill on the "Popular
    # search categories" cards -- tried a noticeably deeper/brighter fill
    # there first, but it read as too loud, so this is the gentler version.
    "Service Manual": {"icon": "🔧", "bg": "#EEF3FC", "fg": "#3457A6", "sub": "Service & maintenance"},
    "Parts Book": {"icon": "⚙️", "bg": "#EBF7EF", "fg": "#2E7D4F", "sub": "Parts & components"},
    "Specifications": {"icon": "📄", "bg": "#F3F0FB", "fg": "#6647A8", "sub": "Specs & performance"},
    "Error Code": {"icon": "⚠️", "bg": "#FCF1E7", "fg": "#B45F1E", "sub": "Troubleshooting"},
    "Procedure": {"icon": "📖", "bg": "#FBEEF4", "fg": "#A83E71", "sub": "Operating procedures"},
    "Installation": {"icon": "🔗", "bg": "#EAF7F8", "fg": "#1F7A8C", "sub": "Setup & connection"},
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
 
    /* Category cards ("Popular search categories"): the colored look comes
       from a markdown block rendered above a button, inside each card's
       own st.container(key=f"catcard_{name}") -- that key gives
       Streamlit's wrapper div a stable "st-key-catcard_<name>" class to
       target here. The real st.button (key=f"catbtn_{name}", a
       deliberately different prefix so its own "st-key-catbtn_<name>"
       class can be targeted without also matching the outer card by
       substring) is stretched over the whole card and made invisible, so
       clicking anywhere on the card -- not just a visible "Browse" label
       -- triggers it. (Recent Searches / My History rows used to share
       this same trick, but went back to a plain bordered container with
       a normal, visible "Search again" button per feedback.) */
    div[class*="st-key-catcard_"] {
        position: relative;
        border: none !important;
        padding: 0 !important;
        margin-bottom: 14px;
        transition: transform 0.05s ease-in-out;
    }
    div[class*="st-key-catcard_"]:has(button:active) {
        transform: scale(0.99);
    }
    div[class*="st-key-catbtn_"] {
        position: absolute;
        inset: 0;
    }
    div[class*="st-key-catbtn_"] .stButton {
        height: 100%;
    }
    div[class*="st-key-catbtn_"] .stButton > button {
        width: 100%;
        height: 100%;
        min-height: 0;
        opacity: 0;
        cursor: pointer;
        margin: 0;
        padding: 0;
        border: none;
    }
 
    /* Sidebar: switched from plain white to a dark "instrument panel" look
       -- a white sidebar on a near-white main background (per feedback)
       didn't read as a separate area at all. Dark navy + a blue accent
       (the same blue as primaryColor in config.toml) gives a clear visual
       boundary and a more "technical tool" feel. Re-declared *after* the
       light-theme sidebar button rules above so these win on the cascade
       without needing !important everywhere. */
    [data-testid="stSidebar"] {
        background-color: #0B1220;
        border-right: 1px solid #1E293B;
    }
    [data-testid="stSidebar"] * {
        color: #CBD5E1;
    }
    [data-testid="stSidebar"] svg {
        fill: #64748B;
    }
    [data-testid="stSidebar"] .stButton > button[kind="secondary"] {
        background: transparent;
        color: #CBD5E1;
    }
    [data-testid="stSidebar"] .stButton > button[kind="secondary"]:hover {
        background: #16213A;
        color: #F8FAFC;
    }
    [data-testid="stSidebar"] .stButton > button[kind="primary"] {
        background: #16294D;
        color: #7FB3FF;
        font-weight: 600;
        box-shadow: inset 3px 0 0 #3B82F6;
    }
    [data-testid="stSidebar"] .stTextInput input {
        background-color: #131F35;
        color: #F1F5F9;
        border: 1px solid #263449;
    }
    [data-testid="stSidebar"] .stTextInput input::placeholder {
        color: #5B6B84;
    }
    [data-testid="stSidebar"] .stTextInput input:focus {
        border-color: #3B82F6;
        box-shadow: 0 0 0 1px #3B82F6;
    }
    [data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
        color: #8FA8C9 !important;
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
# viewer_email is resolved once the sidebar (which asks "who are you?") has
# rendered -- see below. Declared here only so functions defined above that
# reference it as a module-level name (Python looks up globals at call
# time, not at def time) don't error before that point.
viewer_email = None
 
 
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
 
 
def _scope_badge_html(scope_filename: str) -> str:
    """A plain blue pill for the document a search was narrowed to (the
    "Search within" dropdown), shown next to the category badge on a
    Recent Searches / History row -- mirrors _category_badge_html's look
    but in a neutral blue since it isn't tied to a category color."""
    name = scope_filename.rsplit(".", 1)[0]  # drop the .pdf extension
    if len(name) > 22:
        name = name[:21] + "…"
    return (
        f"<span style='background:#EFF6FF;color:#2563EB;padding:2px 10px;"
        f"border-radius:999px;font-size:12px;font-weight:600;'>{html.escape(name)}</span>"
    )
 
 
def _render_history_row(entry: dict, key_prefix: str, index: int):
    """One card for a past search -- used by both the Search page's
    "Recent Searches" panel and the full "My History" list, so the two
    stay visually consistent.
 
    Every piece of HTML built here is assembled as a *single-line* string
    (no multi-line triple-quoted blocks). That's not just style: when the
    query has no category and wasn't scoped to a document, `badges` is an
    empty string, and putting that on its own line inside a multi-line
    f-string left a whitespace-only line in the middle of the HTML --
    which Streamlit's markdown renderer (like most Markdown parsers)
    treats as a paragraph break, splitting one HTML block into two and
    printing everything after the break as literal escaped text instead
    of rendering it. Keeping each chunk on one line sidesteps that
    entirely, blank interpolated values or not.
    """
    badges = ""
    category = entry.get("category")
    if category:
        badges += _category_badge_html(category)
    scope_filename = entry.get("scope_filename")
    if scope_filename:
        badges += _scope_badge_html(scope_filename)
    result_count = entry.get("result_count", 0)
    doc_label = f"{result_count} document{'s' if result_count != 1 else ''}"
    with st.container(border=True):
        col_text, col_btn = st.columns([5, 1.3])
        with col_text:
            st.markdown(
                f"<div style='display:flex;align-items:center;gap:8px;flex-wrap:wrap;'>"
                f"<span style='font-size:15px;color:#2563EB;'>🔍</span>"
                f"<span style='font-size:14px;font-weight:600;color:#1B2440;'>{html.escape(entry['query'])}</span>"
                f"{badges}</div>"
                f"<div style='font-size:12px;color:#6b7280;margin-top:4px;'>{doc_label} · {user_data.humanize_ago(entry['ts'])}</div>",
                unsafe_allow_html=True,
            )
        with col_btn:
            if st.button("Search again", key=f"{key_prefix}_{index}", use_container_width=True):
                _goto_search(prefill_query=entry["query"])
 
 
def _favorite_toggle(doc_id: str, filename: str, page_number: int, widget_id: str):
    """Renders a star button that adds/removes this page from the current
    viewer's Favorites. Without a name entered in the sidebar there's no
    identity to save against, so this shows a hint instead of a button
    that would silently do nothing."""
    if not viewer_email:
        st.caption("☆ Enter your name (sidebar) to save favorites")
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
# the same browser Web Speech API used in the original prototype. This needs
# a real HTTPS origin to access the microphone at all -- which is exactly
# what Streamlit Community Cloud provides automatically, no certificate
# hassle required.
#
# Getting the recognized text from inside this iframe into the actual
# search box took three tries:
#   1. Navigate the top page directly (`window.parent.location.href = ...`).
#      Confirmed in testing that Chrome blocks this outright -- the iframe
#      components.v1.html() creates has no "allow-top-navigation" sandbox
#      permission, so the mic's status line would show the recognized text,
#      but the page never actually reloaded and the search box stayed empty.
#   2. Post the text to a small listener injected into the top page (so
#      *it* does the navigation instead, unsandboxed). Also dead on
#      arrival: Streamlit's HTML renderer runs inline event-handler
#      attributes like onerror="..." through React's prop validation,
#      which rejects a string handler and throws before it ever runs.
#   3. Report the text back as a real Streamlit *component value*, over
#      the same protocol every custom component uses. This is the
#      textbook-correct approach, but empirically broken in this
#      Streamlit build -- even the well-established third-party
#      streamlit-js-eval package hits the exact same "Received component
#      message for unregistered ComponentInstance!" warning here, which
#      means Streamlit's own frontend isn't registering *any* custom
#      component's iframe right now, not just a homemade one.
#
# What actually works: this iframe's sandbox includes "allow-same-origin"
# alongside "allow-scripts" -- and combining those two specific flags is
# explicitly known to let a sandboxed srcdoc iframe access its parent
# document directly (Chrome even logs a warning about it: "can escape its
# sandboxing"). Top-*navigation* is still blocked regardless (that's a
# separate flag), but plain same-origin DOM access is not, so instead of
# navigating or messaging anything, the recognized text is written
# straight into the real search <input> in the parent page (via the
# native value setter + a synthetic "input" event, the standard trick for
# updating a React-controlled input from outside React), then the input
# is blurred -- which is what Streamlit's own text_input already commits
# a new value on, exactly as if a person had typed it and clicked away.
# ---------------------------------------------------------------------------
_VOICE_HTML = """
<style>
@keyframes micPulse {
  0%   { box-shadow: 0 0 0 0 rgba(220,38,38,0.45); }
  70%  { box-shadow: 0 0 0 10px rgba(220,38,38,0); }
  100% { box-shadow: 0 0 0 0 rgba(220,38,38,0); }
}
#micBtn.listening {
  background: #DC2626 !important;
  animation: micPulse 1.4s infinite;
}
</style>
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
 
// Finds the real search <input> in the parent page (see the big comment
// above) and fills it in the same way a person typing would, then blurs
// it so Streamlit commits the new value. Falls back to just leaving the
// text in this iframe's own status line (for manual copy/paste) if the
// parent DOM ever doesn't match what's expected here.
function fillParentSearchBox(text) {
  try {
    const doc = window.parent.document;
    const input = doc.querySelector('input[aria-label="Search"]')
      || doc.querySelector('input[placeholder*="overcurrent fault"]');
    if (!input) {
      statusEl.innerText = 'Could not find the search box automatically -- copy this: "' + text + '"';
      return false;
    }
    const nativeSetter = Object.getOwnPropertyDescriptor(
      window.parent.HTMLInputElement.prototype, 'value'
    ).set;
    nativeSetter.call(input, text);
    input.dispatchEvent(new window.parent.Event('input', { bubbles: true }));
    input.focus();
    input.blur();
    return true;
  } catch (err) {
    statusEl.innerText = 'Could not fill the search box automatically -- copy this: "' + text + '"';
    return false;
  }
}
 
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
if (!SR) {
  statusEl.innerText = 'Voice input needs Chrome/Edge (Web Speech API not available here).';
  btn.disabled = true;
} else {
  const recog = new SR();
  recog.lang = 'en-US';
  recog.interimResults = true;
  recog.maxAlternatives = 1;
  recog.onstart = () => {
    statusEl.innerText = '🎙️ Listening… speak now';
    btn.classList.add('listening');
  };
  recog.onerror = (e) => {
    const messages = {
      'no-speech': 'No speech detected -- try again.',
      'not-allowed': 'Microphone access blocked. Allow it in the browser site settings.',
      'service-not-allowed': 'Microphone access blocked. Allow it in the browser site settings.',
      'audio-capture': 'No microphone found.',
      'network': 'Network error during speech recognition.'
    };
    statusEl.innerText = messages[e.error] || ('Voice error: ' + e.error);
    btn.classList.remove('listening');
  };
  recog.onend = () => { btn.classList.remove('listening'); };
  recog.onresult = (e) => {
    let text = '';
    for (let i = 0; i < e.results.length; i++) { text += e.results[i][0].transcript; }
    if (e.results[e.results.length - 1].isFinal && text.trim()) {
      if (fillParentSearchBox(text.trim())) {
        statusEl.innerText = '✅ Searching for: "' + text.trim() + '"';
      }
    } else {
      statusEl.innerText = text;
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
 
# The "Your name" identity (see below) also lives in the page's own URL as
# ?viewer_name=..., so simply reloading this tab or reopening a bookmarked
# link that already has it restores it with zero clicks -- st.query_params
# is ordinary Streamlit state read/written directly by the main script, so
# unlike a trick routed through an embedded component's iframe, there's no
# browser sandbox permission involved here at all.
if "viewer_name" not in st.session_state:
    st.session_state.viewer_name = st.query_params.get("viewer_name", "")
 
with st.sidebar:
    st.markdown(
        """
        <div style="display:flex;align-items:center;gap:10px;margin:4px 0 18px 0;">
          <div style="width:36px;height:36px;border-radius:10px;background:rgba(59,130,246,0.15);
            border:1px solid rgba(59,130,246,0.35);
            display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0;">
            🔧
          </div>
          <div style="font-size:16px;font-weight:700;color:#F1F5F9;line-height:1.2;">
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
 
    # Simple, no-setup identity for My History / Favorites: Streamlit
    # Community Cloud's viewer-email allowlist (which restricts who can
    # open this app) stopped exposing the visitor's verified email to the
    # app itself as of Streamlit 1.42 -- st.user now requires a full
    # Google OAuth/OIDC login flow wired up separately, which is more
    # setup than this needs right now. Instead, each person just tells the
    # app their own name once, and that's what their History/Favorites are
    # saved under. It's not verified (nothing stops someone from typing a
    # colleague's name), but for a small trusted internal team that's a
    # reasonable trade for zero extra setup.
    st.markdown("<div style='margin-top:14px;font-size:11px;font-weight:600;color:#64A0E8;letter-spacing:0.05em;text-transform:uppercase;'>Your name</div>", unsafe_allow_html=True)
    name_input = st.text_input(
        "Your name",
        value=st.session_state.get("viewer_name", ""),
        placeholder="e.g. Jiajun Hou",
        label_visibility="collapsed",
        key="viewer_name_input",
        help="Used to keep your own search history and favorites separate from your colleagues'. Once set, it's part of this page's link -- bookmark it (or just keep reusing this tab) to skip retyping it next time.",
    )
    st.session_state.viewer_name = name_input.strip()
    if st.session_state.viewer_name:
        st.query_params["viewer_name"] = st.session_state.viewer_name
        st.caption(f"🔖 Bookmark this page to return as **{html.escape(st.session_state.viewer_name)}**.")
    elif "viewer_name" in st.query_params:
        del st.query_params["viewer_name"]
 
viewer_email = st.session_state.get("viewer_name") or None
 
docs = index.list_documents()
 
# ---------------------------------------------------------------------------
# Top header (branded bar within the main content area)
# ---------------------------------------------------------------------------
storage_ok = store.mode == "drive"
if viewer_email:
    initials = "".join(p[0] for p in re.split(r"[\s.\-_@]+", viewer_email) if p)[:2].upper()
    user_badge = f"<div title='{html.escape(viewer_email)}' style='width:34px;height:34px;border-radius:50%;background:rgba(255,255,255,0.15);display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;'>{initials}</div>"
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
 
    if "search_widget_key" not in st.session_state:
        st.session_state.search_widget_key = 0
    if "category_widget_key" not in st.session_state:
        st.session_state.category_widget_key = 0
 
    if "prefill_query" in st.session_state:
        initial_query = st.session_state.pop("prefill_query")
    else:
        initial_query = ""
 
    category_options = ["All categories"] + CATEGORY_NAMES
    if "prefill_category" in st.session_state:
        initial_category = st.session_state.pop("prefill_category")
    else:
        initial_category = "All categories"
    default_cat_index = (
        category_options.index(initial_category) if initial_category in category_options else 0
    )
 
    # Category comes before Document in both the layout and the code: which
    # documents even make sense to narrow to depends on the chosen category,
    # so the category has to be picked first -- picking "Service Manual"
    # then only shows service manuals in "Search within", instead of the
    # full document list with mostly-irrelevant entries in it.
    col_q, col_cat, col_doc, col_btn = st.columns([3, 1, 1, 1])
    with col_q:
        query = st.text_input(
            "Search",
            value=initial_query,
            placeholder="e.g. overcurrent fault on PCB, HMI calibration, inverter parameter…",
            label_visibility="collapsed",
            key=f"search_box_{st.session_state.search_widget_key}",
        )
    with col_cat:
        category_label = st.selectbox(
            "Category",
            category_options,
            index=default_cat_index,
            label_visibility="collapsed",
            key=f"category_select_{st.session_state.category_widget_key}",
        )
 
    if category_label != "All categories":
        docs_in_scope = [d for d in docs if d.get("category", "Uncategorized") == category_label]
    else:
        docs_in_scope = docs
    doc_options = {"All documents": None}
    for d in docs_in_scope:
        doc_options[d["filename"]] = d["doc_id"]
 
    with col_doc:
        scope_label = st.selectbox(
            "Search within", list(doc_options.keys()), label_visibility="collapsed"
        )
    with col_btn:
        st.button("🔍 Search", use_container_width=True)
 
    # See the big comment above _VOICE_HTML for why this fills the search
    # box directly via same-origin DOM access instead of going through
    # Streamlit's own (currently broken, in this build) component-value
    # protocol -- the recognized text lands straight in the real <input>
    # above, so nothing further needs to happen here at all.
    st.components.v1.html(_VOICE_HTML, height=70)
 
    # -----------------------------------------------------------------
    # Popular search categories
    # -----------------------------------------------------------------
    st.markdown("<div style='font-size:16px;font-weight:700;color:#1B2440;margin:20px 0 10px 0;'>🔥 Popular search categories</div>", unsafe_allow_html=True)
    category_counts = Counter(d.get("category", "Uncategorized") for d in docs)
    cat_rows = [CATEGORY_NAMES[i:i + 3] for i in range(0, len(CATEGORY_NAMES), 3)]
    for row in cat_rows:
        cols = st.columns(3, gap="medium")
        for col, cat_name in zip(cols, row):
            meta = CATEGORY_META[cat_name]
            count = category_counts.get(cat_name, 0)
            with col:
                # st.container(key=...) gets a stable "st-key-<key>" CSS
                # class (see the CSS block near the top of this file) --
                # that's what lets the whole colored card act as one
                # clickable unit: the real st.button underneath is
                # stretched to cover it and made invisible, while this
                # markdown supplies the actual look.
                with st.container(key=f"catcard_{cat_name}"):
                    st.markdown(
                        f"""
                        <div style="background:#FFFFFF;border:1px solid #E9EDF3;border-radius:14px;
                          padding:16px 18px;box-shadow:0 1px 2px rgba(16,24,40,0.04);">
                          <div style="display:flex;align-items:center;gap:12px;">
                            <div style="width:38px;height:38px;border-radius:10px;background:{meta['fg']};
                              display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0;">
                              {meta['icon']}
                            </div>
                            <div style="flex:1;min-width:0;">
                              <div style="font-size:14px;font-weight:700;color:{meta['fg']};">{html.escape(cat_name)}</div>
                              <div style="font-size:12px;color:#6B7280;">{meta['sub']} · {count} doc{'s' if count != 1 else ''}</div>
                            </div>
                            <div style="font-size:16px;color:#9CA3AF;">→</div>
                          </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    if st.button("Browse", key=f"catbtn_{cat_name}", use_container_width=True):
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
            # Distinct documents, not raw page hits -- "4 documents" reads
            # more usefully in Recent Searches than "7 result(s)" when
            # several of those hits are different pages of the same manual.
            doc_count = len({h["doc_id"] for h in hits})
            user_data.add_history_entry(
                store,
                viewer_email,
                query.strip(),
                doc_count,
                category=category_label if category_label != "All categories" else None,
                scope_filename=scope_label if scope_label != "All documents" else None,
            )
 
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
                recent = user_data.get_history(store, viewer_email, limit=6) if viewer_email else []
                header_l, header_r = st.columns([3, 1.6])
                with header_l:
                    st.markdown(
                        "<div style='font-size:15px;font-weight:700;color:#1B2440;margin-bottom:6px;'>🕐 Recent Searches</div>",
                        unsafe_allow_html=True,
                    )
                with header_r:
                    if recent:
                        if st.button("View all history →", key="view_all_history", use_container_width=True):
                            st.session_state.nav = "history"
                            st.rerun()
                if not viewer_email:
                    st.caption("Enter your name in the sidebar to keep a history of your searches.")
                elif not recent:
                    st.caption("No searches yet -- try typing something above.")
                else:
                    for i, entry in enumerate(recent):
                        _render_history_row(entry, "recent", i)
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
        st.info("Enter your name in the sidebar (under the navigation) to start building your history.")
    else:
        history = user_data.get_history(store, viewer_email)
        if not history:
            st.caption("No searches yet -- head to Search and look something up.")
        else:
            for i, entry in enumerate(history):
                _render_history_row(entry, "myhist", i)
 
elif view == "favorites":
    st.markdown("<div style='font-size:22px;font-weight:700;color:#1B2440;margin-bottom:12px;'>☆ Favorites</div>", unsafe_allow_html=True)
    if not viewer_email:
        st.info("Enter your name in the sidebar (under the navigation) to start saving pages.")
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
 
            **My History / Favorites** — enter your name in the sidebar (under the
            navigation) to get a private, per-person search history and a list of
            starred pages, saved alongside the shared library under that name.
            It's not a real login (nothing stops someone from typing a colleague's
            name), so please only use your own name -- everyone's history and
            favorites stay separate as long as everyone does.
 
            **Adding or removing documents** — anyone with access to this app can
            upload or delete PDFs from the Document library (bottom of the Search
            page); uploads go into the same shared Drive folder for everyone.
 
            **Access** — this app is restricted to specific Horizon email addresses.
            To be added, or if something looks broken, contact
            technical.support@horizon.co.jp.
            """
        )
 
    with st.container(border=True):
        st.markdown(
            """
            **System architecture & software**
 
            - **Web app**: built with Streamlit, an open-source Python web app framework.
            - **Hosting**: runs on Streamlit Community Cloud (free hosting); the source
              code lives in a GitHub repo, and pushing changes redeploys the app
              automatically.
            - **File storage**: PDFs and the search index live in the company's shared
              Google Drive folder — the app only reads from it and doesn't keep a
              separate copy of anything.
            - **Search engine**: uses TF-IDF, a standard text-matching algorithm that
              runs entirely inside the app. No external AI service is used, and manual
              content is never sent to a third party.
 
            **Security**
 
            - **Access control**: Streamlit Community Cloud is configured to let only
              specific email addresses open this app.
            - **File permissions**: documents live in the company's Google Drive, so
              access follows Drive's own sharing settings — no separate storage system
              was built for this.
            - **Credentials**: the Google Drive connection credentials are stored
              encrypted on Streamlit's side and never appear in the source code.
            - **Encryption**: all traffic is served over HTTPS.
 
            **Where files are stored**
 
            All PDFs, the search index, and everyone's search history/favorites live in
            the company's shared Google Drive folder — the single source of truth. The
            app itself doesn't persist any files, so nothing is lost even if it restarts.
            """
        )
 
    # Japanese translations of the two boxes above, for colleagues who read
    # this page in Japanese -- kept as separate boxes (rather than replacing
    # the English text) so both languages stay available on the same page.
    with st.container(border=True):
        st.markdown(
            """
            **Technical Manual Search** は、共有 Google Drive 上のすべての PDF サービス
            マニュアルを検索対象とし、チームの誰もがキーワードや音声で目的のページをすぐに
            見つけられるようにするツールです。マニュアルを一つずつ開いて探す必要がなくなります。
 
            **カテゴリ** — アップロードされた各マニュアルは、次のいずれかに分類されます：
            🔧 Service Manual（整備マニュアル）、⚙️ Parts Book（パーツブック）、
            📄 Specifications（仕様書）、⚠️ Error Code（エラーコード）、
            📖 Procedure（作業手順）、🔗 Installation（設置）。検索ページのカテゴリカード、
            またはカテゴリフィルターで種類ごとに絞り込めます。
 
            **マイ履歴 / お気に入り** — サイドバー（ナビゲーションの下）に自分の名前を
            入力すると、検索履歴とお気に入りページの一覧が、その名前のもとで個人ごとに
            保存されます。正式なログインではないため（他の人の名前を入力することも技術的
            には可能です）、必ずご自身の名前をご入力ください。全員がそうすることで、
            各自の履歴とお気に入りが分離された状態を保てます。
 
            **資料の追加・削除** — このアプリにアクセスできる人は誰でも、検索ページ下部の
            「Document library」から PDF をアップロード・削除できます。アップロードされた
            ファイルは全員共通の Drive フォルダに保存されます。
 
            **アクセス** — 本アプリは Horizon の特定のメールアドレスのみに利用が制限されて
            います。追加登録が必要な場合や、不具合を見つけた場合は
            technical.support@horizon.co.jp までご連絡ください。
            """
        )
 
    with st.container(border=True):
        st.markdown(
            """
            **システム構成・使用ソフトウェアについて**
 
            - **Webアプリの基盤**：Streamlit（Pythonで作られたオープンソースのWebアプリ
              開発ツール）を使用しています。
            - **ホスティング**：Streamlit公式の無料クラウドサービス（Streamlit Community
              Cloud）上で稼働しており、ソースコードはGitHubのリポジトリで管理し、更新す
              ると自動的に再デプロイされます。
            - **ファイル保存先**：PDF原本と検索インデックスは、会社のGoogle Drive共有フォ
              ルダに保存されています。本アプリはそのフォルダを読み込んで検索を行うだけで、
              別途ファイルを保持することはありません。
            - **検索アルゴリズム**：TF-IDFというテキスト検索の基本的なアルゴリズムを使用
              しており、処理はアプリ内部で完結します。外部のAIサービスは一切使用しておらず、
              マニュアルの内容が第三者に送信されることもありません。
 
            **セキュリティについて**
 
            - **アクセス制限**：Streamlit Community Cloud側で「指定したメールアドレスのみ
              アクセス可能」という設定を行っており、許可リストに登録された社員のみが本ア
              プリのURLを開くことができます。
            - **ファイル権限**：資料の実体は会社のGoogle Drive上にあるため、アクセス権限
              はGoogle Drive自体の共有設定に準じます。新たに独立したストレージ環境を構築
              しているわけではありません。
            - **認証情報**：Google Driveへの接続に使用する認証情報は、Streamlit側で暗号化
              保存されており、ソースコード上には表示されません。
            - **通信の暗号化**：通信はHTTPSで暗号化されています。
 
            **ファイルの保存場所について**
 
            PDFマニュアル、検索インデックス、各自の検索履歴・お気に入りは、すべて会社の
            Google Drive共有フォルダに保存されており、これが唯一のデータ保管場所です。
            本アプリ自体はファイルを永続的に保持しないため、再起動してもデータが失われる
            ことはありません。
            """
        )
 
