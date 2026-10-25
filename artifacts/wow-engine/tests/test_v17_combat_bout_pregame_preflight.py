"""MMA and Boxing cannot share fitted coefficients or infer fight winners."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import pytest

from v17.combat_bout_pregame_preflight import (
    CombatEvidenceBlocked, preflight_combat_bout,
)

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
OUTCOMES = ["FIGHTER_A", "FIGHTER_B", "DRAW", "NO_CONTEST"]


def fixture(sport):
    participants = ["f1", "f2"]
    payload = {"official_event_id": "event-1", "official_bout_id": "bout-1",
               "official_fighter_ids": participants}
    checksum = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                allow_nan=False).encode()).hexdigest()
    return {
        "source_id": "UFCSTATS_PUBLIC" if sport == "MMA" else "BOXING_DATA_API",
        "official_event_id": "event-1", "official_bout_id": "bout-1",
        "source_snapshot_id": "source-1", "event_start_time": "2026-10-25T12:00:00Z",
        "source_timestamp": "2026-10-24T11:00:00Z",
        "scheduled_rounds": 3 if sport == "MMA" else 12,
        "weight_class": "Welterweight", "outcome_space": OUTCOMES,
        "source_payload": payload, "source_payload_sha256": checksum,
        "participants": [{
            "official_fighter_id": ident,
            "participation_status": "CONFIRMED",
            "weigh_in_status": "OFFICIAL",
            "feature_as_of": "2026-10-24T10:00:00Z",
            "historical_features": {"past_striking_rate": 1.5 + i},
        } for i, ident in enumerate(participants)],
    }


def authorized_boxing(monkeypatch):
    monkeypatch.setenv("BOXING_DATA_API_KEY", "test-key")
    monkeypatch.setenv("WOW_BOXING_DATA_TRAINING_ENTITLED", "approved")


def blocked(sport, data, code):
    with pytest.raises(CombatEvidenceBlocked) as exc:
        preflight_combat_bout(sport, data, now=NOW)
    assert exc.value.code == code


def test_mma_pregame_evidence_is_not_a_fitted_probability():
    value = preflight_combat_bout("MMA", fixture("MMA"), now=NOW)
    assert value["sport"] == "MMA"
    assert value["model_probability"] is None
    assert value["independent_source_review_required"] is True
    assert value["can_execute"] is False
    assert value["probability_publishable"] is False


def test_boxing_has_independent_rights_and_identity(monkeypatch):
    authorized_boxing(monkeypatch)
    value = preflight_combat_bout("BOXING", fixture("BOXING"), now=NOW)
    assert value["sport"] == "BOXING"
    assert value["probability_publishable"] is False


def test_boxing_cannot_train_without_rights(monkeypatch):
    monkeypatch.delenv("BOXING_DATA_API_KEY", raising=False)
    monkeypatch.delenv("RAPIDAPI_KEY", raising=False)
    monkeypatch.delenv("WOW_BOXING_DATA_TRAINING_ENTITLED", raising=False)
    blocked("BOXING", fixture("BOXING"), "COMBAT_SOURCE_RIGHTS_OR_CREDENTIALS_MISSING")


def test_cross_sport_model_source_is_rejected():
    blocked("MMA", fixture("BOXING"), "COMBAT_SOURCE_SPORT_MISMATCH")


def test_identity_rounds_and_draw_no_contest_cannot_be_silently_inferred():
    row = fixture("MMA")
    row["scheduled_rounds"] = 12
    blocked("MMA", row, "COMBAT_ROUNDS_OR_RULESET_UNSUPPORTED")
    row = fixture("MMA")
    row["outcome_space"] = OUTCOMES[:2]
    blocked("MMA", row, "COMBAT_DRAW_NO_CONTEST_RULES_UNRESOLVED")
    row = fixture("MMA")
    row["participants"][1]["official_fighter_id"] = "f1"
    blocked("MMA", row, "COMBAT_DUPLICATE_FIGHTER_ID")


def test_participant_status_and_temporal_source_checks():
    row = fixture("MMA")
    row["participants"][1]["weigh_in_status"] = "PENDING"
    blocked("MMA", row, "COMBAT_FIGHTER_STATUS_NOT_VERIFIED")
    row = fixture("MMA")
    row["participants"][1]["feature_as_of"] = "2026-10-26T01:00:00Z"
    blocked("MMA", row, "COMBAT_POST_SNAPSHOT_FEATURE_LEAKAGE")


def test_no_market_inputs_tampered_source_or_nonfinite_features():
    row = fixture("MMA")
    row["participants"][0]["historical_features"]["sportsbook"] = 3.5
    blocked("MMA", row, "COMBAT_MARKET_FEATURE_FORBIDDEN")
    row = fixture("MMA")
    row["source_payload"]["official_bout_id"] = "wrong"
    blocked("MMA", row, "COMBAT_SOURCE_HASH_MISMATCH")
    row = fixture("MMA")
    row["participants"][0]["historical_features"]["past_striking_rate"] = float("nan")
    blocked("MMA", row, "COMBAT_HISTORICAL_FEATURE_INVALID")
