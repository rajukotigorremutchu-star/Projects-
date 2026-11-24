"""Split markdown runbooks into one chunk per '##' section."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Chunk:
    id: str        # "<doc-stem>#<heading-slug>"
    doc: str       # document title (H1)
    heading: str   # section title (H2)
    text: str      # section body


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def chunk_markdown(stem: str, md: str) -> list[Chunk]:
    title, heading, buf, out = stem, None, [], []

    def flush():
        if heading is not None and buf:
            out.append(Chunk(f"{stem}#{slug(heading)}", title, heading, " ".join(buf).strip()))

    for line in md.splitlines():
        if line.startswith("## "):
            flush()
            heading, buf = line[3:].strip(), []
        elif line.startswith("# "):
            title = line[2:].strip()
        elif line.strip():
            buf.append(line.strip())
    flush()
    return out


def load_chunks(directory: str | Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for p in sorted(Path(directory).glob("*.md")):
        chunks.extend(chunk_markdown(p.stem, p.read_text(encoding="utf-8")))
    return chunks
