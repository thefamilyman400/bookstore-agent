"""
RAG Vector Store — in-memory cosine similarity over book embeddings.
Uses sentence-transformers (all-MiniLM-L6-v2) to embed book data.
Falls back to TF-IDF keyword matching if sentence-transformers is unavailable.
"""
import json
import math
import os
from pathlib import Path
from typing import List, Dict, Any

BOOKS_PATH = Path(__file__).parent / "data" / "books.json"

# ── Load books ────────────────────────────────────────────────────────────────
def _load_books() -> List[Dict[str, Any]]:
    with open(BOOKS_PATH, encoding="utf-8") as f:
        return json.load(f)

BOOKS: List[Dict[str, Any]] = _load_books()

# ── Build searchable text per book ────────────────────────────────────────────
def _book_text(book: Dict[str, Any]) -> str:
    return (
        f"{book['title']} {book['author']} {book.get('author_bio', '')} {book['genre']} "
        f"{book['synopsis']} {' '.join(book['tags'])}"
    ).lower()

BOOK_TEXTS = [_book_text(b) for b in BOOKS]

# ── Try sentence-transformers; fall back to TF-IDF ───────────────────────────
try:
    from sentence_transformers import SentenceTransformer
    import numpy as np

    _MODEL = SentenceTransformer("all-MiniLM-L6-v2")
    _EMBEDDINGS = _MODEL.encode(BOOK_TEXTS, show_progress_bar=False, normalize_embeddings=True)

    def _embed(text: str):
        return _MODEL.encode([text.lower()], normalize_embeddings=True)[0]

    def semantic_search(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        q_vec = _embed(query)
        scores = np.dot(_EMBEDDINGS, q_vec)
        top_idx = scores.argsort()[::-1][:top_k]
        return [
            {**BOOKS[i], "score": float(round(scores[i], 3))}
            for i in top_idx if scores[i] > 0.15
        ]

    BACKEND = "sentence-transformers"

except ImportError:
    # ── TF-IDF fallback ───────────────────────────────────────────────────────
    import re
    from collections import Counter

    def _tokenize(text: str) -> List[str]:
        return re.findall(r"[a-z]+", text.lower())

    _IDF: Dict[str, float] = {}
    _N = len(BOOK_TEXTS)

    def _build_idf():
        df: Dict[str, int] = {}
        for text in BOOK_TEXTS:
            for tok in set(_tokenize(text)):
                df[tok] = df.get(tok, 0) + 1
        for tok, freq in df.items():
            _IDF[tok] = math.log(_N / (1 + freq))

    _build_idf()

    def _tfidf_vec(text: str) -> Dict[str, float]:
        tokens = _tokenize(text)
        tf = Counter(tokens)
        n = max(len(tokens), 1)
        return {tok: (count / n) * _IDF.get(tok, 0) for tok, count in tf.items()}

    def _cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
        common = set(a) & set(b)
        dot = sum(a[k] * b[k] for k in common)
        mag_a = math.sqrt(sum(v ** 2 for v in a.values()))
        mag_b = math.sqrt(sum(v ** 2 for v in b.values()))
        return dot / (mag_a * mag_b + 1e-9)

    _VECS = [_tfidf_vec(t) for t in BOOK_TEXTS]

    def semantic_search(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        q_vec = _tfidf_vec(query)
        scores = [_cosine(q_vec, v) for v in _VECS]
        top_idx = sorted(range(_N), key=lambda i: scores[i], reverse=True)[:top_k]
        return [
            {**BOOKS[i], "score": float(round(scores[i], 3))}
            for i in top_idx if scores[i] > 0.0
        ]

    BACKEND = "tfidf-fallback"
