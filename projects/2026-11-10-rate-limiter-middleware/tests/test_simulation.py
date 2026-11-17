import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_simulation_matches_expected_sample():
    out = subprocess.run([sys.executable, "scripts/simulate.py"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout
    assert json.loads(out) == json.loads((ROOT / "samples/expected_simulation.json").read_text())
