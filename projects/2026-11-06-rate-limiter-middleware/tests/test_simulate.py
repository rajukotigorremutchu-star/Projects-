import json
from pathlib import Path

from simulate import load, simulate

ROOT = Path(__file__).resolve().parents[1]


def test_sample_trace_matches_expected_output():
    got = simulate(load(ROOT / "samples/trace.csv"), 5, 10)
    assert got == json.loads((ROOT / "samples/expected_output.json").read_text())
