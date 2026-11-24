"""Tokenisation shared by indexing and querying."""
import re

STOPWORDS = frozenset("""a an and are as at be by can do does did for from has have how i if in is it its of on or
that the their then there this to was we what when where which who why will with you your should must not
use used using run any all after before into than also only each every so too""".split())

_TOKEN = re.compile(r"[a-z0-9][a-z0-9_\-]*")


def stem(tok: str) -> str:
    """Very light suffix stripping, enough to match failover/failovers, promoted/promote."""
    for suf in ("ing", "ed", "es", "s"):
        if tok.endswith(suf) and len(tok) - len(suf) >= 4:
            return tok[: -len(suf)]
    return tok


def tokenize(text: str, keep_stopwords: bool = False) -> list[str]:
    toks = _TOKEN.findall(text.lower())
    return [stem(t) for t in toks if keep_stopwords or t not in STOPWORDS]
