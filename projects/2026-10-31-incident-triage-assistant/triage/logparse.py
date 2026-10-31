"""Parse raw log lines and reduce them to stable error signatures."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

LEVELS = {"DEBUG": 0, "INFO": 1, "WARN": 2, "WARNING": 2, "ERROR": 3, "CRITICAL": 4, "FATAL": 4}
LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?Z?)\s+"
    r"(?P<level>[A-Za-z]+)\s+(?:\[(?P<svc>[\w.\-]+)\]\s+)?(?P<msg>.*)$"
)
# Order matters: most specific first.
NORMALIZERS = [
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<hex>"),
    (re.compile(r"(?<![A-Za-z])/[\w./\-]+"), "<path>"),
    (re.compile(r"\b\d+(?:\.\d+)?\b"), "<n>"),
]


@dataclass(frozen=True)
class LogLine:
    level: str
    service: str
    message: str


def parse_line(line: str) -> LogLine | None:
    m = LINE_RE.match(line.strip())
    if not m:
        return None
    level = m.group("level").upper()
    if level not in LEVELS:
        return None
    return LogLine(level, m.group("svc") or "unknown", m.group("msg"))


def normalize(message: str) -> str:
    for pattern, token in NORMALIZERS:
        message = pattern.sub(token, message)
    return re.sub(r"\s+", " ", message).strip()


@dataclass
class Signature:
    text: str
    service: str
    level: str
    count: int
    example: str


def extract_signatures(lines: list[str], min_level: str = "WARN") -> list[Signature]:
    """Group warn+ lines by (service, normalized message); most severe/frequent first."""
    floor = LEVELS[min_level]
    counts: Counter = Counter()
    meta: dict = {}
    for raw in lines:
        parsed = parse_line(raw)
        if parsed is None or LEVELS[parsed.level] < floor:
            continue
        key = (parsed.service, normalize(parsed.message))
        counts[key] += 1
        prev = meta.get(key)
        if prev is None or LEVELS[parsed.level] > LEVELS[prev[0]]:
            meta[key] = (parsed.level, parsed.message)
    sigs = [
        Signature(text=k[1], service=k[0], level=meta[k][0], count=c, example=meta[k][1])
        for k, c in counts.items()
    ]
    sigs.sort(key=lambda s: (-LEVELS[s.level], -s.count, s.text))
    return sigs
