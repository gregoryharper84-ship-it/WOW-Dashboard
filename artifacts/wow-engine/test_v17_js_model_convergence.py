from __future__ import annotations

import json
from pathlib import Path

from v17.js_model_convergence import (
    JS_MODEL_CONFIRMED,
    JS_MODEL_CONTRADICTION,
    JS_MODEL_STRONG_CONFIRMATION,
    JS_ONLY_MODEL_HOLD,
    MODEL_CONFIRMED_NON_JS,
    evaluate_js_model_convergence,
)


def _model(**overrides):
    base = {
        "probability_publishable": True,
        "rank_eligible": True,
        "model_qualified": True,
        "confidence_tier": "HIGH",
        "calibrated_probability": 0.68,
        "calibrated_lower_bound": 0.61,
        "controlling_specialist": "wow.example-specialist",
    }
    base.update(overrides)
    return base


def _js(candidate=True, priority=82.0):
    return {
        "js_candidate": candidate,
        "js_research_priority": priority,
        "js_archetypes": ["JS_OPPORTUNITY_CEILING_LESS"] if candidate else [],
    }


def test_strong_confirmation_requires_fresh_context_and_stress_pass() -> None:
    decision = evaluate_js_model_convergence(
        js_annotation=_js(),
        model_package=_model(),
        context={
            "evidence_fresh": True,
            "stress_passed": True,
            "current_board_verified": True,
        },
        require_current_board=True,
    )
    assert decision.convergence_status == JS_MODEL_STRONG_CONFIRMATION
    assert decision.action == "ADVANCE_TO_NORMAL_V17_CONSTRUCTION"
    assert decision.calibrated_probability == 0.68
    assert decision.calibrated_lower_bound == 0.61
    assert decision.probability_mutated is False
    assert decision.can_execute is False


def test_js_candidate_cannot_override_unqualified_model() -> None:
    decision = evaluate_js_model_convergence(
        js_annotation=_js(),
        model_package=_model(rank_eligible=False),
        context={"evidence_fresh": True, "stress_passed": True},
    )
    assert decision.convergence_status == JS_ONLY_MODEL_HOLD
    assert decision.action == "HOLD"
    assert "MODEL_NOT_RANK_ELIGIBLE" in decision.blockers


def test_material_contradiction_beats_js_signal() -> None:
    decision = evaluate_js_model_convergence(
        js_annotation=_js(),
        model_package=_model(),
        context={
            "evidence_fresh": True,
            "stress_passed": True,
            "material_contradiction": True,
        },
    )
    assert decision.convergence_status == JS_MODEL_CONTRADICTION
    assert decision.action == "DO_NOT_ELEVATE"
    assert "MATERIAL_JS_MODEL_CONTRADICTION" in decision.blockers


def test_non_js_model_candidate_remains_eligible() -> None:
    decision = evaluate_js_model_convergence(
        js_annotation=_js(candidate=False, priority=20.0),
        model_package=_model(),
        context={"evidence_fresh": True, "stress_passed": True},
    )
    assert decision.convergence_status == MODEL_CONFIRMED_NON_JS
    assert decision.action == "ADVANCE_TO_NORMAL_V17_CONSTRUCTION"


def test_current_prizepicks_verification_is_required_when_requested() -> None:
    decision = evaluate_js_model_convergence(
        js_annotation=_js(),
        model_package=_model(),
        context={
            "evidence_fresh": True,
            "stress_passed": True,
            "current_board_verified": False,
        },
        require_current_board=True,
    )
    assert decision.convergence_status == JS_ONLY_MODEL_HOLD
    assert "CURRENT_EXACT_LINE_DIRECTION_NOT_VERIFIED" in decision.blockers


def test_missing_explicit_stress_pass_can_confirm_but_not_strong_confirm() -> None:
    decision = evaluate_js_model_convergence(
        js_annotation=_js(),
        model_package=_model(),
        context={"evidence_fresh": True},
    )
    assert decision.convergence_status == JS_MODEL_CONFIRMED
    assert decision.action == "ADVANCE_TO_NORMAL_V17_CONSTRUCTION"


def test_probability_values_are_copied_not_blended_with_js_priority() -> None:
    decision = evaluate_js_model_convergence(
        js_annotation=_js(priority=99.9),
        model_package=_model(
            calibrated_probability=0.641,
            calibrated_lower_bound=0.573,
        ),
        context={"evidence_fresh": True, "stress_passed": True},
    )
    assert decision.calibrated_probability == 0.641
    assert decision.calibrated_lower_bound == 0.573
    assert decision.js_research_priority == 99.9
    assert decision.probability_mutated is False
    assert "OFFICIAL_RANKING_REMAINS_GOVERNED_LOWER_BOUND_FIRST" in decision.reasons


def test_convergence_skill_manifest_preserves_probability_authority() -> None:
    manifest_path = Path(__file__).parent / "v17" / "skills" / "skill_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    skill = next(
        item for item in manifest["skills"]
        if item["skill_id"] == "WOW_V17_JS_MODEL_CONVERGENCE"
    )
    assert skill["probability_authority"] is False
    assert skill["status"] == "ACTIVE_RESEARCH_GOVERNOR"
    assert manifest["invariants"]["js_model_convergence_not_new_probability_model"] is True
    assert manifest["invariants"]["js_candidate_cannot_override_governed_model"] is True
    assert manifest["invariants"]["non_js_model_candidate_remains_eligible"] is True
    assert manifest["invariants"]["official_prop_ranking_remains_governed_lower_bound_first"] is True


def test_convergence_runs_before_pick_core_in_composed_workflows() -> None:
    manifest_path = Path(__file__).parent / "v17" / "skills" / "skill_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for skill_id in ("WOW_V17_DAILY_PICKS", "WOW_V17_PRIZEPICKS_BOARD_TO_SLIPS"):
        skill = next(item for item in manifest["skills"] if item["skill_id"] == skill_id)
        assert skill["composes"].index("WOW_V17_JS_MODEL_CONVERGENCE") < skill["composes"].index("WOW_V17_PICK_CORE")
