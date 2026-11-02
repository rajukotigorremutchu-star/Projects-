import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from sessionizer.pipeline import Sessionizer
from sessionizer.generate import generate_batches
from sessionizer.cli import load_jsonl


def ev(i, u, t):
    return {"event_id": i, "user_id": u, "event_time": t}


def test_gap_splits_sessions():
    s = Sessionizer(gap_s=100, allowed_lateness_s=0)
    s.process_batch("b", [ev("a", "u", 0), ev("b", "u", 100), ev("c", "u", 201)])
    s.flush()
    assert s.sessions() == [("u", 0, 100, 2, 100), ("u", 201, 201, 1, 0)]


def test_out_of_order_within_lateness_merges():
    s = Sessionizer(gap_s=100, allowed_lateness_s=500)
    s.process_batch("b1", [ev("a", "u", 0), ev("c", "u", 200)])
    s.process_batch("b2", [ev("b", "u", 100)])  # bridges the two
    s.flush()
    assert s.sessions() == [("u", 0, 200, 3, 200)]


def test_late_event_dead_lettered():
    s = Sessionizer(gap_s=100, allowed_lateness_s=50)
    s.process_batch("b1", [ev("a", "u", 1000)])
    r = s.process_batch("b2", [ev("x", "u", 900)])  # watermark 950
    assert r.late == 1 and r.accepted == 0
    assert ("b2", "x", "late") in s.dead_letters()


def test_duplicate_and_invalid():
    s = Sessionizer()
    r = s.process_batch("b", [ev("a", "u", 1), ev("a", "u", 1), {"user_id": "u", "event_time": 2},
                              ev("z", "u", "bad")])
    assert (r.accepted, r.duplicates, r.invalid) == (1, 1, 2)


def test_duplicate_across_batches_after_finalization():
    s = Sessionizer(gap_s=10, allowed_lateness_s=0)
    s.process_batch("b1", [ev("a", "u", 0)])
    s.process_batch("b2", [ev("b", "u", 1000)])
    assert s.sessions() == [("u", 0, 0, 1, 0)]
    r = s.process_batch("b3", [ev("a", "u", 0)])
    assert r.duplicates == 1


def test_finalized_only_after_watermark():
    s = Sessionizer(gap_s=100, allowed_lateness_s=0)
    r = s.process_batch("b1", [ev("a", "u", 0)])
    assert r.sessions_finalized == 0
    r = s.process_batch("b2", [ev("b", "u", 100)])  # joins session, watermark 100 !> 0+100
    assert r.sessions_finalized == 0
    r = s.process_batch("b3", [ev("c", "u", 201)])  # watermark 201 > 100+100
    assert r.sessions_finalized == 1


def test_batch_replay_is_idempotent():
    s = Sessionizer(gap_s=10, allowed_lateness_s=0)
    batch = [ev("a", "u", 0), ev("b", "u", 5)]
    s.process_batch("b1", batch)
    r = s.process_batch("b1", batch)
    assert r.skipped
    s.flush()
    assert s.sessions() == [("u", 0, 5, 2, 5)]
    assert s.dead_letters() == []


def test_state_survives_restart(tmp_path):
    db = str(tmp_path / "s.db")
    a = Sessionizer(db, gap_s=100, allowed_lateness_s=0)
    a.process_batch("b1", [ev("a", "u", 0)])
    a.conn.close()
    b = Sessionizer(db, gap_s=100, allowed_lateness_s=0)
    b.process_batch("b2", [ev("b", "u", 50)])
    b.flush()
    assert b.sessions() == [("u", 0, 50, 2, 50)]


def test_synthetic_run_accounts_for_every_event():
    batches = generate_batches(seed=7)
    total = sum(len(b) for b in batches)
    s = Sessionizer(gap_s=1800, allowed_lateness_s=600)
    acc = 0
    for i, b in enumerate(batches):
        r = s.process_batch(f"b{i}", b)
        acc += r.accepted
        assert r.accepted + r.duplicates + r.late + r.invalid == len(b)
    s.flush()
    assert sum(x[3] for x in s.sessions()) == acc
    assert acc < total
    reasons = {d[2] for d in s.dead_letters()}
    assert {"duplicate", "invalid", "late"} <= reasons


def test_generator_is_deterministic():
    assert generate_batches(3) == generate_batches(3)


def test_sample_file_matches_expected():
    d = pathlib.Path(__file__).resolve().parents[1] / "data"
    s = Sessionizer(gap_s=100, allowed_lateness_s=50)
    for bid, evs in load_jsonl(str(d / "sample_events.jsonl")):
        s.process_batch(bid, evs)
    s.flush()
    got = [list(r) for r in s.sessions()]
    import json
    assert got == json.loads((d / "expected_sessions.json").read_text())
