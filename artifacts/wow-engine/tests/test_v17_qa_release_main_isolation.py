"""Advisory QA/Release callbacks must not fail the protected-main commit.

GitHub attaches workflow_run jobs to the ``main`` tip. On 2026-11-20 the QA and
Release callbacks left 27 + 3 failing jobs on ``e9d7b057`` and 112 + 29 on
``ddb91e1c`` even though each already published its typed HOLD (or nothing) on
the PR head. These pins keep the fix honest:

* only automatic (workflow_run) runs are isolated; manual dispatch still fails;
* step-level continue-on-error (job-level does not keep the check green);
* every later non-always step carries the fail-fast guard, so a failed step
  still skips everything after it exactly as before;
* the terminal decision still reads step ``outcome`` and publishes the HOLD;
* a failed check publish is never isolated.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
QA = ROOT / ".github/workflows/wow-v17-independent-qa-check.yml"
REL = ROOT / ".github/workflows/wow-v17-release-authority-check.yml"
AUTO = "${{ github.event_name == 'workflow_run' }}"
GUARD = "!contains(steps.*.outcome, 'failure')"
EVALUATION = ("policy", "policy_receipt", "policy_artifact", "review_credential", "preflight",
              "capture", "review", "recheck", "source_review", "source_review_artifact",
              "reviewer_app", "reviewer_attest", "reviewer_verify", "decide")


def _steps(path: Path, job: str) -> list[dict]:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)["jobs"][job]["steps"]


def test_only_automatic_qa_runs_are_isolated_and_only_at_step_level():
    workflow = yaml.load(QA.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert "continue-on-error" not in workflow["jobs"]["qa"]
    by_id = {s["id"]: s for s in workflow["jobs"]["qa"]["steps"] if "id" in s}
    for sid in ("target", *EVALUATION):
        assert by_id[sid].get("continue-on-error") == AUTO, sid


def test_every_later_non_always_qa_step_keeps_fail_fast():
    steps = _steps(QA, "qa")
    start = next(i for i, s in enumerate(steps) if s.get("id") == "target")
    for step in steps[start + 1:]:
        condition = step.get("if", "")
        if "always()" in condition:
            continue
        assert GUARD in condition, step["name"]


def test_identity_contract_and_publish_failures_are_never_isolated():
    by_name = {s["name"]: s for s in _steps(QA, "qa")}
    for name in ("Check Gemini review bridge contract", "Require this role's App identity",
                 "Mint checks-only App token", "Publish exact-head check"):
        assert "continue-on-error" not in by_name[name], name
    publish = by_name["Publish exact-head check"]
    assert "exit 1" in publish["run"] and "QA_CHECK_PUBLISH_FAILED" in publish["run"]


def test_terminal_decision_reads_pre_isolation_outcomes_and_still_holds():
    terminal = next(s for s in _steps(QA, "qa") if s.get("id") == "terminal_decision")
    assert terminal["if"].startswith("always()")
    env = terminal["env"]
    for key in ("POLICY_OUTCOME", "REVIEW_CREDENTIAL_OUTCOME", "CAPTURE_OUTCOME", "REVIEW_OUTCOME",
                "RECHECK_OUTCOME", "SOURCE_REVIEW_OUTCOME", "DECIDE_OUTCOME"):
        assert env[key].endswith(".outcome }}"), key  # .conclusion would read the isolated success
    assert "QA_REVIEW_EXECUTION_FAILED" in terminal["run"]


def test_qa_draft_and_unresolved_target_are_explicit_not_silent():
    by_name = {s["name"]: s for s in _steps(QA, "qa")}
    resolve = by_name["Resolve one exact-head QA target"]["run"]
    assert "QA_SOURCE_PR_DRAFT" in resolve and "--neutral-superseded" in resolve
    warn = by_name["Preserve automatic QA target resolution HOLD"]
    assert warn["if"] == ("always() && github.event_name == 'workflow_run' && "
                          "steps.target.outcome == 'failure'")
    assert "QA_AUTOMATIC_TARGET_HOLD" in warn["run"] and "can_execute=false" in warn["run"]


def test_release_admission_isolated_only_for_automatic_runs_and_never_admits():
    workflow = yaml.load(REL.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    admit = workflow["jobs"]["admit-release"]
    assert "continue-on-error" not in admit
    target = next(s for s in admit["steps"] if s.get("id") == "target")
    assert target["continue-on-error"] == AUTO
    # applicable=false is written before any refusal can exit.
    first = [line for line in target["run"].splitlines() if line.strip()][1]
    assert first.strip() == 'echo "applicable=false" >> "$GITHUB_OUTPUT"'
    gate = workflow["jobs"]["release"]["if"]
    assert "needs.admit-release.result == 'success'" in gate
    assert "needs.admit-release.outputs.applicable == 'true'" in gate
    warn = next(s for s in admit["steps"] if s["name"] == "Preserve automatic Release target admission HOLD")
    assert "steps.target.outcome == 'failure'" in warn["if"]
    assert "RELEASE_AUTOMATIC_TARGET_HOLD" in warn["run"]


def test_release_publishing_job_is_not_isolated():
    workflow = yaml.load(REL.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    release = workflow["jobs"]["release"]
    assert "continue-on-error" not in release
    assert all("continue-on-error" not in json.dumps(s) for s in release["steps"])
