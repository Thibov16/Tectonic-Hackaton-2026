"""Chunk documents for retrieval."""

from __future__ import annotations


def chunk_text(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    clean = " ".join(text.split())
    if not clean:
        return []
    if len(clean) <= size:
        return [clean]
    chunks: list[str] = []
    start = 0
    while start < len(clean):
        end = min(len(clean), start + size)
        chunks.append(clean[start:end])
        if end >= len(clean):
            break
        start = max(0, end - overlap)
    return chunks
