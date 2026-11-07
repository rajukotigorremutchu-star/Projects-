"""Minimal Okapi BM25 index (no dependencies)."""
from __future__ import annotations

import math
from collections import Counter

from .chunker import Chunk
from .text import tokenize


class BM25:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks, self.k1, self.b = chunks, k1, b
        # heading and doc title are indexed too: they carry strong topical signal
        self.docs = [tokenize(f"{c.doc} {c.heading} {c.text}") for c in chunks]
        self.tf = [Counter(d) for d in self.docs]
        self.avgdl = sum(len(d) for d in self.docs) / max(len(self.docs), 1)
        df = Counter(t for d in self.docs for t in set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def score(self, query_tokens: list[str], i: int) -> float:
        tf, dl, s = self.tf[i], len(self.docs[i]), 0.0
        for t in set(query_tokens):
            f = tf.get(t, 0)
            if f:
                s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
        return s

    def search(self, query: str, k: int = 3) -> list[tuple[Chunk, float]]:
        q = tokenize(query)
        scored = [(self.chunks[i], self.score(q, i)) for i in range(len(self.chunks))]
        scored.sort(key=lambda x: (-x[1], x[0].id))  # deterministic tie-break
        return [(c, s) for c, s in scored[:k] if s > 0]
