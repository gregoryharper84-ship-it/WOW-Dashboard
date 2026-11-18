"""Exact-head negative regressions for the V17 scoring-stage authority boundaries.

All fixtures remain research-only. No fitted probabilities, certification,
publication, or execution authority are created by these tests.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from v17 import daily_snapshot_runtime as daily
from v17 import nba_current_pregame_scorer as nba
from v17 import team_event_bridge_runtime as bridges
from v17.team_event_request_runtime import TeamEventRequest


_START = "2026-11-20T17:00:00Z"
_PRIOR = "2026-11-20T16:00:00Z"


def _server_payload(as_of: str = _PRIOR, *, with_lineup: bool = True):
    out = {
        "feature_package": {
            "source_snapshot_id": "server-snapshot",
            "feature_audit": {
                "server_owned": True,
                "timestamp_attestation": "VERIFIED",
                "source_replay_status": "RAW_CAPTURE_HASH_AND_EVENT_REPLAY_PASS",
                "as_of": as_of,
            },
        },
    }
    if with_lineup:
        out["lineup_evidence"] = {
            "injury_report": {"status": "OFFICIAL"},
            "latest_material_update_timestamp": as_of,
        }
    return out


def _nba_request():
    return TeamEventRequest(
        requester_host_identity="WOW_BETTING_ENGINE",
        research_run_id="nba-authority-regression",
        requested_slate_date="2026-11-20",
        requested_timezone="UTC",
        candidate_family="OUTRIGHT_WINNER",
        decision_intent="BEST_SIDE",
        event_key="NBA:401999001",
        sport="NBA",
        league="NBA",
        official_event_id="espn-401999001",
        home_team="espn-1",
        away_team="espn-2",
        event_start_time_utc=_START,
        settlement_basis="FULL_GAME_INCLUDING_OVERTIME",
        source_snapshot_id="caller-snapshot",
        sport_specific_evidence={
            "source_snapshot_id": "caller-snapshot",
            "lineup_evidence": {
                "injury_report": {"status": "OFFICIAL"},
                "expected_starters_rotation": "CONFIRMED",
                "latest_material_update_timestamp": _PRIOR,
            },
        },
    )


def test_certification_cannot_be_counted_from_caller_flags_or_fake_id():
    for alleged in (
        {"certified": True},
        {"model_certified": True},
        {"certification_status": "PASS"},
        {"certification_id": "fake-id"},
        {"certification_receipt": {"status": "CERTIFIED"}},
    ):
        row = {"lane": "MONEYLINE", "row_status": "HELD", "result": {
            "model_invoked": True, "code": "SOME_UNKNOWN_CODE", **alleged,
        }}
        assert daily._is_row_certified(row, evaluated=True) is False


def test_unknown_result_does_not_imply_hydration():
    hydrated, _ = daily._is_row_hydrated({"result": {"code": "UNKNOWN", "model_invoked": True}})
    assert hydrated is False


def test_model_unavailable_does_not_erase_verified_upstream_hydration():
    row = {"result": {
        "code": "MODEL_UNAVAILABLE",
        "feature_consumption_receipt": {
            "consumption_verification_status": "VERIFIED",
            "receipt_complete": True,
        },
    }}
    assert daily._is_row_hydrated(row)[0] is True
    assert daily._is_row_model_evaluated(row, hydrated=True) is False


def test_ncaab_missing_server_source_is_explicit_not_silently_import_held(monkeypatch):
    monkeypatch.setattr(bridges._base_runtime, "_run_mandatory_scout_research",
                        lambda _req: {"pass_scout_research_barrier": True})
    req = SimpleNamespace(
        sport="NCAAB", league="NCAAB", official_event_id="NCAAB:123",
        event_key="NCAAB:123", home_team="Alpha", away_team="Beta",
        event_start_time_utc=_START, sport_specific_evidence={},
    )
    with pytest.raises(HTTPException) as got:
        bridges.score_ncaab_team_event_request(
            req, event_api=SimpleNamespace(get_client=lambda: object()),
            canonical_hydration_required=True,
        )
    assert "NCAAB_CANONICAL_SERVER_HYDRATION_UNAVAILABLE" in got.value.detail["blockers"]
    assert got.value.detail["model_invoked"] is False


@pytest.mark.parametrize("as_of", [
    "2026-11-20T12:00:00-06:00",  # 18Z, one hour after 17Z tipoff
    "2026-11-20T17:00:00Z",       # exactly at tipoff
    "2026-11-20T16:00:00",        # timezone missing
    "not-a-time",
])
def test_nba_server_provenance_rejects_post_tipoff_and_invalid_clock(as_of):
    assert nba._validated_server_hydration(_server_payload(as_of), _START) is None


def test_nba_server_hydration_requires_own_lineup_not_caller_fallback(monkeypatch):
    monkeypatch.setattr(nba, "prepare_nba_current_pregame_scorer_receipt",
                        lambda **_kw: pytest.fail("unsafe caller lineup reached scorer"))
    with pytest.raises(HTTPException) as got:
        nba.score_nba_team_event_request(
            _nba_request(),
            event_api=SimpleNamespace(hydrate_nba_features=lambda _req: _server_payload(with_lineup=False)),
            canonical_hydration_required=True,
        )
    assert got.value.status_code == 422
    assert "NBA_CANONICAL_SERVER_HYDRATION_UNAVAILABLE" in got.value.detail["blockers"]


def test_nba_verified_server_snapshot_id_wins_over_caller_id(monkeypatch):
    observed = {}
    def capture(**kw):
        observed.update(kw)
        return {"status": "HOLD", "typed_failure": "MODEL_INPUTS_INSUFFICIENT",
                "blockers": ["EXPECTED_TEST_HOLD"]}
    monkeypatch.setattr(nba, "prepare_nba_current_pregame_scorer_receipt", capture)
    with pytest.raises(HTTPException):
        nba.score_nba_team_event_request(
            _nba_request(),
            event_api=SimpleNamespace(hydrate_nba_features=lambda _req: _server_payload()),
            canonical_hydration_required=True,
        )
    assert observed["source_snapshot_id"] == "server-snapshot"
    assert observed["lineup_evidence"]["injury_report"]["status"] == "OFFICIAL"


def test_nba_server_hydration_requires_lineup_and_source_id():
    assert nba._validated_server_hydration(_server_payload(with_lineup=False), _START) is None
    missing_source = _server_payload()
    missing_source["feature_package"].pop("source_snapshot_id")
    assert nba._validated_server_hydration(missing_source, _START) is None
