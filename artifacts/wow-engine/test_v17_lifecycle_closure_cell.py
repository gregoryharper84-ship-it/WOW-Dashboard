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
