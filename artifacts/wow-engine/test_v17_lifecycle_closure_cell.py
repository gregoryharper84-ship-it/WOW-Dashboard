import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "artifacts/wow-engine/v17/engineering_agent_team.py"
LOOP_PATH = ROOT / ".github/workflows/wow-v17-24h-engineering-closure-loop.yml"


def _module():
    spec = importlib.util.spec_from_file_location("engineering_agent_team", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _lifecycle_record(module, declared="A"):
    return {
        "lifecycle_control_plane": True,
        "change_class": declared,
        **{flag: False for flag in module.LIFECYCLE_SCOPE_FLAGS},
    }


def test_lifecycle_cell_is_non_probability_authority():
    module = _module()
    assert module.TEAM_VERSION == "5.1"
    assert set(module.LIFECYCLE_CELL.values()) <= set(module.AGENT_ROLES)
    for role_name in module.LIFECYCLE_CELL.values():
        role = module.AGENT_ROLES[role_name]
        assert role["may_write_code"] is False
        assert role["may_approve_own_work"] is False
        assert role["may_change_probability_behavior"] is False


def test_class_a_lifecycle_boundary_accepts_governance_only_scope():
    module = _module()
    record = _lifecycle_record(module, "A")
    assert module.required_lifecycle_change_class(record) == ("A", ())
    assert module.validate_lifecycle_classification(record) == []


def test_class_a_lifecycle_boundary_escalates_probability_adjacent_scope_to_b():
    module = _module()
    record = _lifecycle_record(module, "A")
    record["changes_model_registration"] = True
    required, triggers = module.required_lifecycle_change_class(record)
    assert required == "B"
    assert triggers == ("changes_model_registration",)
    errors = module.validate_lifecycle_classification(record)
    assert errors == [
        "CLASSIFICATION_ESCALATION_REQUIRED: declared A, required B: changes_model_registration"
    ]


def test_class_a_lifecycle_boundary_escalates_probability_producing_scope_to_c():
    module = _module()
    record = _lifecycle_record(module, "A")
    record["changes_calibration_or_lower_bounds"] = True
    required, triggers = module.required_lifecycle_change_class(record)
    assert required == "C"
    assert triggers == ("changes_calibration_or_lower_bounds",)
    errors = module.validate_lifecycle_classification(record)
    assert errors == [
        "CLASSIFICATION_ESCALATION_REQUIRED: declared A, required C: changes_calibration_or_lower_bounds"
    ]


def test_lifecycle_classification_requires_explicit_scope_attestation():
    module = _module()
    errors = module.validate_lifecycle_classification(
        {"lifecycle_control_plane": True, "change_class": "A"}
    )
    assert len(errors) == 1
    assert errors[0].startswith("lifecycle classification requires explicit scope flags:")


def test_non_lifecycle_closure_records_are_not_reclassified():
    module = _module()
    assert module.validate_lifecycle_classification({"change_class": "A"}) == []



def test_24h_loop_emits_explicit_lifecycle_owner():
    text = LOOP_PATH.read_text(encoding="utf-8")
    assert 'lifecycle_owner="LIFECYCLE_CONTROLLER_AGENT"' in text
    assert 'lifecycle_owner="RELEASE_VERIFICATION_OWNER_AGENT"' in text
    assert 'lifecycle_owner="QUEUE_STEWARD_AGENT"' in text
    assert 'echo "lifecycle_owner=$lifecycle_owner"' in text
    assert '--arg lifecycle_owner "$lifecycle_owner"' in text
    assert 'lifecycle_owner:$lifecycle_owner' in text



def test_lifecycle_control_path_cannot_bypass_marker():
    module = _module()
    record = module.lifecycle_record_from_pr_body(
        "",
        changed_paths=("artifacts/wow-engine/v17/engineering_agent_team.py",),
    )
    assert record["lifecycle_control_plane"] is True
    errors = module.validate_lifecycle_classification(record)
    assert any("invalid lifecycle change_class: MISSING" in error for error in errors)
    assert any("explicit scope flags" in error for error in errors)


def test_lifecycle_pr_body_parses_explicit_class_a_attestation():
    module = _module()
    lines = [
        "Lifecycle-Control-Plane: true",
        "Change-Class: A",
        "",
        "### Explicit Class A scope attestation",
        *[f"- {flag}: false" for flag in module.LIFECYCLE_SCOPE_FLAGS],
    ]
    record = module.lifecycle_record_from_pr_body(
        "\n".join(lines),
        changed_paths=(".github/workflows/wow-v17-24h-engineering-closure-loop.yml",),
    )
    assert record["lifecycle_control_plane"] is True
    assert record["change_class"] == "A"
    assert module.validate_lifecycle_classification(record) == []


def test_non_lifecycle_path_does_not_force_lifecycle_classification():
    module = _module()
    record = module.lifecycle_record_from_pr_body(
        "",
        changed_paths=("README.md",),
    )
    assert record == {"lifecycle_control_plane": False}
    assert module.validate_lifecycle_classification(record) == []

def test_unrelated_main_movement_revalidates_same_pr_in_place():
    module = _module()
    decision = module.decide_base_drift(
        base_advanced=True,
        mergeable=True,
        semantic_conflict=False,
        protected_path_overlap=False,
    )
    assert decision.action == "REVALIDATE_IN_PLACE"
    assert decision.rerun_candidate_gates is True
    assert decision.restack_same_pr is False
    assert decision.close_pr is False
    assert decision.as_dict()["can_execute"] is False


def test_protected_overlap_requires_revalidation_not_pr_recreation():
    module = _module()
    decision = module.decide_base_drift(
        base_advanced=True,
        mergeable=True,
        semantic_conflict=False,
        protected_path_overlap=True,
    )
    assert decision.action == "REVALIDATE_IN_PLACE"
    assert decision.rerun_candidate_gates is True
    assert decision.restack_same_pr is False
    assert decision.close_pr is False
    assert "protected-path overlap" in decision.reason


def test_real_conflict_restacks_same_pr_without_superseding_it():
    module = _module()
    decision = module.decide_base_drift(
        base_advanced=True,
        mergeable=False,
    )
    assert decision.action == "RESTACK_SAME_PR"
    assert decision.rerun_candidate_gates is True
    assert decision.restack_same_pr is True
    assert decision.close_pr is False


def test_no_base_drift_preserves_current_certification():
    module = _module()
    decision = module.decide_base_drift(
        base_advanced=False,
        mergeable=True,
    )
    assert decision.action == "NO_BASE_DRIFT"
    assert decision.rerun_candidate_gates is False
    assert decision.restack_same_pr is False
    assert decision.close_pr is False


def test_lifecycle_skill_forbids_supersession_for_base_drift():
    skill = (ROOT / ".agents/skills/wow-engineering-lifecycle-closure-cell/SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "base drift alone is never supersession" in skill
    assert "Movement of `main` alone never closes, supersedes, or recreates" in skill
    assert "REVALIDATE_IN_PLACE" in skill
    assert "RESTACK_SAME_PR" not in skill or "Restack the same PR branch only" in skill

def _candidate_receipt():
    return {
        "merge_candidate": {
            "pr_number": 1457,
            "pr_head_sha": "head-sha",
            "base_sha": "base-sha",
            "candidate_sha": "candidate-sha",
        },
        "certification_status": "PASS",
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }


def test_merge_candidate_receipt_binds_pr_head_base_and_candidate_sha():
    module = _module()
    assert module.validate_merge_candidate_receipt(
        _candidate_receipt(),
        expected_pr_number=1457,
        expected_head_sha="head-sha",
        expected_base_sha="base-sha",
        expected_candidate_sha="candidate-sha",
    ) == []


def test_merge_candidate_receipt_rejects_stale_base_without_superseding_pr():
    module = _module()
    receipt = _candidate_receipt()
    errors = module.validate_merge_candidate_receipt(
        receipt,
        expected_pr_number=1457,
        expected_head_sha="head-sha",
        expected_base_sha="new-base-sha",
        expected_candidate_sha="new-candidate-sha",
    )
    assert any("base_sha mismatch" in error for error in errors)
    assert any("candidate_sha mismatch" in error for error in errors)
    decision = module.decide_base_drift(base_advanced=True, mergeable=True)
    assert decision.action == "REVALIDATE_IN_PLACE"
    assert decision.close_pr is False


def test_merge_candidate_receipt_fails_closed_on_governance_or_execution_drift():
    module = _module()
    receipt = _candidate_receipt()
    receipt["certification_status"] = "FAIL"
    receipt["terminal_authority"] = "OTHER"
    receipt["can_execute"] = True
    errors = module.validate_merge_candidate_receipt(
        receipt,
        expected_pr_number=1457,
        expected_head_sha="head-sha",
        expected_base_sha="base-sha",
        expected_candidate_sha="candidate-sha",
    )
    assert "merge candidate certification_status must be PASS" in errors
    assert "merge candidate receipt must preserve V17_TERMINAL_REDUCER" in errors
    assert "merge candidate receipt must set can_execute=false" in errors

