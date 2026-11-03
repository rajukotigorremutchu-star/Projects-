"""Evaluate every sample flag for every sample context; print/compare JSON.

    python scripts/run_sample.py            # print
    python scripts/run_sample.py --check    # compare against samples/expected_output.json
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flagsvc.evaluator import evaluate  # noqa: E402
from flagsvc.models import Flag  # noqa: E402


def run() -> dict:
    flags = json.loads((ROOT / "samples/flags.json").read_text())
    contexts = json.loads((ROOT / "samples/contexts.json").read_text())
    out = {}
    for ctx in contexts:
        out[ctx["user_id"]] = {
            k: evaluate(Flag(key=k, **d), ctx).model_dump(exclude={"key"})
            for k, d in sorted(flags.items())}
    return out


if __name__ == "__main__":
    result = run()
    if "--check" in sys.argv:
        expected = json.loads((ROOT / "samples/expected_output.json").read_text())
        ok = result == expected
        print("sample output matches expected" if ok else "MISMATCH")
        sys.exit(0 if ok else 1)
    print(json.dumps(result, indent=2, sort_keys=True))
