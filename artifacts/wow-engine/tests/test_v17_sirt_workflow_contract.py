"""Protected SIRT review uses existing QA/Release, never a new workflow."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[3]


def _steps(name):
    doc = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
    jobs = list(doc["jobs"].values())
    return {step["name"]: step for job in jobs for step in job.get("steps", [])}


def test_qa_sirt_mode_is_opt_in_and_gemini_mutually_exclusive():
    steps = _steps("wow-v17-independent-qa-check.yml")
    policy = steps["Select exact-head review policy"]
    assert "WOW_QA_SIRT_VERIFICATION_ACTIVE" in str(policy["env"])
    assert "mode=sirt" in policy["run"]
    assert "QA_SIRT_REVIEW_MODES_CONFLICT" in policy["run"]
    assert "WOW_SIRT_REVIEWER_USER_ID" in str(steps["Decide at exact head (read-only)"]["env"])


def test_release_independently_reads_same_protected_sirt_principal():
    steps = _steps("wow-v17-release-authority-check.yml")
    assert "WOW_SIRT_REVIEWER_USER_ID" in str(steps["Decide at exact head (read-only)"]["env"])


def test_sirt_does_not_let_gemini_steps_run_or_sign():
    steps = _steps("wow-v17-independent-qa-check.yml")
    for name in (
        "Require isolated adversarial-review credential",
        "Run isolated read-only Gemini semantic review",
        "Build versioned exact-head review artifact",
    ):
        assert steps[name]["if"] == "steps.policy.outputs.review_active == 'true'"
    assert "QA_SIRT_INDEPENDENT_EVIDENCE" in (
        ROOT / "artifacts/wow-engine/v17/independent_release_checks.py"
    ).read_text()


def test_sirt_opt_in_is_bound_to_both_exact_head_decisions():
    for workflow in (
        "wow-v17-independent-qa-check.yml",
        "wow-v17-release-authority-check.yml",
    ):
        step = _steps(workflow)["Decide at exact head (read-only)"]
        assert "WOW_QA_SIRT_VERIFICATION_ACTIVE" in str(step["env"])
        assert "--sirt-review-policy-enabled" in step["run"]
