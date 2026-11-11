"""The automatic Release admission callback must name its typed refusal.

A `workflow_run` callback executes against protected `main`, so an opaque shell
failure here publishes a failing check on whichever commit Render's `checksPass`
trigger is currently evaluating. These tests pin three properties:

* a refused policy resolution still fails closed and publishes no Release check;
* the exact typed reason is visible on the check annotation and job summary, so
  the next occurrence is diagnosable without server-side log access;
* a draft source — which GitHub cannot merge — is neutralized only for the
  automatic callback, and can never become Release evidence.
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-release-authority-check.yml"


def _load():
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _admission_run():
    admission = _load()["jobs"]["admit-release"]
    return next(s for s in admission["steps"] if s.get("id") == "target")["run"]


def test_admission_surfaces_the_typed_policy_refusal():
    run = _admission_run()
    assert 'if ! bundle=$(python "$tool" "${args[@]}" 2>"$err"); then' in run
    assert "resolve-qa-policy.err" in run
    assert 'echo "::error::${reason}"' in run
    assert "GITHUB_STEP_SUMMARY" in run


def test_a_refused_policy_still_exits_nonzero_and_publishes_nothing():
    run = _admission_run()
    # The typed-reason block must terminate before the neutral short-circuit.
    reason_block = run.split("if ! bundle=", 1)[1].split("fi", 1)[0]
    assert reason_block.count("exit 1") == 1
    # Neutrality stays reserved for a resolved policy bundle (including
    # RELEASE_QA_POLICY_ARTIFACT_ABSENT under --neutral-if-superseded), never
    # an unresolved shell refusal.
    assert '.disposition // empty' in run and '"neutral"' in run


def test_draft_neutralisation_travels_only_with_superseded_neutrality():
    """The strict default must stay reachable: one flag, one call site.

    Library-level strictness (a draft at the policy head still raises without
    the explicit opt-in) is pinned in
    .github/scripts/tests/test_independent_source_review_receipt.py.
    """
    run = _admission_run()
    assert run.count("--neutral-if-superseded") == 1
    assert "--neutral-if-superseded --neutral-if-draft-target" in run
    # The manual path still names exactly one PR; no blanket neutrality.
    assert 'args+=(--pr "$INPUT_PR")' in run


def test_release_lane_cannot_run_without_a_successful_admission():
    release = _load()["jobs"]["release"]
    assert release["needs"] == "admit-release"
    assert release["if"].startswith("needs.admit-release.result == 'success'")
    assert "needs.admit-release.outputs.applicable == 'true'" in release["if"]
