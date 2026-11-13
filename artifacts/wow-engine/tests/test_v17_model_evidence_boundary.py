"""Serve-time contracts for the V17 evidence-triad gate."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from v17.model_evidence_boundary import (
    COMPLETE,
    EVIDENCE_UNAVAILABLE,
    HOLD,
    IDENTITY_UNRESOLVED,
    INCOMPLETE,
    apply_evidence_triad_gate,
    build_evidence_scope,
    evaluate_served_model_evidence,
    seal_report,
)
from v17.team_event_bridge_runtime import (
    TEAM_EVENT_BRIDGES,
    register_team_event_bridge,
    score_registered_team_event_request,
)
from v17.team_event_capability_manifest import TEAM_EVENT_INPUT_CONTRACTS
from v17.team_event_official_publication_guard import (
    evaluate_team_event_official_publication,
)

PREDICTION_AT = "2026-11-12T18:30:00+00:00"
EVENT_START = "2026-11-12T19:00:00+00:00"
ARTIFACT_VERSION = "MLB_V16_V2D_CONTEXT_SHARED_SIM_R1"


class _EmptyDb:
    """Governed read client with no rows: absence, never an exception."""

    def table(self, _name: str):
        return self

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, _count: int):
        return self

    def execute(self):
        return SimpleNamespace(data=[])


class _BrokenDb:
    def table(self, _name: str):
        raise RuntimeError("evidence store unavailable")


def _req(**overrides):
    values = {
        "official_event_id": "official-event-1",
        "event_start_time_utc": EVENT_START,
        "latest_material_update_timestamp": "2026-11-12T18:10:00+00:00",
        "sport": "MLB",
        "league": "MLB",
        "home_team": "Alpha",
        "away_team": "Beta",
        "requester_host_identity": "WOW_BETTING_ENGINE",
        "candidate_family": "TEAM_EVENT",
        "settlement_basis": "FULL_GAME",
        "sport_specific_evidence": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _served_result(**overrides):
    row = {
        "official_event_id": "official-event-1",
        "calibrated_probability": 0.72,
        "calibrated_lower_bound": 0.66,
        "calibrated_upper_bound": 0.78,
        "immutable_model_timestamp": PREDICTION_AT,
        "model_family": "V2D_SHARED_NB_PLUS_CONTEXT_REGIMES",
        "model_artifact_version": ARTIFACT_VERSION,
        "feature_schema_version": "MLB_V2D_CONTEXT_V1",
        "probability_publishable": True,
        "rank_eligible": True,
        "terminal_label": "FINAL_APPROVED",
        "can_execute": False,
    }
    row.update(overrides)
    return row


def _complete_assessor(_db, _scope):
    return {
        "bundle_complete": True,
        "blockers": [],
        "component_sources": {"artifact": {"status": "FOUND"}},
        "missing_component_fields": {},
    }


def _incomplete_assessor(_db, _scope):
    return {
        "bundle_complete": False,
        "blockers": ["MODEL_BUNDLE_CALIBRATOR_INVALID_OR_UNVERIFIED"],
        "component_sources": {"calibrator": {"status": "FOUND"}},
        "missing_component_fields": {"calibrator": ["calibrator_checksum"]},
    }


def test_scope_binds_the_served_model_not_a_sport_default():
    scope, missing = build_evidence_scope(_served_result(), _req(), sport="MLB")

    assert missing == ()
    assert scope is not None
    assert scope.model_kind == "TEAM_EVENT"
    assert scope.model_artifact_version == ARTIFACT_VERSION
    assert scope.model_family == "V2D_SHARED_NB_PLUS_CONTEXT_REGIMES"
    assert scope.feature_schema_version == "MLB_V2D_CONTEXT_V1"
    assert scope.prediction_timestamp == PREDICTION_AT


@pytest.mark.parametrize("sport", ("NBA", "WNBA", "NHL", "NCAAF", "NCAAB", "SOCCER"))
def test_cross_sport_scope_never_borrows_nfl_feature_schema(sport):
    """Missing sport-specific schema is a typed hold, not NFL evidence."""
    served = _served_result(
        model_family=f"{sport}_FITTED_TEST_V1",
        model_artifact_version=f"{sport}_FITTED_TEST_V1_20261111",
        feature_schema_version=None,
    )
    scope, missing = build_evidence_scope(served, _req(sport=sport, league=sport), sport=sport)
    assert scope is None
    assert "feature_schema_version" in missing

    report = evaluate_served_model_evidence(
        served, _req(sport=sport, league=sport), sport=sport, db=_EmptyDb(),
        assessor=_complete_assessor,
    )
    assert report["status"] == IDENTITY_UNRESOLVED
    assert report["bundle_complete"] is False
    assert "MODEL_EVIDENCE_TRIAD_IDENTITY_UNRESOLVED:feature_schema_version" in report["blockers"]
    assert report["probability_publishable"] is False
    assert report["can_execute"] is False

    served["feature_schema_version"] = f"{sport}_FEATURE_SCHEMA_V1"
    resolved, missing = build_evidence_scope(
        served, _req(sport=sport, league=sport), sport=sport,
    )
    assert missing == ()
    assert resolved is not None
    assert resolved.feature_schema_version == f"{sport}_FEATURE_SCHEMA_V1"


def test_unresolved_identity_names_the_missing_fields_and_withholds():
    # MLB declares a repository artifact version, so an omitted one is a real
    # contract fallback. NFL declares no version: the served result must name it.
    result = _served_result(
        model_family=None, model_artifact_version=None, model_version=None
    )
    report = evaluate_served_model_evidence(
        result, _req(sport="NFL", league="NFL"), sport="NFL", db=_EmptyDb(),
        assessor=_complete_assessor,
    )

    assert report["status"] == IDENTITY_UNRESOLVED
    assert report["bundle_complete"] is False
    assert "MODEL_EVIDENCE_TRIAD_IDENTITY_UNRESOLVED:model_family" in report["blockers"]
    assert "MODEL_EVIDENCE_TRIAD_IDENTITY_UNRESOLVED:model_artifact_version" in report["blockers"]
    assert report["probability_publishable"] is False


def test_unreadable_store_is_typed_and_does_not_look_like_complete_evidence():
    report = evaluate_served_model_evidence(
        _served_result(), _req(), sport="MLB", db=None
    )

    assert report["status"] == EVIDENCE_UNAVAILABLE
    assert "MODEL_EVIDENCE_TRIAD_READER_UNAVAILABLE" in report["blockers"]
    assert report["bundle_complete"] is False


def test_incomplete_triad_withholds_publication_and_forces_the_hold_ceiling():
    gated = apply_evidence_triad_gate(
        _served_result(),
        evaluate_served_model_evidence(
            _served_result(), _req(), sport="MLB", db=_EmptyDb(), assessor=_incomplete_assessor
        ),
    )

    assert gated["evidence_triad_required"] is True
    assert gated["evidence_triad_status"] == INCOMPLETE
    assert gated["probability_publishable"] is False
    assert gated["rank_eligible"] is False
    assert gated["terminal_label"] == HOLD
    assert gated["terminal_ceiling"] == HOLD
    assert gated["terminal_reducer_input"]["terminal_output"] == HOLD
    assert gated["terminal_reducer_input"]["global_terminal_reducer"] == "V17_TERMINAL_REDUCER"
    assert "MODEL_EVIDENCE_TRIAD_INCOMPLETE" in gated["publication_blockers"]
    assert "MODEL_BUNDLE_CALIBRATOR_INVALID_OR_UNVERIFIED" in gated["publication_blockers"]
    assert gated["can_execute"] is False
    # The scored numbers are preserved for immutable diagnostics.
    assert gated["calibrated_probability"] == 0.72
    assert gated["calibrated_lower_bound"] == 0.66


def test_complete_triad_does_not_rewrite_the_scored_or_terminal_fields():
    gated = apply_evidence_triad_gate(
        _served_result(),
        evaluate_served_model_evidence(
            _served_result(), _req(), sport="MLB", db=_EmptyDb(), assessor=_complete_assessor
        ),
    )

    assert gated["evidence_triad_status"] == COMPLETE
    assert gated["probability_publishable"] is True
    assert gated["terminal_label"] == "FINAL_APPROVED"
    assert "publication_blockers" not in gated
    assert gated["can_execute"] is False


def test_never_erases_an_upstream_publication_blocker():
    upstream = _served_result(publication_blockers=["TEAM_EVENT_EVIDENCE_PREFLIGHT_FAILED"])
    gated = apply_evidence_triad_gate(
        upstream,
        evaluate_served_model_evidence(
            upstream, _req(), sport="MLB", db=_EmptyDb(), assessor=_incomplete_assessor
        ),
    )

    assert gated["publication_blockers"][0] == "TEAM_EVENT_EVIDENCE_PREFLIGHT_FAILED"
    assert "MODEL_EVIDENCE_TRIAD_INCOMPLETE" in gated["publication_blockers"]


def test_official_publication_guard_blocks_an_incomplete_required_triad():
    payload = _served_result(
        evidence_triad_required=True,
        evidence_triad_status=INCOMPLETE,
        model_evidence_triad={
            "status": INCOMPLETE,
            "bundle_complete": False,
            "blockers": ["MODEL_BUNDLE_ARTIFACT_MISSING"],
        },
    )
    decision = evaluate_team_event_official_publication(payload)

    assert decision["official_publication_allowed"] is False
    assert "TEAM_EVENT_MODEL_EVIDENCE_TRIAD_NOT_PROVEN" in decision["blockers"]


def test_team_event_official_publication_never_exempts_an_unattributed_legacy_payload():
    payload = _served_result()
    decision = evaluate_team_event_official_publication(payload)

    assert "TEAM_EVENT_MODEL_EVIDENCE_TRIAD_NOT_PROVEN" in decision["blockers"]
    assert decision["official_publication_allowed"] is False


def test_upset_or_unknown_sport_cannot_turn_off_the_team_event_triad():
    for overrides in (
        {"sport": "MLB", "candidate_family": "UPSET"},
        {"sport": "MLS", "candidate_family": "UNDERDOG"},
        {"sport": None, "candidate_family": None},
        {"sport": "UNKNOWN", "candidate_family": "TEAM_EVENT"},
    ):
        payload = _served_result(**overrides)
        payload.pop("model_evidence_triad", None)
        payload.pop("evidence_triad_required", None)
        decision = evaluate_team_event_official_publication(payload)
        assert "TEAM_EVENT_MODEL_EVIDENCE_TRIAD_NOT_PROVEN" in decision["blockers"]
        assert decision["official_publication_allowed"] is False


def _complete_sealed(sport="MLB"):
    return seal_report({
        "status": COMPLETE,
        "bundle_complete": True,
        "sport": sport,
        "official_event_id": "official-event-1",
        "model_kind": "TEAM_EVENT",
        "model_family": "V2D_SHARED_NB_PLUS_CONTEXT_REGIMES",
        "model_artifact_version": ARTIFACT_VERSION,
        "feature_schema_version": "MLB_V2D_CONTEXT_V1",
        "blockers": [],
        "can_execute": False,
    })


def test_triad_requirement_is_not_controlled_by_the_payload_flag():
    # A caller that omits (or clears) evidence_triad_required cannot bypass the
    # gate for a governed sport: the requirement comes from the manifest.
    payload = _served_result(sport="MLB", evidence_triad_required=False)
    decision = evaluate_team_event_official_publication(payload)

    assert decision["official_publication_allowed"] is False
    assert "TEAM_EVENT_MODEL_EVIDENCE_TRIAD_NOT_PROVEN" in decision["blockers"]


def test_unsealed_complete_claim_cannot_elevate_publication():
    payload = _served_result(
        sport="MLB",
        model_evidence_triad={"status": COMPLETE, "bundle_complete": True},
    )
    decision = evaluate_team_event_official_publication(payload)

    assert decision["official_publication_allowed"] is False
    assert "TEAM_EVENT_MODEL_EVIDENCE_TRIAD_NOT_PROVEN" in decision["blockers"]


def test_edited_sealed_report_loses_its_seal():
    sealed = _complete_sealed("MLB")
    tampered = dict(sealed, bundle_complete=False)
    payload = _served_result(sport="MLB", model_evidence_triad=tampered)

    decision = evaluate_team_event_official_publication(payload)

    assert decision["official_publication_allowed"] is False
    assert "TEAM_EVENT_MODEL_EVIDENCE_TRIAD_NOT_PROVEN" in decision["blockers"]


def test_sealed_report_for_another_sport_does_not_substitute():
    payload = _served_result(sport="MLB", model_evidence_triad=_complete_sealed("NFL"))
    decision = evaluate_team_event_official_publication(payload)

    assert decision["official_publication_allowed"] is False
    assert "TEAM_EVENT_MODEL_EVIDENCE_TRIAD_NOT_PROVEN" in decision["blockers"]


def test_a_sealed_complete_report_is_accepted_for_its_own_sport():
    payload = _served_result(sport="MLB", model_evidence_triad=_complete_sealed("MLB"))
    decision = evaluate_team_event_official_publication(payload)

    assert "TEAM_EVENT_MODEL_EVIDENCE_TRIAD_NOT_PROVEN" not in decision["blockers"]


def test_an_already_stricter_terminal_is_never_replaced_by_the_model_hold():
    outcome = _served_result(
        terminal_label="RESEARCH_INTEREST",
        terminal_ceiling="RESEARCH_INTEREST",
        terminal_reducer_input={
            "status": "PASS",
            "terminal_output": "RESEARCH_INTEREST",
            "global_terminal_reducer": "V17_TERMINAL_REDUCER",
        },
    )
    gated = apply_evidence_triad_gate(
        outcome,
        evaluate_served_model_evidence(
            outcome, _req(), sport="MLB", db=_EmptyDb(), assessor=_incomplete_assessor
        ),
    )

    assert gated["terminal_label"] == "RESEARCH_INTEREST"
    assert gated["terminal_ceiling"] == "RESEARCH_INTEREST"
    assert gated["terminal_reducer_input"]["terminal_output"] == "RESEARCH_INTEREST"
    assert gated["terminal_reducer_input"]["status"] == "HOLD"
    assert "MODEL_EVIDENCE_TRIAD_INCOMPLETE" in gated["publication_blockers"]


@pytest.fixture(autouse=True)
def isolated_bridge_registry():
    original = dict(TEAM_EVENT_BRIDGES)
    TEAM_EVENT_BRIDGES.clear()
    try:
        yield
    finally:
        TEAM_EVENT_BRIDGES.clear()
        TEAM_EVENT_BRIDGES.update(original)


def _valid_package():
    return {
        "candidate_id": "candidate-1",
        "calibrated_probability": 0.72,
        "calibrated_lower_bound": 0.66,
        "calibrated_upper_bound": 0.78,
        "immutable_model_timestamp": PREDICTION_AT,
        "calibration_method": "ISOTONIC",
        "calibration_version": "v1",
        "source_snapshot_id": "snapshot-1",
        "source_snapshot_timestamp": "2026-11-12T18:25:00+00:00",
        "outcome_space": "TWO_WAY_WINNER",
        "model_version": ARTIFACT_VERSION,
        "probability_publishable": True,
        "rank_eligible": True,
        "blockers": [],
        "can_execute": False,
    }


def _registered_mlb_request():
    evidence = {field: f"verified:{field}" for field in TEAM_EVENT_INPUT_CONTRACTS["MLB"]}
    return _req(sport_specific_evidence=evidence)


def test_registered_scorer_cannot_publish_without_the_triad():
    register_team_event_bridge(
        "MLB",
        adapter_name="TEST_MLB_ADAPTER",
        controlling_specialist="TEST_MLB_SPECIALIST",
        scorer=lambda *_args, **_kwargs: _valid_package(),
        required_inputs=TEAM_EVENT_INPUT_CONTRACTS["MLB"],
    )
    result = score_registered_team_event_request(
        _registered_mlb_request(),
        event_api=_EmptyDb(),
        canonical_hydration_required=False,
    )

    assert result["evidence_triad_required"] is True
    assert result["evidence_triad_status"] == INCOMPLETE
    assert result["model_evidence_triad"]["bundle_complete"] is False
    assert "MODEL_EVIDENCE_TRIAD_INCOMPLETE" in result["publication_blockers"]
    assert "MODEL_BUNDLE_ARTIFACT_MISSING" in result["publication_blockers"]
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["terminal_ceiling"] == HOLD
    assert result["feature_consumption_receipt_id"]
    assert result["can_execute"] is False


def test_held_row_survives_the_official_reducer_and_action_serialization():
    register_team_event_bridge(
        "MLB",
        adapter_name="TEST_MLB_ADAPTER",
        controlling_specialist="TEST_MLB_SPECIALIST",
        scorer=lambda *_args, **_kwargs: _valid_package(),
        required_inputs=TEAM_EVENT_INPUT_CONTRACTS["MLB"],
    )
    result = score_registered_team_event_request(
        _registered_mlb_request(),
        event_api=_EmptyDb(),
        canonical_hydration_required=False,
    )

    # The authoritative official-publication reducer must refuse the row, and the
    # serialized Action response must carry the same hold and blocker.
    decision = evaluate_team_event_official_publication(result)
    assert decision["status"] == "HELD"
    assert decision["official_publication_allowed"] is False
    assert "TEAM_EVENT_MODEL_EVIDENCE_TRIAD_NOT_PROVEN" in decision["blockers"]

    serialized = json.loads(json.dumps({
        key: result.get(key)
        for key in (
            "terminal_label",
            "terminal_ceiling",
            "probability_publishable",
            "rank_eligible",
            "publication_blockers",
            "model_evidence_triad",
            "terminal_reducer_input",
            "can_execute",
        )
    }))
    assert serialized["terminal_ceiling"] == HOLD
    assert serialized["terminal_label"] == HOLD
    assert serialized["probability_publishable"] is False
    assert serialized["rank_eligible"] is False
    assert serialized["can_execute"] is False
    assert "MODEL_EVIDENCE_TRIAD_INCOMPLETE" in serialized["publication_blockers"]
    assert serialized["model_evidence_triad"]["report_id"]
    assert serialized["terminal_reducer_input"]["terminal_output"] == HOLD
    assert serialized["terminal_reducer_input"]["global_terminal_reducer"] == "V17_TERMINAL_REDUCER"


def test_registered_scorer_gate_survives_a_broken_evidence_store():
    register_team_event_bridge(
        "MLB",
        adapter_name="TEST_MLB_ADAPTER",
        controlling_specialist="TEST_MLB_SPECIALIST",
        scorer=lambda *_args, **_kwargs: _valid_package(),
        required_inputs=TEAM_EVENT_INPUT_CONTRACTS["MLB"],
    )
    result = score_registered_team_event_request(
        _registered_mlb_request(),
        event_api=_BrokenDb(),
        canonical_hydration_required=False,
    )

    assert result["evidence_triad_status"] == INCOMPLETE
    assert "MODEL_BUNDLE_ARTIFACT_READ_FAILED" in result["publication_blockers"]
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False



def test_complete_seal_cannot_be_replayed_for_another_official_event():
    sealed = _complete_sealed("MLB")
    payload = _served_result(
        sport="MLB",
        official_event_id="official-event-2",
        model_evidence_triad=sealed,
    )
    decision = evaluate_team_event_official_publication(payload)

    assert decision["official_publication_allowed"] is False
    assert "TEAM_EVENT_MODEL_EVIDENCE_TRIAD_NOT_PROVEN" in decision["blockers"]


def test_complete_seal_cannot_be_replayed_for_another_model_version():
    sealed = _complete_sealed("MLB")
    payload = _served_result(
        sport="MLB",
        model_artifact_version="unrelated-model-v2",
        model_evidence_triad=sealed,
    )
    decision = evaluate_team_event_official_publication(payload)

    assert decision["official_publication_allowed"] is False
    assert "TEAM_EVENT_MODEL_EVIDENCE_TRIAD_NOT_PROVEN" in decision["blockers"]


def test_complete_seal_covers_blockers_and_source_audit_fields():
    sealed = _complete_sealed("MLB")
    for field, tampered_value in (
        ("blockers", ["WAS_NOT_PROVEN"]),
        ("component_sources", {"artifact": {"status": "FOUND"}}),
        ("missing_component_fields", {"calibrator": ["missing_sha"]}),
    ):
        edited = dict(sealed)
        edited[field] = tampered_value
        decision = evaluate_team_event_official_publication(
            _served_result(sport="MLB", model_evidence_triad=edited)
        )
        assert decision["official_publication_allowed"] is False
        assert "TEAM_EVENT_MODEL_EVIDENCE_TRIAD_NOT_PROVEN" in decision["blockers"]

@pytest.mark.parametrize("label", ["MONEY_QUALIFIED", "MARKET_VERIFIED_HOLD"])
def test_incomplete_triad_replaces_money_terminal(label):
    from v17.model_evidence_boundary import _strictest_terminal
    assert _strictest_terminal(label, "MODEL_QUALIFIED_HOLD") == "MODEL_QUALIFIED_HOLD"
    assert _strictest_terminal("UNKNOWN_STRICT_HOLD", "MODEL_QUALIFIED_HOLD") == "UNKNOWN_STRICT_HOLD"
