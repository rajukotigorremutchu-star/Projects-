"""Pure-Python BM25 retriever over runbook sections."""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

STOP = set("a an and are as at be by for from has in is it of on or that the to was were with this".split())


def tokenize(text: str) -> list[str]:
    toks = re.findall(r"[a-z][a-z0-9_]+", text.lower())
    return [t for t in toks if t not in STOP and not t.startswith("n_")]


@dataclass
class Chunk:
    runbook_id: str
    title: str
    section: str
    text: str


def load_runbooks(directory: Path) -> list[Chunk]:
    """Each runbook is Markdown: '# Title' then '## Section' blocks. One chunk per section."""
    chunks: list[Chunk] = []
    for path in sorted(Path(directory).glob("*.md")):
        title, section, buf = path.stem, "intro", []

        def flush():
            body = "\n".join(buf).strip()
            if body:
                chunks.append(Chunk(path.stem, title, section, f"{title}. {section}.\n{body}"))

        for line in path.read_text().splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
            elif line.startswith("## "):
                flush()
                buf, section = [], line[3:].strip()
            else:
                buf.append(line)
        flush()
    return chunks


class BM25:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks, self.k1, self.b = chunks, k1, b
        self.docs = [Counter(tokenize(c.text)) for c in chunks]
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.lens) / max(len(self.lens), 1)
        df: Counter = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def score(self, query: str) -> list[float]:
        q = tokenize(query)
        out = []
        for d, ln in zip(self.docs, self.lens):
            s = 0.0
            for t in q:
                f = d.get(t, 0)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * ln / self.avg))
            out.append(s)
        return out

    def search_runbooks(self, query: str, k: int = 3) -> list[tuple[Chunk, float]]:
        """Best chunk per runbook, ranked; zero-score runbooks are dropped."""
        best: dict[str, tuple[Chunk, float]] = {}
        for chunk, s in zip(self.chunks, self.score(query)):
            if s > 0 and (chunk.runbook_id not in best or s > best[chunk.runbook_id][1]):
                best[chunk.runbook_id] = (chunk, s)
        return sorted(best.values(), key=lambda x: (-x[1], x[0].runbook_id))[:k]
