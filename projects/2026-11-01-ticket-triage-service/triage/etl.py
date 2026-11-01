"""ETL: clean raw tickets and load them into SQLite (idempotent)."""
from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

import pandas as pd

from .generate import LABELS

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    label TEXT NOT NULL,
    template_id TEXT,
    text_hash TEXT NOT NULL UNIQUE
);
"""


def normalize(text: str) -> str:
    """Collapse whitespace and strip; keeps case (the vectorizer lowercases)."""
    return re.sub(r"\s+", " ", str(text)).strip()


def text_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).lower().encode()).hexdigest()[:16]


def clean(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Normalize, drop empty/invalid-label rows and exact-duplicate texts.

    Dedup happens *before* any train/test split so identical texts cannot
    appear on both sides (a classic leakage source).
    """
    stats = {"input_rows": len(df)}
    out = df.copy()
    if "template_id" not in out:
        out["template_id"] = None
    out["text"] = out["text"].fillna("").map(normalize)
    out = out[out["text"] != ""]
    stats["dropped_empty"] = stats["input_rows"] - len(out)
    before = len(out)
    out = out[out["label"].isin(LABELS)]
    stats["dropped_bad_label"] = before - len(out)
    out["text_hash"] = out["text"].map(text_hash)
    before = len(out)
    out = out.drop_duplicates("text_hash", keep="first").drop_duplicates("ticket_id", keep="first")
    stats["dropped_duplicates"] = before - len(out)
    stats["output_rows"] = len(out)
    return out.reset_index(drop=True), stats


def load_sqlite(df: pd.DataFrame, db_path: str | Path) -> int:
    """Insert rows, ignoring ones already present. Returns rows inserted."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as con:
        con.execute(SCHEMA)
        before = con.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        con.executemany(
            "INSERT OR IGNORE INTO tickets VALUES (?,?,?,?,?)",
            df[["ticket_id", "text", "label", "template_id", "text_hash"]].itertuples(index=False, name=None),
        )
        after = con.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    return after - before


def read_sqlite(db_path: str | Path) -> pd.DataFrame:
    with sqlite3.connect(db_path) as con:
        return pd.read_sql_query("SELECT * FROM tickets ORDER BY ticket_id", con)
