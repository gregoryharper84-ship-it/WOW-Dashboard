"""Evidence-triad enforcement for the prop and Kalshi weather serving paths."""
from __future__ import annotations

from types import SimpleNamespace

from v17 import model_evidence_boundary as boundary
from v17.model_evidence_boundary import (
    COMPLETE,
    HOLD,
    INCOMPLETE,
    evaluate_market_evidence_triad,
    gate_prop_probability_payload,
)
from v17.prop_universal_forward_evidence import _persist_prediction

EVENT_START = "2026-11-12T19:00:00+00:00"
MODEL_AT = "2026-11-12T18:30:00+00:00"


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def in_(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def gt(self, *_args, **_kwargs):
        return self

    def limit(self, _value):
        return self

    def range(self, _start, _end):
        return self

    def upsert(self, payload, **_kwargs):
        self.db.writes.append((self.table, dict(payload)))
        return self

    def execute(self):
        return SimpleNamespace(data=[])


class _EmptyDb:
    """A store with no fitted rows: absence, never an exception."""

    def __init__(self):
        self.writes: list[tuple[str, dict]] = []

    def table(self, name):
        return _Query(self, name)


def _prop_payload(**overrides):
    payload = {
        "prediction_id": "pred-prop-1",
        "player_id": "player-1", "period": "FULL_GAME",
        "event_id": "WNBA:TEST-1",
        "event_start_time": EVENT_START,
        "model_timestamp": MODEL_AT,
        "sport": "WNBA",
        "market_type": "PLAYER_PROP",
        "stat_type": "POINTS",
        "line": 20.5,
        "direction": "MORE",
        "model_family": "WNBA_PROP_POISSON_LOGGLM_V1",
        "model_artifact_version": "WNBA_POINTS_MODEL_V1",
        "feature_schema_version": "WNBA_PROP_FEATURES_V1",
        "calibrated_probability": 0.62,
        "calibrated_probability_lower_bound": 0.55,
        "probability_publishable": True,
        "can_execute": False,
    }
    payload.update(overrides)
    return payload


def test_prop_persistence_withholds_publication_without_the_triad():
    db = _EmptyDb()
    payload = _prop_payload()

    _persist_prediction(db, payload)

    assert payload["probability_publishable"] is False
    assert payload["evidence_triad_required"] is True
    assert payload["evidence_triad_status"] == INCOMPLETE
    assert "MODEL_EVIDENCE_TRIAD_INCOMPLETE" in payload["triad_blockers"]
    assert "MODEL_BUNDLE_ARTIFACT_MISSING" in payload["triad_blockers"]
    assert payload["model_evidence_triad"]["report_id"]
    # The calibrated numbers are preserved for immutable diagnostics.
    assert payload["calibrated_probability"] == 0.62
    assert payload["calibrated_probability_lower_bound"] == 0.55
    # The persisted row carries the same withheld claim.
    assert db.writes[0][0] == "wow_predictions"
    assert db.writes[0][1]["probability_publishable"] is False
    assert db.writes[0][1]["can_execute"] is False


def test_prop_publication_is_preserved_when_the_triad_is_verified(monkeypatch):
    db = _EmptyDb()
    payload = _prop_payload()
    monkeypatch.setattr(
        boundary,
        "EVIDENCE_ASSESSOR",
        lambda _db, _scope: {
            "bundle_complete": True,
            "blockers": [],
            "component_sources": {},
            "missing_component_fields": {},
        },
    )

    gate_prop_probability_payload(payload, db=db)

    assert payload["evidence_triad_status"] == COMPLETE
    assert payload["probability_publishable"] is True
    assert "triad_blockers" not in payload
    assert payload["can_execute"] is False


def test_prop_identity_gap_is_typed_not_guessed():
    payload = _prop_payload(model_artifact_version=None)

    gate_prop_probability_payload(payload, db=_EmptyDb())

    assert payload["evidence_triad_status"] == "IDENTITY_UNRESOLVED"
    assert (
        "MODEL_EVIDENCE_TRIAD_IDENTITY_UNRESOLVED:model_artifact_version"
        in payload["triad_blockers"]
    )
    assert payload["probability_publishable"] is False


def test_weather_has_no_declared_lane_so_its_triad_is_unconfigured():
    report = evaluate_market_evidence_triad(model_kind="WEATHER", sport="WEATHER", db=_EmptyDb())

    assert report["status"] == INCOMPLETE
    assert report["bundle_complete"] is False
    assert "MODEL_EVIDENCE_TRIAD_LANE_NOT_CONFIGURED" in report["blockers"]
    assert "MODEL_BUNDLE_ARTIFACT_SOURCE_NOT_CONFIGURED" in report["blockers"]
    assert report["probability_publishable"] is False
    assert report["can_execute"] is False


def test_a_sealed_prop_report_is_labelled_player_prop_and_cannot_authorize_team_event(monkeypatch):
    monkeypatch.setattr(
        boundary,
        "EVIDENCE_ASSESSOR",
        lambda _db, _scope: {
            "bundle_complete": True,
            "blockers": [],
            "component_sources": {},
            "missing_component_fields": {},
        },
    )
    payload = _prop_payload()

    gate_prop_probability_payload(payload, db=_EmptyDb())
    report = payload["model_evidence_triad"]

    assert report["bundle_complete"] is True
    assert report["model_kind"] == "PLAYER_PROP"
    # The official-publication guard accepts only a TEAM_EVENT seal, so a verified
    # prop report for the same sport/event/version must never authorize a team-event
    # publication.
    assert boundary.verify_sealed_report(
        report,
        sport="WNBA",
        official_event_id="WNBA:TEST-1",
        model_artifact_version="WNBA_POINTS_MODEL_V1",
    ) is False


def test_weather_report_is_labelled_weather_kind_not_team_event():
    report = evaluate_market_evidence_triad(model_kind="WEATHER", sport="WEATHER", db=_EmptyDb())

    assert report["model_kind"] == "WEATHER"
    assert report["bundle_complete"] is False


def test_a_declared_prop_lane_is_read_rather_than_reported_unconfigured():
    report = evaluate_market_evidence_triad(
        model_kind="PLAYER_PROP",
        sport="WNBA",
        market_family="POINTS",
        player_id="player-1", stat_type="POINTS", period="FULL_GAME", line="7+", direction="MORE",
        official_event_id="WNBA:TEST-1",
        event_start_time=EVENT_START,
        prediction_timestamp=MODEL_AT,
        model_family="WNBA_PROP_POISSON_LOGGLM_V1",
        model_artifact_version="WNBA_POINTS_MODEL_V1",
        feature_schema_version="WNBA_PROP_FEATURES_V1",
        db=_EmptyDb(),
    )

    assert report["status"] == INCOMPLETE
    assert "MODEL_EVIDENCE_TRIAD_LANE_NOT_CONFIGURED" not in report["blockers"]
    assert "MODEL_BUNDLE_ARTIFACT_MISSING" in report["blockers"]


def test_weather_withheld_result_keeps_the_probability_and_hold_ceiling():
    # Mirrors the shape the weather reducer returns after the gate.
    sealed = boundary.seal_report(
        evaluate_market_evidence_triad(model_kind="WEATHER", sport="WEATHER", db=_EmptyDb())
    )
    result = {
        "status": "QUALIFIED_EDGE",
        "p_yes": 0.64,
        "p_no": 0.36,
        "probability_publishable": True,
        "rank_eligible": True,
        "can_execute": False,
    }
    if sealed.get("bundle_complete") is not True:
        result["probability_publishable"] = False
        result["rank_eligible"] = False
        result["probability_blockers"] = list(sealed.get("blockers") or ())

    assert result["p_yes"] == 0.64
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert "MODEL_EVIDENCE_TRIAD_LANE_NOT_CONFIGURED" in result["probability_blockers"]
    assert result["can_execute"] is False
    assert HOLD  # hold ceiling vocabulary remains available to the reducer


def test_prop_scope_retains_literal_offer(monkeypatch):
    seen = []
    from v17 import model_evidence_boundary as boundary
    monkeypatch.setattr(boundary, "EVIDENCE_ASSESSOR", lambda db, scope: seen.append(scope) or {"bundle_complete": False, "blockers": []})
    payload = _prop_payload(line="7+")
    boundary.gate_prop_probability_payload(payload, db=_EmptyDb())
    assert seen[0].line == "7+"
    assert seen[0].player_id == payload["player_id"]
    assert seen[0].period == payload["period"]
    assert seen[0].direction == payload["direction"]
