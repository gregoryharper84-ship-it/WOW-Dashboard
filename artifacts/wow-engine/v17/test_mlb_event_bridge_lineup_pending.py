"""Regressions separating an unposted lineup from a missing canonical snapshot.

A future MLB event whose forward-shadow snapshot is present and PASS-hydrated
but whose official lineup has not been posted yet returned
``MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_UNAVAILABLE``. That reads as a broken
canonical producer, and production was misdiagnosed on it for three status
cycles while the producer was in fact working.

Both states stay fail-closed at 422 ``MODEL_INPUTS_INSUFFICIENT`` and neither
publishes a probability; only the blocker taxonomy distinguishes them.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from v17 import mlb_event_bridge_repair as repair

EVENT_ID = "824224"
SNAPSHOT_ID = "b5693e27-5c19-4e0b-a870-dba334e1a4a4"
SHADOW_EVENT_ID = "93cfff90-9e51-4dd3-aab7-a31f7f981e4e"


class _Query:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def select(self, *_a, **_k) -> "_Query":
        return self

    def eq(self, *_a, **_k) -> "_Query":
        return self

    def order(self, *_a, **_k) -> "_Query":
        return self

    def limit(self, *_a, **_k) -> "_Query":
        return self

    def execute(self) -> Any:
        return SimpleNamespace(data=list(self._rows))


class _Client:
    def __init__(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        self._tables = tables

    def table(self, name: str) -> _Query:
        return _Query(self._tables.get(name, []))


def _event_api(tables: dict[str, list[dict[str, Any]]]) -> Any:
    return SimpleNamespace(get_client=lambda: _Client(tables))


def _shadow_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "shadow_event_id": SHADOW_EVENT_ID,
        "spec_id": "spec-1",
        "official_event_id": EVENT_ID,
        "official_date": "2026-09-12",
        "event_start_time": "2026-09-12T17:10:00+00:00",
        "event_status": "Scheduled",
        "home_team": "Detroit Tigers",
        "away_team": "Colorado Rockies",
        "venue_name": "Comerica Park",
        "home_probable_pitcher": "Andrew Sears",
        "away_probable_pitcher": "Tanner Gordon",
        "snapshot_id": SNAPSHOT_ID,
        "snapshot_timestamp": "2026-09-12T06:02:00+00:00",
        "feature_hydration_status": "PASS",
        "lineup_status": "NOT_YET_AVAILABLE",
        "lineup_snapshot_id": None,
        "lineup_confirmed_at": None,
    }
    row.update(overrides)
    return row


def _req() -> Any:
    return SimpleNamespace(
        official_event_id=EVENT_ID,
        source_snapshot_id=SNAPSHOT_ID,
        research_run_id="rr-lineup-pending",
        event_key=f"MLB:{EVENT_ID}",
    )


def test_hydrated_snapshot_awaiting_lineup_is_not_reported_as_a_missing_snapshot():
    resolution = repair._resolve_bridge_payload(
        _req(),
        event_api=_event_api({
            "wow_mlb_forward_shadow_events": [_shadow_row()],
            "wow_mlb_forward_lineup_snapshots": [],
        }),
    )

    assert resolution["ok"] is False
    assert resolution["status"] == "MODEL_INPUTS_INSUFFICIENT"
    assert resolution["blocker_code"] == "MLB_TEAM_EVENT_LINEUP_NOT_YET_AVAILABLE"
    assert resolution["canonical_snapshot_present"] is True
    assert resolution["lineup_status"] == "NOT_YET_AVAILABLE"
    assert resolution["missing_fields"] == ["away_lineup_status", "home_lineup_status"]
    assert resolution["sport_model_invoked"] is False


def test_genuinely_absent_canonical_snapshot_keeps_its_own_blocker():
    resolution = repair._resolve_bridge_payload(
        _req(),
        event_api=_event_api({
            "wow_mlb_forward_shadow_events": [],
            "wow_mlb_forward_lineup_snapshots": [],
        }),
    )

    assert resolution["blocker_code"] == "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_UNAVAILABLE"


def test_incomplete_canonical_identity_is_not_downgraded_to_a_lineup_wait():
    resolution = repair._resolve_bridge_payload(
        _req(),
        event_api=_event_api({
            "wow_mlb_forward_shadow_events": [_shadow_row(venue_name="", feature_hydration_status="FAIL")],
            "wow_mlb_forward_lineup_snapshots": [],
        }),
    )

    assert resolution["blocker_code"] == "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_UNAVAILABLE"
    assert resolution["canonical_snapshot_present"] is False
    assert "venue_name" in resolution["missing_fields"]
    assert "feature_hydration_status" in resolution["missing_fields"]


def test_lineup_wait_still_fails_closed_at_the_route_without_publishing():
    with pytest.raises(HTTPException) as caught:
        repair.score_event_v17_bridge(
            _req(),
            event_api=_event_api({
                "wow_mlb_forward_shadow_events": [_shadow_row()],
                "wow_mlb_forward_lineup_snapshots": [],
            }),
            scorer_override=lambda *a, **k: pytest.fail("scorer must not run without a lineup"),
        )

    detail = caught.value.detail
    assert caught.value.status_code == 422
    assert detail["status"] == "MODEL_INPUTS_INSUFFICIENT"
    assert detail["blocker_code"] == "MLB_TEAM_EVENT_LINEUP_NOT_YET_AVAILABLE"
    assert detail["canonical_snapshot_present"] is True
    assert detail["probability_publishable"] is False
    assert detail["rank_eligible"] is False
    assert detail["market_probability_substitution_allowed"] is False
    assert detail["can_execute"] is False
