"""Hybrid BM25 + optional dense retrieval index."""

from __future__ import annotations

import logging
import math
import re
import sqlite3
from typing import Any

from rank_bm25 import BM25Okapi

from app.ingest import store

logger = logging.getLogger(__name__)

RRF_K = 60

_SENTENCE_MODEL = None
_HAS_ST = False
try:
    from sentence_transformers import SentenceTransformer  # type: ignore

    _HAS_ST = True
except ImportError:
    logger.warning(
        "sentence-transformers not available; HybridIndex will use BM25 only"
    )


def tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def _get_sentence_model() -> Any | None:
    global _SENTENCE_MODEL
    if not _HAS_ST:
        return None
    if _SENTENCE_MODEL is None:
        try:
            _SENTENCE_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load sentence-transformers model: %s", exc)
            return None
    return _SENTENCE_MODEL


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(y * y for y in b)) or 1e-9
    return dot / (na * nb)


class HybridIndex:
    """Chunk-level BM25 (+ optional dense) index with document aggregation."""

    def __init__(self) -> None:
        self._chunks: list[dict[str, Any]] = []
        self._tokenized: list[list[str]] = []
        self._bm25: BM25Okapi | None = None
        self._embeddings: list[list[float]] | None = None
        self._built = False

    def build(self, conn: sqlite3.Connection) -> None:
        chunks = store.list_chunks(conn)
        self._chunks = chunks
        self._tokenized = [tokenize(c.get("text") or "") for c in chunks]
        if self._tokenized:
            self._bm25 = BM25Okapi(self._tokenized)
        else:
            self._bm25 = None

        model = _get_sentence_model()
        if model is not None and chunks:
            texts = [c.get("text") or "" for c in chunks]
            try:
                emb = model.encode(texts, show_progress_bar=False)
                self._embeddings = [list(map(float, row)) for row in emb]
            except Exception as exc:  # noqa: BLE001
                logger.warning("Dense embedding failed, BM25 only: %s", exc)
                self._embeddings = None
        else:
            self._embeddings = None
        self._built = True

    def search(self, query: str, top_k: int = 25) -> list[dict[str, Any]]:
        if not self._built or not self._chunks or self._bm25 is None:
            return []

        q_tokens = tokenize(query)
        if not q_tokens:
            return []

        bm25_scores = self._bm25.get_scores(q_tokens)
        bm25_ranked = sorted(
            range(len(bm25_scores)),
            key=lambda i: float(bm25_scores[i]),
            reverse=True,
        )

        dense_ranked: list[int] = []
        model = _get_sentence_model()
        if model is not None and self._embeddings is not None:
            try:
                q_emb = list(map(float, model.encode([query], show_progress_bar=False)[0]))
                dense_scores = [_cosine(q_emb, e) for e in self._embeddings]
                dense_ranked = sorted(
                    range(len(dense_scores)),
                    key=lambda i: dense_scores[i],
                    reverse=True,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Dense search failed: %s", exc)
                dense_ranked = []

        rrf: dict[int, float] = {}
        for rank, idx in enumerate(bm25_ranked[: max(top_k * 4, 50)]):
            rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (RRF_K + rank + 1)
        for rank, idx in enumerate(dense_ranked[: max(top_k * 4, 50)]):
            rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (RRF_K + rank + 1)

        # Aggregate by document: keep best chunk evidence + sum RRF
        by_doc: dict[int, dict[str, Any]] = {}
        for idx, score in rrf.items():
            chunk = self._chunks[idx]
            doc_id = int(chunk["document_id"])
            existing = by_doc.get(doc_id)
            if existing is None or score > existing["_chunk_score"]:
                by_doc[doc_id] = {
                    "document_id": doc_id,
                    "path": chunk.get("path") or "",
                    "title": chunk.get("title") or "",
                    "doc_type": chunk.get("doc_type") or "",
                    "status": chunk.get("status"),
                    "country": chunk.get("country"),
                    "customer": chunk.get("customer"),
                    "retracted": bool(chunk.get("retracted")),
                    "evidence": (chunk.get("text") or "")[:600],
                    "score": 0.0,
                    "_chunk_score": score,
                    "_sum_score": score,
                }
            else:
                existing["_sum_score"] += score

        results = []
        for item in by_doc.values():
            item["score"] = float(item["_sum_score"])
            del item["_chunk_score"]
            del item["_sum_score"]
            results.append(item)

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]
