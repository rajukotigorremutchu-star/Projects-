"""Report generation: deterministic mock backend + optional Anthropic backend."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from .logparse import LEVELS, Signature, extract_signatures
from .retriever import BM25

MIN_SCORE = 1.0  # below this, we say "no matching runbook" rather than guess


@dataclass
class Finding:
    signature: Signature
    runbook_id: str | None
    runbook_title: str | None
    score: float
    steps: list[str] = field(default_factory=list)


def steps_from(text: str, limit: int = 4) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r"^\s*(?:\d+\.|-)\s+(.*)$", text, re.M)][:limit]


def mitigation_steps(index: BM25, runbook_id: str) -> list[str]:
    for c in index.chunks:
        if c.runbook_id == runbook_id and c.section.lower() == "mitigation":
            return steps_from(c.text)
    return []


def build_findings(lines: list[str], index: BM25, top_signatures: int = 5) -> list[Finding]:
    findings = []
    for sig in extract_signatures(lines)[:top_signatures]:
        hits = index.search_runbooks(f"{sig.service} {sig.text}", k=1)
        if hits and hits[0][1] >= MIN_SCORE:
            chunk, score = hits[0]
            findings.append(Finding(sig, chunk.runbook_id, chunk.title, score, mitigation_steps(index, chunk.runbook_id)))
        else:
            findings.append(Finding(sig, None, None, hits[0][1] if hits else 0.0))
    return findings


def severity(findings: list[Finding]) -> str:
    if not findings:
        return "none"
    top = max(LEVELS[f.signature.level] for f in findings)
    total = sum(f.signature.count for f in findings)
    if top >= LEVELS["CRITICAL"]:
        return "SEV1"
    if top >= LEVELS["ERROR"] and total >= 10:
        return "SEV2"
    return "SEV3"


def mock_report(findings: list[Finding]) -> str:
    """Deterministic, extractive report. Never invents steps that are not in a runbook."""
    if not findings:
        return "No WARN+ log lines found. Nothing to triage."
    out = [f"Severity: {severity(findings)}", ""]
    for i, f in enumerate(findings, 1):
        s = f.signature
        out.append(f"{i}. [{s.level}] {s.service}: {s.text} (x{s.count})")
        if f.runbook_id:
            out.append(f"   Runbook: {f.runbook_title} ({f.runbook_id}, score {f.score:.2f})")
            out += [f"   - {st}" for st in f.steps]
        else:
            out.append("   Runbook: none matched - escalate to on-call for manual investigation.")
    return "\n".join(out)


def llm_report(findings: list[Finding], model: str | None = None) -> str:
    """Optional: ask Claude to write the summary, grounded in the retrieved steps only."""
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("pip install -r requirements-llm.txt to use --backend anthropic") from e
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    payload = [
        {"signature": f.signature.text, "service": f.signature.service, "level": f.signature.level,
         "count": f.signature.count, "runbook": f.runbook_title, "steps": f.steps}
        for f in findings
    ]
    prompt = (
        "You are an on-call assistant. Summarize these incident findings for a human. "
        "Use ONLY the runbook steps provided; if runbook is null say escalation is needed. "
        "Do not invent commands.\n\n" + json.dumps(payload, indent=2)
    )
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model or os.environ.get("TRIAGE_MODEL", "claude-sonnet-5-5"),
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text  # pragma: no cover
