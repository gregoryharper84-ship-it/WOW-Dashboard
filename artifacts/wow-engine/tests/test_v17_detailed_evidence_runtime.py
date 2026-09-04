from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from v17.detailed_evidence_runtime import (
    DetailedEvidenceEnvelope,
    compile_feature_candidates,
    evidence_fingerprint,
    evidence_summary,
    validate_detailed_evidence,
)


def _family(status="AVAILABLE", items=None):
    return {
        "status": status,
        "sample_window": "L10",
        "sample_size": 10 if status == "AVAILABLE" else 0,
        "data_quality": 0.9 if status == "AVAILABLE" else None,
        "certainty": 0.85 if status == "AVAILABLE" else None,
        "items": items or [],
    }


def _packet(*, event_id="evt-1", sport="MLB", now=None):
    now = now or datetime.now(timezone.utc)
    as_of = (now - timedelta(minutes=2)).isoformat()
    model_item = {
        "name": "recent_rate",
        "value": 0.24,
        "feature_status": "MODEL_INPUT",
        "source": "official-stats",
        "source_type": "OFFICIAL",
        "as_of": as_of,
        "sample_size": 10,
        "data_quality": 0.95,
        "certainty": 0.9,
    }
    market_item = {
        "name": "market_move_context",
        "value": -12,
        "feature_status": "MARKET_EVIDENCE",
        "source": "sportsbook",
        "source_type": "MARKET",
        "as_of": as_of,
    }
    evidence_only = {
        "name": "narrative_style_note",
        "value": "high press",
        "feature_status": "EVIDENCE_ONLY",
        "source": "scout-report",
        "source_type": "RESEARCH",
        "as_of": as_of,
    }
    return {
        "candidate_id": "cand-1",
        "sport": sport,
        "event_id": event_id,
        "as_of": as_of,
        "controlling_specialist": "TEST_SPECIALIST",
        "evidence_families": {
            "recent_form": _family(items=[model_item]),
            "head_to_head": _family("NOT_APPLICABLE"),
            "player_performance": _family(),
            "lineup_availability_depth": _family(),
            "tactical_style": _family(items=[evidence_only]),
            "match_context_stakes": _family(),
            "environment": _family(),
            "officiating": _family(),
            "schedule_fatigue_travel": _family(),
            "advanced_statistics": _family(items=[market_item]),
        },
        "market_evidence": {
            "market_state": "EXACT_LINE",
            "platform": "TEST_BOOK",
            "market": "TEST",
            "line": 5.5,
            "price": -110,
            "source": "sportsbook",
            "as_of": as_of,
            "implied_probability": 0.5238,
        },
        "source_conflicts": [],
        "lineup_certainty": 0.8,
        "role_certainty": 0.9,
        "starter_certainty": 0.95,
        "evidence_quality": 0.91,
        "notes": [],
    }


def test_valid_packet_is_pregame_identity_locked_and_summarized():
    now = datetime.now(timezone.utc)
    packet = _packet(now=now)
    event_start = (now + timedelta(hours=3)).isoformat()

    env = validate_detailed_evidence(
        packet,
        event_id="evt-1",
        sport="MLB",
        event_start_time=event_start,
        now=now,
    )
    summary = evidence_summary(env)

    assert summary["status"] == "VALIDATED"
    assert summary["probability_claimed"] is False
    assert summary["can_execute"] is False
    assert summary["feature_status_counts"]["MODEL_INPUT"] == 1
    assert summary["feature_status_counts"]["MARKET_EVIDENCE"] == 1


def test_market_and_evidence_only_items_never_enter_model_feature_candidates():
    env = DetailedEvidenceEnvelope.model_validate(_packet())
    compiled = compile_feature_candidates(env)

    assert "recent_form.recent_rate" in compiled["MODEL_INPUT"]
    all_numeric_candidates = {
        **compiled["MODEL_INPUT"],
        **compiled["REGIME_INPUT"],
        **compiled["CALIBRATION_INPUT"],
    }
    assert "advanced_statistics.market_move_context" not in all_numeric_candidates
    assert "tactical_style.narrative_style_note" not in all_numeric_candidates
    assert compiled["market_evidence_forwarded_to_model"] is False
    assert compiled["numerical_authority"] == "CONTROLLING_SPECIALIST_ADAPTER_ONLY"


def test_identity_mismatch_fails_closed():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="DETAILED_EVIDENCE_EVENT_ID_MISMATCH"):
        validate_detailed_evidence(
            _packet(now=now),
            event_id="different-event",
            sport="MLB",
            event_start_time=(now + timedelta(hours=2)).isoformat(),
            now=now,
        )


def test_post_start_evidence_fails_closed():
    now = datetime.now(timezone.utc)
    event_start = now + timedelta(hours=1)
    packet = _packet(now=now)
    packet["evidence_families"]["recent_form"]["items"][0]["as_of"] = (
        event_start + timedelta(seconds=1)
    ).isoformat()

    with pytest.raises(ValueError, match="DETAILED_EVIDENCE_ITEM_AS_OF_IN_FUTURE|DETAILED_EVIDENCE_ITEM_NOT_PREGAME"):
        validate_detailed_evidence(
            packet,
            event_id="evt-1",
            sport="MLB",
            event_start_time=event_start.isoformat(),
            now=now + timedelta(hours=2),
        )


def test_governed_probability_field_cannot_be_smuggled_as_evidence():
    packet = _packet()
    packet["evidence_families"]["recent_form"]["items"][0]["name"] = "calibrated_probability"

    with pytest.raises(ValidationError, match="GOVERNED_OUTPUT_FIELD_NOT_ALLOWED_AS_EVIDENCE"):
        DetailedEvidenceEnvelope.model_validate(packet)


def test_fingerprint_is_stable_and_material_evidence_change_moves_it():
    packet = _packet()
    first = evidence_fingerprint(packet)
    second = evidence_fingerprint(packet)
    assert first == second

    changed = _packet()
    changed["evidence_families"]["recent_form"]["items"][0]["value"] = 0.31
    assert evidence_fingerprint(changed) != first
