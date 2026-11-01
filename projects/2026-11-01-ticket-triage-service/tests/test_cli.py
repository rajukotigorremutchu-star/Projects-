import json

from triage.cli import main


def test_end_to_end_cli(tmp_path, capsys):
    raw, db, mdl, rep = (str(tmp_path / n) for n in ("r.csv", "t.db", "m.joblib", "rep.json"))
    assert main(["generate", "--n", "600", "--out", raw]) == 0
    assert main(["etl", "--raw", raw, "--db", db]) == 0
    assert main(["train", "--db", db, "--model", mdl, "--report", rep]) == 0
    report = json.loads(open(rep).read())
    assert set(report) == {"random_split", "group_split"}
    capsys.readouterr()
    assert main(["predict", "--model", mdl, "Please add dark mode to the reports view"]) == 0
    assert json.loads(capsys.readouterr().out)["label"] == "feature_request"
