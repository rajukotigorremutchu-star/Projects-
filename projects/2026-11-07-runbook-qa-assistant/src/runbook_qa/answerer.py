"""Answer generation. `extractive` is a deterministic offline mode; `claude` is optional."""
from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field

from .chunker import Chunk
from .text import tokenize

ABSTAIN_TEXT = "I could not find this in the runbooks."


@dataclass
class Answer:
    text: str
    citations: list[str] = field(default_factory=list)
    abstained: bool = False


def coverage(query: str, chunk: Chunk) -> float:
    """Fraction of distinct content terms in the query that appear in the chunk."""
    q = set(tokenize(query))
    if not q:
        return 0.0
    c = set(tokenize(f"{chunk.doc} {chunk.heading} {chunk.text}"))
    return len(q & c) / len(q)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def extractive_answer(query: str, hits: list[tuple[Chunk, float]], max_sentences: int = 2) -> Answer:
    """Pick the sentences of the best chunk with highest query-term overlap."""
    chunk = hits[0][0]
    q = set(tokenize(query))
    sents = _sentences(chunk.text)
    ranked = sorted(range(len(sents)), key=lambda i: (-len(q & set(tokenize(sents[i]))), i))
    chosen = sorted(ranked[:max_sentences])  # restore document order
    return Answer(" ".join(sents[i] for i in chosen), [chunk.id])


def claude_answer(query: str, hits: list[tuple[Chunk, float]], model: str | None = None) -> Answer:
    """Calls the Anthropic Messages API. Requires ANTHROPIC_API_KEY. Not exercised by tests."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set; use --mode extractive")
    context = "\n\n".join(f"[{c.id}]\n{c.text}" for c, _ in hits)
    prompt = (
        "Answer the question using ONLY the runbook excerpts. Cite chunk ids in square brackets. "
        f"If the excerpts do not contain the answer reply exactly: {ABSTAIN_TEXT}\n\n"
        f"{context}\n\nQuestion: {query}"
    )
    body = json.dumps({
        "model": model or os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5"),
        "max_tokens": 400,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", body,
        {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        text = json.load(r)["content"][0]["text"].strip()
    if text.startswith(ABSTAIN_TEXT):
        return Answer(ABSTAIN_TEXT, [], True)
    valid = {c.id for c, _ in hits}
    cites = [c for c in dict.fromkeys(re.findall(r"\[([^\]]+)\]", text)) if c in valid]
    return Answer(text, cites)
