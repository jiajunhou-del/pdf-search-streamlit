"""
Query expansion using a small, editable synonym/abbreviation dictionary
(synonyms.json). This is what lets a search for "PCB" also match a
document that only ever says "Panel Control Board", without needing a
real embedding model.

synonyms.json is a list of groups; every term in a group is treated as
interchangeable, e.g. ["pcb", "panel control board"]. Edit that file to
add your own equipment-specific abbreviations -- it's re-read on every
search (it's tiny), so changes take effect immediately without
restarting the server.
"""
import json
import os
import re

from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

SYNONYMS_PATH = os.path.join(os.path.dirname(__file__), "synonyms.json")

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9/\-]*")


def _load_synonym_groups():
    try:
        with open(SYNONYMS_PATH, "r", encoding="utf-8") as f:
            groups = json.load(f)
        return [g for g in groups if isinstance(g, list) and len(g) > 1]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _contains_term(text: str, term: str) -> bool:
    """
    Whole-word/phrase match, not a raw substring check -- otherwise a
    short abbreviation like "cb" would false-positive match inside
    unrelated words (e.g. it's a substring of "PCB" itself).
    """
    pattern = r"\b" + re.escape(term) + r"\b"
    return re.search(pattern, text, re.IGNORECASE) is not None


def expand_query(query: str):
    """
    Returns (expanded_query_text, highlight_terms).

    expanded_query_text: the original query plus any synonym terms found
    for it, joined together -- meant to be fed to the vectorizer so
    matching documents score higher even if they use different wording.

    highlight_terms: individual words worth bolding in a result snippet
    (the query's own significant words, plus any synonym terms that were
    pulled in), for the frontend to highlight.
    """
    groups = _load_synonym_groups()
    extra_terms = []
    for group in groups:
        if any(_contains_term(query, term) for term in group):
            for term in group:
                if not _contains_term(query, term):
                    extra_terms.append(term)

    expanded = query
    if extra_terms:
        expanded = query + " " + " ".join(extra_terms)

    own_words = [
        w for w in _WORD_RE.findall(query)
        if len(w) > 1 and w.lower() not in ENGLISH_STOP_WORDS
    ]
    extra_words = []
    for term in extra_terms:
        extra_words.extend(_WORD_RE.findall(term))

    highlight_terms = list(dict.fromkeys(own_words + extra_terms + extra_words))
    return expanded, highlight_terms
