"""Fail-closed gates inside checksPass-isolated optional observers (#2742 follow-up).

#2742 made these jobs' steps ``continue-on-error`` so optional observers never
fail the protected-main commit. Isolation must not let them act on a failed
governance assertion, or keep a drift-failed post-deploy run recorded as
``ACCEPTANCE_LANES_PASSED`` (which ``post_deploy_dedupe`` would trust).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WF = ROOT / ".github" / "workflows"
ORCH = WF / "wow-v17-post-deploy-verification-orchestrator.yml"
WITHDRAW = "Withdraw acceptance unless the final identity readback is proven"


def _steps(name: str, job: str) -> list[dict]:
    return yaml.load((WF / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)["jobs"][job]["steps"]


@pytest.mark.parametrize("workflow, job, gate", [
    ("wow-v17-first-six-transport-rescue.yml", "rescue",
     "steps.checkout.outcome == 'success' && steps.python.outcome == 'success' && "
     "steps.deps.outcome == 'success' && steps.gov.outcome == 'success'"),
    ("wow-v17-basketball-model-maintenance.yml", "maintain", "steps.gov.outcome == 'success'"),
    ("wow-v17-first-six-model-maintenance.yml", "maintain", "steps.gov.outcome == 'success'"),
])
def test_governed_work_never_runs_after_a_failed_governance_assertion(workflow, job, gate):
    steps = _steps(workflow, job)
    by_id = {s["id"]: s for s in steps if "id" in s}
    assert by_id["gov"]["name"] == "Assert non-execution governance and OIDC context"
    assert by_id["work"]["if"] == gate
    gov_index = steps.index(by_id["gov"])
    assert steps.index(by_id["work"]) > gov_index
    record = next(s for s in steps if s.get("name") == "Record authentic optional-observer outcome")
    assert record["env"]["GOV_OUTCOME"] == "${{ steps.gov.outcome }}"
    assert "_GOVERNANCE_ASSERTION_FAILED" in record["run"]
    # The governance failure is checked before any PASS label can be printed.
    assert record["run"].index("GOV_OUTCOME\" != \"success\"") < record["run"].index("_PASS")


def _withdraw_step() -> tuple[dict, list[dict]]:
    steps = yaml.load(ORCH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)["jobs"]
    for job in steps.values():
        for step in job.get("steps", []):
            if step.get("name") == WITHDRAW:
                return step, job["steps"]
    raise AssertionError("withdraw step missing")


def test_withdraw_runs_after_final_readback_and_before_the_dedupe_upload():
    step, steps = _withdraw_step()
    names = [s.get("name") for s in steps]
    assert names.index("Reject main or deployment changes during acceptance") < names.index(WITHDRAW)
    assert names.index(WITHDRAW) < names.index("Preserve authentic acceptance outcome for dedupe")
    assert step["if"] == "always()"
    ids = {s.get("id") for s in steps}
    assert {"final-checkout", "final-sha", "final-readback"} <= ids


def _run_withdraw(tmp_path: Path, outcome: dict | str | None, **outcomes: str) -> dict | None:
    if shutil.which("jq") is None:
        pytest.skip("jq not installed")
    step, _ = _withdraw_step()
    target = tmp_path / "wow-v17-post-deploy-acceptance-outcome.json"
    if outcome is not None:
        target.write_text(outcome if isinstance(outcome, str) else json.dumps(outcome), encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
           "FINAL_CHECKOUT_OUTCOME": outcomes.get("checkout", "success"),
           "FINAL_SHA_OUTCOME": outcomes.get("sha", "success"),
           "FINAL_READBACK_OUTCOME": outcomes.get("readback", "success")}
    subprocess.run(["bash", "-c", step["run"]], cwd=tmp_path, env=env, check=False)
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"_raw": target.read_text(encoding="utf-8")}


PASSED = {"status": "ACCEPTANCE_LANES_PASSED", "can_execute": False, "production_verified": False}


def test_proven_final_identity_keeps_an_authentic_pass(tmp_path):
    assert _run_withdraw(tmp_path, PASSED)["status"] == "ACCEPTANCE_LANES_PASSED"


@pytest.mark.parametrize("failed", ["checkout", "sha", "readback"])
@pytest.mark.parametrize("outcome", ["failure", "skipped", "cancelled", ""])
def test_unproven_final_identity_withdraws_pass(tmp_path, failed, outcome):
    result = _run_withdraw(tmp_path, PASSED, **{failed: outcome})
    assert result is not None and result["status"] == "ACCEPTANCE_LANES_FAILED"
    assert result["final_identity_readback"] == "NOT_PROVEN" and result["can_execute"] is False
    assert "ACCEPTANCE_WITHDRAWN_FINAL_IDENTITY_NOT_PROVEN" in (tmp_path / "summary.md").read_text()


def test_non_pass_outcomes_and_missing_files_are_untouched(tmp_path):
    failed = {"status": "ACCEPTANCE_LANES_FAILED", "can_execute": False}
    assert _run_withdraw(tmp_path, failed, readback="failure") == failed
    empty = tmp_path / "no-outcome"
    empty.mkdir()
    assert _run_withdraw(empty, None, readback="failure") is None


def test_unreadable_outcome_is_never_upgraded(tmp_path):
    result = _run_withdraw(tmp_path, "{not json", readback="failure")
    assert result is None or result.get("status") != "ACCEPTANCE_LANES_PASSED"
