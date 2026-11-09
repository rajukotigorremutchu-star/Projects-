import json
import random
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from tablediff import DiffError, diff_tables, load_csv

ROOT = Path(__file__).resolve().parent.parent


def make(rows_s, rows_t, cols=("id", "a", "b")):
    con = duckdb.connect()
    for name, rows in (("s", rows_s), ("t", rows_t)):
        con.execute(f"CREATE TABLE {name} ({', '.join(c + ' VARCHAR' for c in cols)})")
        for r in rows:
            con.execute(f"INSERT INTO {name} VALUES ({','.join('?' * len(cols))})", [None if v is None else str(v) for v in r])
    return con


def test_identical_tables_pruned_without_detail():
    rows = [(i, f"x{i}", i * 2) for i in range(1, 500)]
    r = diff_tables(make(rows, rows), "s", "t", "id")
    assert r.identical and r.segments_checked == 1 and r.rows_detail_compared == 0


def test_detects_missing_extra_changed():
    s = [(i, "a", "b") for i in range(1, 101)]
    t = [r for r in s if r[0] != 5] + [(500, "a", "b")]
    t[10] = (t[10][0], "CHANGED", "b")
    r = diff_tables(make(s, t), "s", "t", "id", threshold=8)
    assert r.missing_in_target == [5]
    assert r.extra_in_target == [500]
    assert [c["key"] for c in r.changed] == [t[10][0]]
    assert r.changed[0]["columns"] == {"a": {"source": "a", "target": "CHANGED"}}


def test_bisection_reads_fewer_rows_than_full_join():
    s = [(i, "a", "b") for i in range(1, 5001)]
    t = [list(x) for x in s]
    t[4000][1] = "z"
    r = diff_tables(make(s, [tuple(x) for x in t]), "s", "t", "id", threshold=32)
    assert len(r.changed) == 1
    assert r.rows_detail_compared <= 64 < 5000


def test_null_vs_empty_vs_literal_marker_distinguished():
    s = [(1, None, "x"), (2, "", "x"), (3, "NULL", "x")]
    t = [(1, "", "x"), (2, None, "x"), (3, None, "x")]
    r = diff_tables(make(s, t), "s", "t", "id", numeric_normalize=False, trim=False)
    # DuckDB INSERT keeps '' distinct from NULL here
    assert {c["key"] for c in r.changed} == {1, 2, 3}


def test_numeric_and_whitespace_normalisation():
    s = [(1, "12.30", " hi "), (2, "1e3", "x")]
    t = [(1, "12.3000", "hi"), (2, "1000", "x")]
    assert diff_tables(make(s, t), "s", "t", "id").identical
    r = diff_tables(make(s, t), "s", "t", "id", numeric_normalize=False, trim=False)
    assert len(r.changed) == 2


def test_precision_boundary():
    s, t = [(1, "1.0000004", "x")], [(1, "1.0000001", "x")]
    assert diff_tables(make(s, t), "s", "t", "id", precision=6).identical
    assert not diff_tables(make(s, t), "s", "t", "id", precision=8).identical


def test_ignore_and_schema_drift():
    con = duckdb.connect()
    con.execute("CREATE TABLE s AS SELECT '1' id, 'x' a, 'old' ts, 'only_s' c")
    con.execute("CREATE TABLE t AS SELECT '1' id, 'x' a, 'new' ts, 'only_t' d")
    r = diff_tables(con, "s", "t", "id", ignore=["ts"])
    assert r.identical and r.compared_columns == ["a"]
    assert r.columns_only_in_source == ["c"] and r.columns_only_in_target == ["d"]


def test_negative_and_sparse_keys():
    s = [(-50, "a", "b"), (0, "a", "b"), (10**12, "a", "b")]
    t = [(-50, "a", "b"), (0, "CHG", "b"), (10**12, "a", "b")]
    r = diff_tables(make(s, t), "s", "t", "id", threshold=1)
    assert [c["key"] for c in r.changed] == [0]


def test_empty_tables():
    r = diff_tables(make([], []), "s", "t", "id")
    assert r.identical and r.source_rows == 0
    r = diff_tables(make([(1, "a", "b")], []), "s", "t", "id")
    assert r.missing_in_target == [1]


@pytest.mark.parametrize("rows,msg", [
    ([(1, "a", "b"), (1, "c", "d")], "duplicated"),
    ([("x", "a", "b")], "not integers"),
    ([(None, "a", "b")], "not integers"),
])
def test_key_validation(rows, msg):
    with pytest.raises(DiffError, match=msg):
        diff_tables(make(rows, rows), "s", "t", "id")


def test_missing_key_column():
    with pytest.raises(DiffError, match="not found"):
        diff_tables(make([], []), "s", "t", "nope")


def test_randomised_matches_naive_oracle():
    rng = random.Random(7)
    for trial in range(25):
        n = rng.randint(0, 300)
        keys = rng.sample(range(-100, 1000), n)
        s = {k: (rng.choice("abc"), rng.randint(0, 5)) for k in keys}
        t = {}
        for k, v in s.items():
            roll = rng.random()
            if roll < 0.05:
                continue
            t[k] = (rng.choice("xyz"), v[1]) if roll < 0.12 else v
        for k in rng.sample(range(2000, 2100), rng.randint(0, 4)):
            t[k] = ("a", 1)
        r = diff_tables(make([(k, *v) for k, v in s.items()], [(k, *v) for k, v in t.items()]),
                        "s", "t", "id", threshold=rng.choice([1, 4, 32]))
        assert sorted(r.missing_in_target) == sorted(set(s) - set(t))
        assert sorted(r.extra_in_target) == sorted(set(t) - set(s))
        assert sorted(c["key"] for c in r.changed) == sorted(k for k in s if k in t and s[k] != t[k])


def test_sample_data_matches_expected_report(tmp_path):
    out = tmp_path / "r.json"
    p = subprocess.run(
        [sys.executable, "-m", "tablediff", "data/source.csv", "data/target.csv", "--key", "order_id",
         "--ignore", "updated_at", "--threshold", "16", "--out", str(out)],
        cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 1  # differences found
    assert json.loads(out.read_text()) == json.loads((ROOT / "data/expected_report.json").read_text())


def test_cli_exit_codes(tmp_path):
    (tmp_path / "a.csv").write_text("id,v\n1,x\n")
    (tmp_path / "b.csv").write_text("id,v\n1,x\n")
    (tmp_path / "c.csv").write_text("id,v\n1,x\n1,y\n")
    run = lambda *a: subprocess.run([sys.executable, "-m", "tablediff", *a], cwd=ROOT, capture_output=True, text=True).returncode
    assert run(str(tmp_path / "a.csv"), str(tmp_path / "b.csv"), "--key", "id") == 0
    assert run(str(tmp_path / "a.csv"), str(tmp_path / "c.csv"), "--key", "id") == 2
