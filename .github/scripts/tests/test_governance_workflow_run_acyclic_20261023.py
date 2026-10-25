"""Prevent GitHub workflow_run cycles without weakening exact-head QA/Release.

Skipped workflows still complete and emit workflow_run:completed. Job-level
conditional guards cannot break a cyclic completion-trigger graph.
"""
from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = {
    "wow-v17-independent-qa-check": ".github/workflows/wow-v17-independent-qa-check.yml",
    "wow-v17-existing-pr-governance-review": ".github/workflows/wow-v17-existing-pr-governance-review.yml",
    "wow-v17-trusted-governance-gate": ".github/workflows/wow-v17-trusted-governance-gate.yml",
    "wow-v17-release-authority-check": ".github/workflows/wow-v17-release-authority-check.yml",
}

def read(name: str) -> str:
    return (ROOT / WORKFLOWS[name]).read_text(encoding="utf-8")

def completed_run_sources(name: str) -> set[str]:
    body = read(name)
    head = body.split("\npermissions:", 1)[0]
    match = re.search(r"(?m)^\s+workflows:\s*\[([^\]\n]*)\]", head)
    assert match is not None, f"Missing or changed workflow_run trigger: {name}"
    return {w.strip() for w in match.group(1).split(",") if w.strip()}

def test_no_workflow_run_cycle_can_retrigger_qa_from_governance():
    edges: dict[str, set[str]] = {n: set() for n in WORKFLOWS}
    for consumer in WORKFLOWS:
        for source in completed_run_sources(consumer):
            edges.setdefault(source, set()).add(consumer)
    seen: set[str] = set()
    active: set[str] = set()
    def visit(name: str) -> None:
        assert name not in active, f"Workflow completion cycle involving {name}"
        if name in seen:
            return
        active.add(name)
        for target in edges.get(name, ()):
            visit(target)
        active.remove(name)
        seen.add(name)
    for node in edges:
        visit(node)

def test_qa_still_requires_original_independent_regression_triggers():
    sources = completed_run_sources("wow-v17-independent-qa-check")
    assert sources == {"wow-verify", "wow-engine-verify", "wow-v17-rapid-repair", "wow-v17-change-impact-gate"}
    assert "workflow_dispatch:" in read("wow-v17-independent-qa-check")

def test_certifier_and_trusted_gate_preserve_exact_lineage():
    assert completed_run_sources("wow-v17-existing-pr-governance-review") == {"wow-v17-independent-qa-check"}
    assert completed_run_sources("wow-v17-trusted-governance-gate") == {"wow-v17-existing-pr-governance-review"}
    assert completed_run_sources("wow-v17-release-authority-check") == {"wow-v17-independent-qa-check"}
    assert "pull_request_target:" in read("wow-v17-existing-pr-governance-review")
    assert "pull_request_target:" in read("wow-v17-trusted-governance-gate")
    for name in WORKFLOWS:
        body = read(name)
        assert "permissions:" in body
        assert "can_execute=true" not in body
