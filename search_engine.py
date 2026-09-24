"""
Same TF-IDF search core as the original FastAPI prototype, adapted to
persist through a pluggable store (drive_store.py) instead of a local
pickle file directly -- so the index can live in Google Drive alongside
the PDFs it describes.

doc_id is simply the file's id in the store (the Google Drive file id, or
the filename in local mode) -- no separate id scheme needed.
"""
import pickle

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from query_expand import expand_query


class SearchIndex:
    def __init__(self, store):
        self.store = store
        self.records = []  # list of {doc_id, filename, page_number, text}
        self.vectorizer = None
        self.matrix = None
        self._load()
        self._rebuild()

    def _load(self):
        raw = self.store.load_index_bytes()
        if raw:
            self.records = pickle.loads(raw)

    def _save(self):
        self.store.save_index_bytes(pickle.dumps(self.records))

    def _rebuild(self):
        if not self.records:
            self.vectorizer = None
            self.matrix = None
            return
        texts = [r["text"] for r in self.records]
        self.vectorizer = TfidfVectorizer(
            stop_words="english", ngram_range=(1, 2), max_features=50000
        )
        self.matrix = self.vectorizer.fit_transform(texts)

    def add_document(self, doc_id: str, filename: str, chunks: list):
        if not chunks:
            return 0
        for c in chunks:
            self.records.append(
                {
                    "doc_id": doc_id,
                    "filename": filename,
                    "page_number": c["page_number"],
                    "text": c["text"],
                }
            )
        self._save()
        self._rebuild()
        return len(chunks)

    def remove_document(self, doc_id: str):
        self.records = [r for r in self.records if r["doc_id"] != doc_id]
        self._save()
        self._rebuild()

    def list_documents(self):
        seen = {}
        for r in self.records:
            seen[r["doc_id"]] = r["filename"]
        return [{"doc_id": k, "filename": v} for k, v in seen.items()]

    def search(self, query: str, top_k: int = 8, doc_id: str = None):
        if self.vectorizer is None or self.matrix is None:
            return []

        expanded_query, highlight_terms = expand_query(query)
        query_vec = self.vectorizer.transform([expanded_query])
        sims = cosine_similarity(query_vec, self.matrix)[0]

        if doc_id:
            mask = np.array([r["doc_id"] == doc_id for r in self.records])
            sims = np.where(mask, sims, -1.0)

        top_indices = np.argsort(sims)[::-1][:top_k]
        hits = []
        for i in top_indices:
            if sims[i] <= 0:
                continue
            r = self.records[i]
            hits.append(
                {
                    "doc_id": r["doc_id"],
                    "filename": r["filename"],
                    "page_number": r["page_number"],
                    "snippet": r["text"],
                    "score": float(sims[i]),
                    "highlight_terms": highlight_terms,
                }
            )
        return hits
