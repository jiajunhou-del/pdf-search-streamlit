"""
Extracts text from a PDF, page by page, and splits each page into
overlapping chunks small enough to embed well while keeping track of
which page (and roughly where on the page) each chunk came from.
"""
import re
import pymupdf as fitz  # PyMuPDF (the "fitz" module name is deprecated)


def _clean(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pages(pdf_path: str):
    """Returns a list of {page_number, text} for every page (1-indexed)."""
    doc = fitz.open(pdf_path)
    pages = []
    for i, page in enumerate(doc):
        text = _clean(page.get_text("text"))
        pages.append({"page_number": i + 1, "text": text})
    doc.close()
    return pages


def chunk_pages(pages, chunk_size=800, overlap=150):
    """
    Splits each page's text into overlapping chunks (by characters).
    Keeping chunks page-scoped means every chunk maps to exactly one
    page number, which is what lets search results cite a page.
    """
    chunks = []
    for page in pages:
        text = page["text"]
        if not text:
            continue
        if len(text) <= chunk_size:
            chunks.append({"page_number": page["page_number"], "text": text})
            continue
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunk_text = text[start:end]
            chunks.append({"page_number": page["page_number"], "text": chunk_text})
            if end == len(text):
                break
            start = end - overlap
    return chunks
