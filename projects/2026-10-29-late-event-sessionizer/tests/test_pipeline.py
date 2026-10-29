import csv
import random
from pathlib import Path

import pytest

from sessionizer import Sessionizer
from sessionizer.generate import generate

HDR = ["event_id", "user_id", "event_ts", "event_type"]


def write(tmp_path, name, rows):
    p = tmp_path / f"{name}.csv"
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HDR)
        w.writerows(rows)
    return p


def test_gap_boundary_and_session_fields(tmp_path):
    s = Sessionizer(gap_minutes=30)
    p = write(tmp_path, "b1", [
        ("1", "u", "2026-01-01T10:00:00", "page_view"),
        ("2", "u", "2026-01-01T10:30:00", "click"),      # exactly 30 min: same session
        ("3", "u", "2026-01-01T11:00:01", "purchase"),   # 30m01s: new session
    ])
    s.process_file(p)
    rows = s.sessions()
    assert len(rows) == 2
    assert rows[0][4] == 2 and rows[0][5] == 1800 and rows[0][6] is False
    assert rows[1][4] == 1 and rows[1][6] is True


def test_invalid_and_duplicates_rejected(tmp_path):
    s = Sessionizer()
    p = write(tmp_path, "b1", [
        ("1", "u", "2026-01-01T10:00:00", "click"),
        ("1", "u", "2026-01-01T10:00:00", "click"),   # in-batch dup
        ("", "u", "2026-01-01T10:00:00", "click"),
        ("3", "", "2026-01-01T10:00:00", "click"),
        ("4", "u", "garbage", "click"),
        ("5", "u", "2026-01-01T10:00:00", "nope"),
    ])
    r = s.process_file(p)
    assert (r.rows_read, r.accepted, r.rejected_invalid, r.rejected_duplicate) == (6, 1, 4, 1)
    reasons = {x[0] for x in s.con.execute("SELECT reason FROM rejects").fetchall()}
    assert reasons == {"duplicate_event_id", "missing_event_id", "missing_user_id",
                       "bad_timestamp", "bad_event_type"}


def test_cross_batch_duplicate_rejected(tmp_path):
    s = Sessionizer()
    row = ("1", "u", "2026-01-01T10:00:00", "click")
    s.process_file(write(tmp_path, "b1", [row]))
    r = s.process_file(write(tmp_path, "b2", [row]))
    assert r.accepted == 0 and r.rejected_duplicate == 1
    assert s.counts()["events"] == 1


def test_reprocessing_same_batch_id_fails(tmp_path):
    s = Sessionizer()
    p = write(tmp_path, "b1", [("1", "u", "2026-01-01T10:00:00", "click")])
    s.process_file(p)
    with pytest.raises(ValueError):
        s.process_file(p)


def test_late_event_within_lateness_merges_sessions(tmp_path):
    s = Sessionizer(gap_minutes=30, allowed_lateness_minutes=60)
    s.process_file(write(tmp_path, "b1", [
        ("1", "u", "2026-01-01T10:00:00", "click"),
        ("2", "u", "2026-01-01T10:50:00", "click"),
    ]))
    assert len(s.sessions()) == 2
    r = s.process_file(write(tmp_path, "b2", [("3", "u", "2026-01-01T10:25:00", "click")]))
    assert r.accepted == 1 and r.late_diverted == 0
    rows = s.sessions()
    assert len(rows) == 1 and rows[0][4] == 3


def test_too_late_event_diverted_and_sessions_unchanged(tmp_path):
    s = Sessionizer(gap_minutes=30, allowed_lateness_minutes=60)
    s.process_file(write(tmp_path, "b1", [
        ("1", "u", "2026-01-01T10:00:00", "click"),
        ("2", "v", "2026-01-01T12:00:00", "click"),  # watermark 12:00
    ]))
    before = s.sessions()
    r = s.process_file(write(tmp_path, "b2", [("3", "u", "2026-01-01T10:10:00", "click")]))
    assert r.late_diverted == 1 and r.accepted == 0
    assert s.sessions() == before
    assert s.counts()["late_events"] == 1
    # a re-delivery of the late event is a duplicate, not a second late event
    r = s.process_file(write(tmp_path, "b3", [("3", "u", "2026-01-01T10:10:00", "click")]))
    assert r.rejected_duplicate == 1 and s.counts()["late_events"] == 1


def test_only_affected_users_recomputed(tmp_path):
    s = Sessionizer()
    s.process_file(write(tmp_path, "b1", [
        ("1", "a", "2026-01-01T10:00:00", "click"),
        ("2", "b", "2026-01-01T10:00:00", "click")]))
    r = s.process_file(write(tmp_path, "b2", [("3", "a", "2026-01-01T10:05:00", "click")]))
    assert r.users_recomputed == 1


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_incremental_equals_full_rebuild(tmp_path, seed):
    """Invariant: incremental session maintenance == recompute from accepted events."""
    paths = generate(tmp_path / "g", n_users=25, n_batches=5, seed=seed)
    s = Sessionizer(allowed_lateness_minutes=45)
    for p in paths:
        s.process_file(p)
    incremental = s.sessions()
    assert incremental
    s.full_rebuild()
    assert s.sessions() == incremental


def test_conservation_of_rows(tmp_path):
    paths = generate(tmp_path / "g", seed=11)
    s = Sessionizer(allowed_lateness_minutes=30)
    reports = [s.process_file(p) for p in paths]
    for r in reports:
        assert r.rows_read == r.accepted + r.late_diverted + r.rejected_invalid + r.rejected_duplicate
    total_events = sum(r.accepted for r in reports)
    assert s.counts()["events"] == total_events
    assert sum(x[4] for x in s.sessions()) == total_events


def test_generator_is_deterministic(tmp_path):
    a = generate(tmp_path / "a", seed=5)
    b = generate(tmp_path / "b", seed=5)
    assert [p.read_text() for p in a] == [p.read_text() for p in b]


def test_invalid_config():
    with pytest.raises(ValueError):
        Sessionizer(gap_minutes=0)


def test_sample_data_matches_expected_output(tmp_path):
    root = Path(__file__).resolve().parent.parent / "data"
    s = Sessionizer(gap_minutes=30, allowed_lateness_minutes=60)
    for p in sorted((root / "sample_batches").glob("*.csv")):
        s.process_file(p)
    out = tmp_path / "sessions.csv"
    s.export_sessions_csv(out)
    assert out.read_text() == (root / "expected" / "sessions.csv").read_text()
