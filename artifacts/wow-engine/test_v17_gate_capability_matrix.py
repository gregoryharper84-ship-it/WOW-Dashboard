"""Regression for the V25 section 2 gate-capability matrix.

The matrix exists to answer, before CI is spent or a reviewer is dispatched: can this
gate ever pass for this change, and if not what authorized route resolves it? These
tests keep it honest rather than descriptive:

- every referenced workflow exists, so the matrix cannot cite a gate that is gone;
- every typed reason is in the canonical registry, so a new code cannot appear only
  in a planner;
- the live/proposed split matches the ruleset artifacts, which is what makes
  "unreachable" different from "merge-blocking";
- a structural impasse is `POLICY_UNREACHABLE` with a named actor and is never
  retried;
- the matrix grants no execution or merge authority;
- the published markdown cannot drift from the module.

Run with pytest, or directly with the repository's no-pytest runner.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "artifacts/wow-engine/v17/gate_capability_matrix.py"
REGISTRY = ROOT / "artifacts/wow-engine/docs/failure_codes.md"
PROPOSED_RULESET = ROOT / "artifacts/wow-engine/v17/agent_identity/ruleset-main.proposed.json"
DOC = ROOT / "docs/wow/engineering/WOW_GATE_CAPABILITY_MATRIX.md"

spec = importlib.util.spec_from_file_location("gate_capability_matrix", MODULE)
gcm = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = gcm
spec.loader.exec_module(gcm)


def test_every_referenced_workflow_exists():
    for gate in gcm.GATES:
        assert (ROOT / gate.workflow).is_file(), gate.workflow
        assert gate.check_context.strip(), gate.id
        assert gate.requirement in gcm.REQUIREMENT_LEVELS, gate.id
        assert gate.required_by.strip(), gate.id


def test_every_impossible_state_is_fully_specified():
    for gate in gcm.GATES:
        for state in gate.impossible:
            assert state.condition in gcm.CONDITIONS, (gate.id, state.condition)
            assert state.recovery_class in gcm.RECOVERY_CLASSES, (gate.id, state)
            assert state.actor.strip(), (gate.id, state)
            assert state.action.strip(), (gate.id, state)
            # Only TRANSIENT may be retried; a structural impasse never is.
            assert state.recovery_class != "TRANSIENT", (gate.id, state)


def test_every_typed_reason_is_registered():
    registered = set(re.findall(r"^\| `([A-Z][A-Z0-9_]+)` \|", REGISTRY.read_text(),
                                re.MULTILINE))
    assert registered, "registry table rows not parsed"
    named = {state.reason for gate in gcm.GATES for state in gate.impossible}
    assert named, "matrix names no typed reasons"
    assert named <= registered, sorted(named - registered)


def test_live_required_checks_are_exactly_the_pinned_three():
    live = {gate.check_context for gate in gcm.live_gates()}
    assert live == {
        "WOW required-three regression",
        "WOW governed probability backend",
        "WOW additional required regression",
    }
    proposed_text = PROPOSED_RULESET.read_text()
    for context in live:
        assert context in proposed_text, context


def test_app_checks_are_proposed_not_live():
    proposed = json.loads(PROPOSED_RULESET.read_text())
    contexts = {c["context"] for c in
                proposed["rules"][-1]["parameters"]["required_status_checks"]}
    by_id = {gate.id: gate for gate in gcm.GATES}
    for gate_id in ("independent_qa", "release_authority"):
        gate = by_id[gate_id]
        assert gate.requirement == "ruleset_proposed", gate_id
        assert gate.check_context in contexts, gate_id
        assert gate.check_context not in {g.check_context for g in gcm.live_gates()}


def test_owner_authored_trust_root_is_policy_unreachable_not_retryable():
    result = gcm.assess(change_class="S", author_is_owner=True, touches_trust_root=True)
    reasons = {entry["reason"] for entry in result["unreachable"]}
    assert "QA_TRUST_ROOT_OWNER_AUTHORED" in reasons
    assert "OWNER_IS_SOURCE_AUTHOR_OR_IDENTITY_UNVERIFIED" in reasons
    assert result["retryable"] == []
    for entry in result["unreachable"]:
        assert entry["recovery_class"] == "POLICY_UNREACHABLE", entry
        assert entry["actor"] == "INDEPENDENT_ENGINEERING_AUTHOR", entry
        assert "INDEPENDENT_SOURCE_ADOPTION" in entry["action"], entry


def test_structural_impasse_never_reports_a_merge_block():
    # This is the #2301 finding: certification is unreachable while the pinned
    # three still decide the merge. Conflating the two is what caused the loop.
    for change_class in gcm.CHANGE_CLASSES:
        result = gcm.assess(change_class=change_class, author_is_owner=True,
                            touches_trust_root=True)
        assert result["unreachable"], change_class
        assert result["blocking"] == [], change_class
        assert result["merge_blocked"] is False, change_class


def test_independent_author_trust_root_change_is_reachable():
    result = gcm.assess(change_class="S", author_is_owner=False, touches_trust_root=True)
    assert result["unreachable"] == []
    assert result["next_action"] == "NONE"


def test_provider_failure_is_configuration_not_transient():
    result = gcm.assess(change_class="S", author_is_owner=False, touches_trust_root=False,
                        provider_available=False)
    entries = [e for e in result["unreachable"] if e["gate"] == "semantic_review"]
    assert entries, result["unreachable"]
    entry = entries[0]
    assert entry["reason"] == "PROVIDER_MODEL_UNAVAILABLE"
    assert entry["recovery_class"] == "AUTH_OR_CONFIGURATION"
    assert entry["action"] == "REPAIR_PROVIDER_CREDENTIAL_OR_ROUTE"


def test_unchanged_head_after_reject_requires_new_evidence_not_a_new_verdict():
    result = gcm.assess(change_class="S", author_is_owner=False, touches_trust_root=False,
                        head_unchanged_since_reject=True)
    entry = [e for e in result["unreachable"]
             if e["reason"] == "PREVIOUS_SEMANTIC_REJECT_SAME_SHA"][0]
    assert entry["recovery_class"] == "PROVEN_REJECT"
    assert entry["action"] == "NEW_HEAD_OR_NEW_EVIDENCE_REQUIRED"
    assert result["retryable"] == []


def test_matrix_grants_no_execution_or_merge_authority():
    for change_class in gcm.CHANGE_CLASSES:
        for owner in (False, True):
            result = gcm.assess(change_class=change_class, author_is_owner=owner,
                                touches_trust_root=owner)
            assert result["can_execute"] is False
            assert result["grants_merge"] is False
    source = MODULE.read_text()
    for forbidden in ("subprocess", "requests", "urllib", "gh ", "git "):
        assert forbidden not in source, forbidden


def test_unknown_change_class_is_rejected():
    for bad in ("", "B", "s", "X"):
        try:
            gcm.assess(change_class=bad, author_is_owner=False, touches_trust_root=False)
        except ValueError:
            continue
        raise AssertionError(f"accepted unknown change class {bad!r}")


def test_published_doc_matches_module():
    assert DOC.is_file(), DOC
    assert DOC.read_text() == gcm.render_markdown()


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
            else:
                print(f"ok   {name}")
    raise SystemExit(1 if failures else 0)


def test_matrix_does_not_equate_pinned_ci_with_release_authority():
    for change_class in gcm.CHANGE_CLASSES:
        report = gcm.assess(change_class=change_class, author_is_owner=True,
                            touches_trust_root=True)
        assert report["merge_blocked_scope"] == "STRUCTURAL_PINNED_CI_ONLY"
        assert report["independent_review_status"] == "UNVERIFIED"
        assert report["source_independence_status"] == "UNVERIFIED"
        assert report["live_ruleset_status"] == "NOT_QUERIED_THIS_INVOCATION"
        assert report["policy_review_required"] == (change_class in ("S", "C"))
        assert report["can_execute"] is False
        assert report["grants_merge"] is False
