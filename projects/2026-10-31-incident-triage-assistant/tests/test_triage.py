from pathlib import Path

import pytest

from triage.cli import main
from triage.evaluate import evaluate, load_cases, random_baseline
from triage.logparse import extract_signatures, normalize, parse_line
from triage.report import build_findings, mock_report, severity
from triage.retriever import BM25, load_runbooks, tokenize

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def index():
    return BM25(load_runbooks(ROOT / "data" / "runbooks"))


def test_parse_line_variants():
    p = parse_line("2026-10-31T02:14:01Z ERROR [svc] boom")
    assert (p.level, p.service, p.message) == ("ERROR", "svc", "boom")
    assert parse_line("2026-10-31 02:14:01,123 warning no service here").service == "unknown"
    assert parse_line("garbage") is None
    assert parse_line("2026-10-31T02:14:01Z VERBOSE [s] x") is None


def test_normalize_masks_variable_parts():
    out = normalize("conn 10.0.0.1:5432 failed id 123e4567-e89b-12d3-a456-426614174000 after 30 ms at /var/x.log")
    assert out == "conn <ip> failed id <uuid> after <n> ms at <path>"


def test_signatures_group_filter_and_order():
    lines = [
        "2026-10-31T00:00:00Z INFO [a] ok 1",
        "2026-10-31T00:00:01Z WARN [a] retry 1 of 3",
        "2026-10-31T00:00:02Z WARN [a] retry 2 of 3",
        "2026-10-31T00:00:03Z ERROR [b] bad thing 7",
        "not a log",
    ]
    sigs = extract_signatures(lines)
    assert [(s.level, s.count) for s in sigs] == [("ERROR", 1), ("WARN", 2)]
    assert sigs[1].text == "retry <n> of <n>"


def test_tokenize_drops_placeholders_and_stopwords():
    assert tokenize("The pool <n> failed on 10 hosts") == ["pool", "failed", "hosts"]


def test_retrieval_ranks_expected_runbook(index):
    top = index.search_runbooks("write failed no space left on device", k=1)
    assert top[0][0].runbook_id == "disk-full"
    assert index.search_runbooks("zzzz qqqq", k=3) == []


def test_findings_include_mitigation_steps(index):
    lines = ["2026-10-31T00:00:00Z ERROR [x] x509: certificate has expired"] * 3
    f = build_findings(lines, index)
    assert f[0].runbook_id == "tls-cert-expired"
    assert len(f[0].steps) == 4 and "openssl" in f[0].steps[0]


def test_unmatched_signature_escalates(index):
    f = build_findings(["2026-10-31T00:00:00Z ERROR [x] purple unicorn"], index)
    assert f[0].runbook_id is None
    assert "escalate" in mock_report(f)


def test_severity_rules(index):
    crit = build_findings(["2026-10-31T00:00:00Z CRITICAL [x] disk is full"], index)
    assert severity(crit) == "SEV1"
    errs = build_findings(["2026-10-31T00:00:00Z ERROR [x] HTTP 429"] * 10, index)
    assert severity(errs) == "SEV2"
    warn = build_findings(["2026-10-31T00:00:00Z WARN [x] HTTP 429"], index)
    assert severity(warn) == "SEV3"
    assert severity([]) == "none"
    assert "Nothing to triage" in mock_report([])


def test_eval_metrics_quality_floor(index):
    cases = load_cases(ROOT / "data" / "eval_cases.json")
    res = evaluate(cases, index)
    assert res["positives"] == 24 and res["negatives"] == 2
    assert res["hit@3"] >= 0.9 and res["hit@1"] >= 0.85
    assert res["hit@1"] > 3 * random_baseline(cases, sorted({c.runbook_id for c in index.chunks}))


def test_eval_on_tiny_case(index):
    cases = [{"id": "a", "expected_runbook": "dns-resolution",
              "logs": ["2026-10-31T00:00:00Z ERROR [s] lookup foo: no such host"]},
             {"id": "b", "expected_runbook": None, "logs": ["2026-10-31T00:00:00Z ERROR [s] zzzz"]}]
    res = evaluate(cases, index)
    assert res["hit@1"] == 1.0 and res["mrr"] == 1.0 and res["abstain_accuracy"] == 1.0


def test_cli_report_matches_expected_output(capsys):
    assert main(["report", str(ROOT / "samples" / "incident_db.log")]) == 0
    out = capsys.readouterr().out
    assert out == (ROOT / "samples" / "expected_report.txt").read_text()


def test_anthropic_backend_requires_key(monkeypatch, index):
    from triage.report import llm_report
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    pytest.importorskip("anthropic")
    with pytest.raises(RuntimeError):
        llm_report([])
