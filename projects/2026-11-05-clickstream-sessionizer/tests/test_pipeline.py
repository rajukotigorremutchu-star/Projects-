import csv
import json
from pathlib import Path

import duckdb
import pytest

from sessionizer.cli import main
from sessionizer.generate import generate
from sessionizer.pipeline import connect, export_parquet, ingest_batch, validate

SAMPLE = Path(__file__).parent.parent / "data" / "sample"


def sessions(con):
    rows = con.execute(
        "SELECT user_id, session_start, session_end, duration_s, n_events, n_page_views, converted "
        "FROM sessions ORDER BY user_id, session_start"
    ).fetchall()
    return [tuple(str(x).lower() if isinstance(x, bool) else str(x) for x in r) for r in rows]


def expected(name):
    with open(SAMPLE / name) as f:
        return [tuple(r) for r in list(csv.reader(f))[1:]]


@pytest.fixture
def con():
    c = connect()
    yield c
    c.close()


def test_validate_reasons():
    assert validate("{nope")[1] == "invalid_json"
    assert validate("[1]")[1] == "not_an_object"
    assert validate('{"user_id":"a","event_type":"click","ts":"2026-01-01T00:00:00Z"}')[1] == "missing_event_id"
    assert validate('{"event_id":"1","user_id":"a","event_type":"x","ts":"2026-01-01T00:00:00Z"}')[1] == "unknown_event_type"
    assert validate('{"event_id":"1","user_id":"a","event_type":"click","ts":"zzz"}')[1] == "bad_timestamp"
    assert validate('{"event_id":"1","user_id":"a","event_type":"click","ts":12}')[1] == "bad_timestamp"


def test_timezone_offsets_normalised_to_utc():
    rec, _ = validate('{"event_id":"1","user_id":"a","event_type":"click","ts":"2026-01-01T02:00:00+02:00"}')
    assert str(rec["ts"]) == "2026-01-01 00:00:00"


def test_first_batch_matches_expected(con):
    res = ingest_batch(con, SAMPLE / "01_initial.jsonl")
    assert (res.read, res.loaded, res.duplicates, res.quarantined) == (9, 4, 1, 4)
    assert sessions(con) == expected("expected_after_batch1.csv")
    reasons = dict(con.execute("SELECT reason, count(*) FROM quarantine GROUP BY 1").fetchall())
    assert reasons == {"invalid_json": 1, "unknown_event_type": 1, "bad_timestamp": 1, "missing_user_id": 1}


def test_late_event_merges_sessions(con):
    ingest_batch(con, SAMPLE / "01_initial.jsonl")
    res = ingest_batch(con, SAMPLE / "02_late.jsonl")
    assert res.loaded == 1 and res.duplicates == 1
    assert sessions(con) == expected("expected_after_batch2.csv")


def test_only_touched_users_are_recomputed(con):
    ingest_batch(con, SAMPLE / "01_initial.jsonl")
    res = ingest_batch(con, SAMPLE / "02_late.jsonl")
    assert res.affected_users == 1  # u2 untouched


def test_rerun_is_idempotent(con):
    ingest_batch(con, SAMPLE / "01_initial.jsonl")
    snap = sessions(con)
    assert ingest_batch(con, SAMPLE / "01_initial.jsonl").skipped
    assert sessions(con) == snap
    # forced reprocess also yields the same state
    res = ingest_batch(con, SAMPLE / "01_initial.jsonl", force=True)
    assert res.loaded == 0 and sessions(con) == snap
    assert con.execute("SELECT count(*) FROM quarantine").fetchone()[0] == 4


def test_gap_boundary(tmp_path, con):
    f = tmp_path / "b.jsonl"
    ev = [("a", "2026-01-01T00:00:00Z"), ("b", "2026-01-01T00:30:00Z"),  # exactly 30m: same session
          ("c", "2026-01-01T01:00:01Z")]                                # 30m01s: new session
    f.write_text("\n".join(json.dumps({"event_id": i, "user_id": "u", "event_type": "click", "ts": t}) for i, t in ev))
    ingest_batch(con, f, gap_minutes=30)
    assert con.execute("SELECT n_events FROM sessions ORDER BY session_start").fetchall() == [(2,), (1,)]


def test_daily_metrics(con):
    ingest_batch(con, SAMPLE / "01_initial.jsonl")
    assert con.execute("SELECT * FROM daily_metrics").fetchall() == [
        (__import__("datetime").date(2026, 11, 1), 3, 2, 200.0, round(1 / 3, 4))]


def test_failed_batch_rolls_back(tmp_path, con, monkeypatch):
    import sessionizer.pipeline as p
    monkeypatch.setattr(p, "refresh_daily_metrics", lambda c: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        ingest_batch(con, SAMPLE / "01_initial.jsonl")
    assert con.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM processed_batches").fetchone()[0] == 0


def test_parquet_export_partitioned(tmp_path, con):
    ingest_batch(con, SAMPLE / "01_initial.jsonl")
    files = export_parquet(con, tmp_path / "pq")
    assert files and "session_date=2026-11-01" in str(files[0])
    n = duckdb.connect().execute(f"SELECT count(*) FROM read_parquet('{tmp_path}/pq/**/*.parquet', hive_partitioning=true)").fetchone()[0]
    assert n == 3


def test_generated_data_invariants(tmp_path, con):
    f = tmp_path / "gen.jsonl"
    generate(f, n_users=15)
    res = ingest_batch(con, f)
    assert res.quarantined == 3 and res.duplicates == 1
    # every event belongs to exactly one session
    assert con.execute("SELECT sum(n_events) FROM sessions").fetchone()[0] == res.loaded
    # no two sessions of a user are within the gap
    bad = con.execute("""SELECT count(*) FROM (SELECT session_start - lag(session_end) OVER
        (PARTITION BY user_id ORDER BY session_start) AS g FROM sessions) WHERE g <= INTERVAL 30 MINUTE""").fetchone()[0]
    assert bad == 0


def test_cli(tmp_path, capsys):
    rc = main(["run", str(SAMPLE), "--db", str(tmp_path / "x.duckdb"), "--parquet-dir", str(tmp_path / "pq")])
    out = capsys.readouterr().out
    assert rc == 0 and "daily_metrics" in out and "parquet files: 1" in out
