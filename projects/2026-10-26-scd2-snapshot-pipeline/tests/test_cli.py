from pathlib import Path

from scd2.cli import main

ROOT = Path(__file__).resolve().parent.parent


def test_cli_load_sample_exports_expected(tmp_path, capsys):
    out = tmp_path / "dim.csv"
    assert main(["load", str(ROOT / "data/sample"), "--export", str(out), "--as-of", "2026-01-03"]) == 0
    assert out.read_text() == (ROOT / "data/expected_dim_customer.csv").read_text()
    assert "as of 2026-01-03: 3 active customers" in capsys.readouterr().out


def test_cli_generate_then_load(tmp_path):
    snaps = tmp_path / "s"
    assert main(["generate", str(snaps), "--days", "4", "--customers", "10"]) == 0
    assert len(list(snaps.glob("snapshot_*.csv"))) == 4
    assert main(["load", str(snaps), "--db", str(tmp_path / "w.duckdb")]) == 0
