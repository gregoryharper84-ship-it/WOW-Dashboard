"""#2136: no green skipped writer when an actionable source was selected."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from v17 import engineering_adoption_progression as progression

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github/workflows/wow-v17-engineering-auto-repair.yml"
SHA = "a" * 40


@pytest.mark.parametrize("args,outcome", [
    (("skipped", "", "skipped", "", ""), "INAPPLICABLE_EVENT"),
    (("success", "NO_ELIGIBLE_SOURCE", "skipped", "", ""), "NO_ELIGIBLE_SOURCE"),
    (("success", "SOURCE_READY", "success", "2158", SHA), "APP_PROPOSAL_REVIEW_PENDING"),
])
def test_progression_records_truthful_state(args, outcome):
    result = progression.classify(
        preflight_result=args[0], selection_status=args[1],
        writer_result=args[2], source_pr=args[3], source_sha=args[4],
    )
    assert result == {"status": outcome, "writer_result": args[2],
                      "can_execute": False}


@pytest.mark.parametrize("args,reason", [
    (("success", "SOURCE_READY", "skipped", "2158", SHA),
     "ADOPTION_WRITER_SKIPPED_WITH_ACTIONABLE_BACKLOG"),
    (("success", "SOURCE_READY", "failure", "2158", SHA),
     "ADOPTION_WRITER_SKIPPED_WITH_ACTIONABLE_BACKLOG"),
    (("success", "SOURCE_READY", "cancelled", "2158", SHA),
     "ADOPTION_WRITER_SKIPPED_WITH_ACTIONABLE_BACKLOG"),
    (("failure", "", "skipped", "", ""),
     "ADOPTION_PREFLIGHT_UNVERIFIED"),
    (("cancelled", "", "skipped", "", ""),
     "ADOPTION_PREFLIGHT_UNVERIFIED"),
    (("success", "", "skipped", "", ""),
     "ADOPTION_SELECTION_STATUS_UNVERIFIED"),
    (("success", "SOURCE_READY", "success", "", SHA),
     "ADOPTION_ACTIONABLE_SOURCE_IDENTITY_INVALID"),
    (("success", "SOURCE_READY", "success", "2158", "bad"),
     "ADOPTION_ACTIONABLE_SOURCE_IDENTITY_INVALID"),
    (("success", "NO_ELIGIBLE_SOURCE", "success", "", ""),
     "ADOPTION_EMPTY_QUEUE_WRITER_INCONSISTENT"),
    (("skipped", "SOURCE_READY", "skipped", "2158", SHA),
     "ADOPTION_INAPPLICABLE_EVENT_INCONSISTENT"),
])
def test_progression_fails_closed_on_broken_transitions(args, reason):
    with pytest.raises(progression.ProgressionHold, match=reason):
        progression.classify(
            preflight_result=args[0], selection_status=args[1],
            writer_result=args[2], source_pr=args[3], source_sha=args[4],
        )


def test_progression_reuses_current_workflow_without_parallel_writer_or_new_trigger():
    wf = yaml.safe_load(WF.read_text(encoding="utf-8"))
    jobs = wf["jobs"]
    assert "adoption-readonly-preflight" in jobs
    assert "adopt-reviewed-owner-source" in jobs
    v = jobs["verify-adoption-progression"]
    assert v["needs"] == ["adoption-readonly-preflight", "adopt-reviewed-owner-source"]
    assert "always()" in v["if"]
    assert "concurrency" not in v
    assert "environment" not in v
    assert v["permissions"] == {"contents": "read"}
    script = v["steps"][-1]["run"]
    assert "engineering_adoption_progression.py" in script
    assert ".can_execute == false" in script
    assert "APP_PROPOSAL_REVIEW_PENDING" in script
    assert wf["jobs"]["adopt-reviewed-owner-source"]["concurrency"]["group"] == (
        "wow-v17-engineering-implementation-single-writer"
    )
    triggers = wf.get("on", wf.get(True))
    assert "workflow_dispatch" in triggers
    assert set(triggers["workflow_dispatch"]["inputs"]) == {"source_pr", "source_sha"}


def test_progression_has_no_external_mutation_tools_or_execution_authority():
    code = (ROOT / "artifacts/wow-engine/v17/engineering_adoption_progression.py").read_text()
    assert "subprocess" not in code
    assert "requests" not in code
    assert "github.token" not in code
    assert "can_execute\": False" in code
