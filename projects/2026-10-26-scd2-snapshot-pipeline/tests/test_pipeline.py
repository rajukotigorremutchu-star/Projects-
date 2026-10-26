import filecmp
from datetime import date
from pathlib import Path

import pytest

from scd2 import pipeline as p
from scd2.generate import generate

ROOT = Path(__file__).resolve().parent.parent
D = lambda n: date(2026, 1, n)


def test_sample_matches_expected_output(tmp_path):
    con = p.connect()
    p.run_directory(con, ROOT / "data/sample")
    out = tmp_path / "dim.csv"
    p.export_dimension(con, out)
    assert filecmp.cmp(out, ROOT / "data/expected_dim_customer.csv", shallow=False)


def test_change_closes_old_and_opens_new():
    con = p.connect()
    p.load_snapshot(con, D(1), [(1, "A", "X", "free")])
    res = p.load_snapshot(con, D(2), [(1, "A", "X", "pro")])
    assert (res["inserted"], res["closed"]) == (1, 1)
    rows = con.execute("SELECT plan, valid_from, valid_to, is_current FROM dim_customer ORDER BY valid_from").fetchall()
    assert rows == [("free", D(1), D(2), False), ("pro", D(2), p.OPEN_END, True)]


def test_unchanged_snapshot_creates_nothing():
    con = p.connect()
    p.load_snapshot(con, D(1), [(1, "A", "X", "free")])
    res = p.load_snapshot(con, D(2), [(1, "A", "X", "free")])
    assert (res["inserted"], res["closed"]) == (0, 0)


def test_reload_same_date_is_idempotent():
    con = p.connect()
    p.load_snapshot(con, D(1), [(1, "A", "X", "free")])
    before = con.execute("SELECT * FROM dim_customer").fetchall()
    assert p.load_snapshot(con, D(1), [(1, "A", "X", "pro")])["skipped"]
    assert con.execute("SELECT * FROM dim_customer").fetchall() == before


def test_out_of_order_rejected():
    con = p.connect()
    p.load_snapshot(con, D(5), [])
    with pytest.raises(p.SnapshotError):
        p.load_snapshot(con, D(4), [])


def test_null_vs_string_null_are_distinct():
    con = p.connect()
    p.load_snapshot(con, D(1), [(1, "A", None, "free")])
    res = p.load_snapshot(con, D(2), [(1, "A", "<null>", "free")])
    assert res["inserted"] == 1  # literal '<null>' must not collide with a real NULL


def test_deleted_then_reappearing_key():
    con = p.connect()
    p.load_snapshot(con, D(1), [(1, "A", "X", "free")])
    p.load_snapshot(con, D(2), [])
    assert con.execute("SELECT count(*) FROM dim_customer WHERE is_current").fetchone()[0] == 0
    p.load_snapshot(con, D(3), [(1, "A", "X", "free")])
    assert p.as_of(con, D(2)) == []
    assert len(p.as_of(con, D(3))) == 1
    assert not any(p.quality_checks(con).values())


def test_duplicate_key_in_snapshot_rejected(tmp_path):
    f = tmp_path / "s.csv"
    f.write_text("customer_id,name,city,plan\n1,A,X,free\n1,B,Y,pro\n")
    with pytest.raises(p.SnapshotError, match="duplicate"):
        p.read_snapshot(f)


def test_quality_checks_detect_corruption():
    con = p.connect()
    p.load_snapshot(con, D(1), [(1, "A", "X", "free")])
    con.execute("INSERT INTO dim_customer SELECT 99, customer_id, name, city, plan, row_hash, DATE '2026-01-01', "
                "DATE '9999-12-31', TRUE FROM dim_customer")
    checks = p.quality_checks(con)
    assert checks["multiple_current_rows_per_key"] == 1
    assert checks["overlapping_intervals"] == 1


def test_as_of_matches_raw_snapshots(tmp_path):
    """Property check: point-in-time view must equal each raw snapshot, every day."""
    paths = generate(tmp_path, days=15, customers=40, seed=3)
    con = p.connect()
    p.run_directory(con, tmp_path)
    for path in paths:
        d = date.fromisoformat(path.stem.removeprefix("snapshot_"))
        assert p.as_of(con, d) == sorted(p.read_snapshot(path)), d
    assert not any(p.quality_checks(con).values())


def test_generator_is_deterministic(tmp_path):
    a = generate(tmp_path / "a", days=5, seed=1)
    b = generate(tmp_path / "b", days=5, seed=1)
    assert all(x.read_text() == y.read_text() for x, y in zip(a, b))
