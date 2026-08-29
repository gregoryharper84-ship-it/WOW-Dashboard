from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from ncaaf_evidence_readiness import EVENT_SCOPED_KINDS, REQUIRED_SCOPES, TEAM_SCOPED_KINDS
from ncaaf_feature_builder import NCAAFFeatureBuildUnavailable, build_training_feature_row


def _payload(kind: str, scope: str) -> dict:
    if kind == "WEATHER":
        return {"temperature_f": 82.0, "wind_mph": 8.0, "precip_probability": 0.2}
    if kind == "REST_TRAVEL":
        return {"rest_days": 7.0, "travel_distance_miles": 650.0 if scope == "AWAY" else 0.0}
    if kind in {"QB_CERTAINTY", "OL_HEALTH", "DEF_FRONT_HEALTH", "SKILL_AVAILABILITY"}:
        return {"value": 0.9}
    if kind == "QB_STATUS":
        return {"starter": "QB Example", "status": "CONFIRMED"}
    if kind == "TURNOVER_VOLATILITY":
        return {"value": 0.4}
    return {"value": 1.25}


def _row(event_id: str, kickoff: datetime, kind: str, scope: str, i: int) -> dict:
    payload = _payload(kind, scope)
    raw = f"{event_id}|{kind}|{scope}|{i}".encode()
    return {
        "evidence_id": f"e-{i}",
        "official_event_id": event_id,
        "event_start_time": kickoff.isoformat(),
        "evidence_kind": kind,
        "scope": scope,
        "source_provider": "TEST_PROVIDER",
        "evidence_timestamp": (kickoff - timedelta(hours=3, minutes=i)).isoformat(),
        "provenance_grade": "A",
        "payload": payload,
        "payload_sha256": hashlib.sha256(raw).hexdigest(),
        "blocker_codes": [],
    }


def _complete(event_id: str, kickoff: datetime) -> list[dict]:
    rows: list[dict] = []
    i = 0
    for kind in TEAM_SCOPED_KINDS:
        for scope in REQUIRED_SCOPES:
            rows.append(_row(event_id, kickoff, kind, scope, i))
            i += 1
    for kind in EVENT_SCOPED_KINDS:
        rows.append(_row(event_id, kickoff, kind, "EVENT", i))
        i += 1
    return rows


def test_builder_materializes_complete_governed_row():
    kickoff = datetime(2026, 9, 12, 23, 0, tzinfo=timezone.utc)
    rows = _complete("cfb-build-1", kickoff)
    built = build_training_feature_row(
        training_game_id="training-1",
        official_event_id="cfb-build-1",
        event_start_time=kickoff.isoformat(),
        rows=rows,
    )
    assert built["feature_schema_version"] == "NCAAF_FEATURES_V1"
    assert built["home_qb_certainty"] == 0.9
    assert built["away_rest_days"] == 7.0
    assert built["travel_distance_miles"] == 650.0
    assert built["weather_precip_probability"] == 0.2
    assert built["market_home_no_vig"] is None
    assert built["can_execute"] is False
    assert built["feature_source_manifest"]["provider_set"] == ["TEST_PROVIDER"]


def test_builder_refuses_incomplete_evidence():
    kickoff = datetime(2026, 9, 12, 23, 0, tzinfo=timezone.utc)
    rows = [r for r in _complete("cfb-build-2", kickoff) if not (r["evidence_kind"] == "QB_STATUS" and r["scope"] == "HOME")]
    with pytest.raises(NCAAFFeatureBuildUnavailable) as exc:
        build_training_feature_row(
            training_game_id="training-2",
            official_event_id="cfb-build-2",
            event_start_time=kickoff.isoformat(),
            rows=rows,
        )
    assert exc.value.code == "NCAAF_PREGAME_EVIDENCE_INCOMPLETE"


def test_builder_refuses_invalid_health_range():
    kickoff = datetime(2026, 9, 12, 23, 0, tzinfo=timezone.utc)
    rows = _complete("cfb-build-3", kickoff)
    for row in rows:
        if row["evidence_kind"] == "OL_HEALTH" and row["scope"] == "HOME":
            row["payload"] = {"value": 1.2}
    with pytest.raises(NCAAFFeatureBuildUnavailable) as exc:
        build_training_feature_row(
            training_game_id="training-3",
            official_event_id="cfb-build-3",
            event_start_time=kickoff.isoformat(),
            rows=rows,
        )
    assert exc.value.code == "NCAAF_EVIDENCE_VALUE_INVALID"
