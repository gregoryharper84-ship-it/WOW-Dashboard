from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

import wnba_prop_auto_hydration as wnba
from v17 import wnba_official_schedule_web_fallback as bridge
from v17 import wnba_prop_evidence_control_plane as control

NOW = datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)
EVENT = datetime(2026, 9, 29, 22, 30, tzinfo=timezone.utc)


def _schedule() -> dict:
    return {
        "leagueSchedule": {
            "gameDates": [
                {
                    "games": [
                        {
                            "gameId": "espn-401900001",
                            "gameDateTimeUTC": "2026-09-29T22:30:00Z",
                            "gameStatus": 1,
                            "homeTeam": {
                                "teamId": "1611661325",
                                "teamTricode": "IND",
                                "teamName": "Indiana Fever",
                            },
                            "awayTeam": {
                                "teamId": "1611661319",
                                "teamTricode": "LVA",
                                "teamName": "Las Vegas Aces",
                            },
                        }
                    ]
                }
            ]
        }
    }


class _Response:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _espn_payload() -> dict:
    return {
        "events": [
            {
                "id": "401900001",
                "date": "2026-09-29T22:30:00Z",
                "status": {
                    "type": {
                        "state": "pre",
                        "name": "STATUS_SCHEDULED",
                        "description": "Scheduled",
                        "completed": False,
                    }
                },
                "competitions": [
                    {
                        "competitors": [
                            {
                                "homeAway": "home",
                                "team": {
                                    "id": "5",
                                    "displayName": "Indiana Fever",
                                    "shortDisplayName": "Fever",
                                    "location": "Indiana",
                                    "name": "Fever",
                                    "abbreviation": "IND",
                                },
                            },
                            {
                                "homeAway": "away",
                                "team": {
                                    "id": "17",
                                    "displayName": "Las Vegas Aces",
                                    "shortDisplayName": "Aces",
                                    "location": "Las Vegas",
                                    "name": "Aces",
                                    "abbreviation": "LVA",
                                },
                            },
                        ]
                    }
                ],
            }
        ]
    }


def _team_log_payload() -> dict:
    return {
        "resultSets": [
            {
                "name": "LeagueGameLog",
                "headers": ["TEAM_ID", "TEAM_ABBREVIATION", "TEAM_NAME"],
                "rowSet": [
                    [1611661325, "IND", "Indiana Fever"],
                    [1611661325, "IND", "Indiana Fever"],
                    [1611661319, "LVA", "Las Vegas Aces"],
                ],
            }
        ]
    }


def test_identity_fallback_reconciles_espn_to_official_wnba_team_ids():
    calls: list[str] = []

    def http_get(url, **kwargs):
        calls.append(str(url))
        if str(url) == control.ESPN_BASE_URLS["WNBA"]:
            return _Response(_espn_payload())
        if str(url).endswith("/leaguegamelog"):
            params = kwargs.get("params") or {}
            assert list(params)[0] == "LeagueID"
            assert params["LeagueID"] == "10"
            assert params["PlayerOrTeam"] == "T"
            return _Response(_team_log_payload())
        raise AssertionError(f"unexpected source {url}")

    payload = control._espn_identity_schedule_for_date(
        requested_date="2026-09-29",
        requested_timezone="America/Chicago",
        now=NOW,
        http_get=http_get,
    )

    assert payload is not None
    game = payload["leagueSchedule"]["gameDates"][0]["games"][0]
    assert game["gameId"] == "espn-401900001"
    assert game["homeTeam"]["teamId"] == "1611661325"
    assert game["homeTeam"]["teamTricode"] == "IND"
    assert game["awayTeam"]["teamId"] == "1611661319"
    assert game["awayTeam"]["teamTricode"] == "LVA"
    provenance = payload["wowScheduleProvenance"]
    assert provenance["provider"] == bridge.ESPN_IDENTITY_BRIDGE_PROVIDER
    assert provenance["market_features_used"] is False
    assert provenance["probability_authority"] is False
    assert provenance["can_execute"] is False
    assert calls == [control.ESPN_BASE_URLS["WNBA"], f"{wnba.WNBA_STATS_BASE}/leaguegamelog"]


def test_identity_fallback_treats_valid_empty_scoreboard_as_off_day():
    def http_get(url, **kwargs):
        assert str(url) == control.ESPN_BASE_URLS["WNBA"]
        return _Response({"events": []})

    payload = control._espn_identity_schedule_for_date(
        requested_date="2026-09-30",
        requested_timezone="America/Chicago",
        now=NOW,
        http_get=http_get,
    )
    assert payload is None


def test_request_scoped_espn_identity_override_preserves_provider_and_resets():
    calls = []

    def fail_network(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("identity override must bypass direct schedule transport")

    with bridge.official_schedule_override(
        _schedule(), provider=bridge.ESPN_IDENTITY_BRIDGE_PROVIDER
    ):
        payload = bridge.request_with_official_web_fallback(
            wnba.WNBA_SCHEDULE_URL,
            http_get=fail_network,
            headers=wnba._cdn_headers(),
        )
        assert payload["leagueSchedule"]["gameDates"]
        provider, source = bridge._SCHEDULE_SOURCE.get()
        assert provider == bridge.ESPN_IDENTITY_BRIDGE_PROVIDER
        assert source == bridge.ESPN_WNBA_SCOREBOARD_URL
        assert calls == []


def test_control_plane_recovers_after_typed_official_schedule_failure(monkeypatch: pytest.MonkeyPatch):
    observed: list[str] = []

    def request_schedule(**kwargs):
        if bridge._OFFICIAL_SCHEDULE_OVERRIDE.get() is not None:
            observed.append("override-discovery")
            return bridge.request_with_official_web_fallback(
                wnba.WNBA_SCHEDULE_URL,
                http_get=kwargs["http_get"],
                headers=wnba._cdn_headers(),
            )
        raise wnba.WNBAPropHydrationError(
            "WNBA_OFFICIAL_SOURCE_UNAVAILABLE",
            "official transports unavailable",
            detail={
                "primary_source": bridge.CDN_PROVIDER,
                "fallback_source": bridge.WEB_PROVIDER,
                "primary_errors": ["JSONDecodeError"],
                "primary_minimal_errors": ["JSONDecodeError"],
                "fallback_errors": ["WNBAPropHydrationError:WNBA_OFFICIAL_SCHEDULE_WEB_PARSE_EMPTY"],
            },
        )

    player = {
        "event_id": "WNBA:espn-401900001",
        "official_game_id": "espn-401900001",
        "event_start_time": EVENT.isoformat(),
        "player": "Alpha Guard",
        "player_id": "1001",
        "team": "IND",
        "opponent": "LVA",
    }

    monkeypatch.setattr(control.acquisition, "_request_schedule", request_schedule)
    monkeypatch.setattr(control, "_espn_identity_schedule_for_date", lambda **_k: _schedule())
    monkeypatch.setattr(control.acquisition, "_schedule_players", lambda *_a, **_k: [player])
    monkeypatch.setattr(control, "_existing_snapshot", lambda *_a, **_k: False)

    def hydrate(**kwargs):
        observed.append("hydration")
        schedule = bridge.request_with_official_web_fallback(
            wnba.WNBA_SCHEDULE_URL,
            http_get=kwargs["http_get"],
            headers=wnba._cdn_headers(),
        )
        assert schedule["leagueSchedule"]["gameDates"]
        return {
            "captured_at": NOW.isoformat(),
            "game_log": [float(i) for i in range(10)],
            "box_score_log": [{"game": i} for i in range(10)],
            "role_status": {
                "status": "ACTIVE",
                "team_tricode": "IND",
                "opponent_tricode": "LVA",
            },
            "role_timestamp": NOW.isoformat(),
            "opportunity_ledger": {"status": "READY", "availability_gate": "PASS"},
            "source_timestamps": {
                bridge.ESPN_IDENTITY_BRIDGE_PROVIDER: NOW.isoformat()
            },
            "evidence_version": "PROP_EVIDENCE_V1",
            "rate_provenance": "identity fallback test",
        }

    monkeypatch.setattr(control.acquisition, "auto_hydrate_prop_evidence", hydrate)
    monkeypatch.setattr(
        control.acquisition,
        "_validate_evidence",
        lambda row, stat: row.evidence.model_dump(),
    )
    monkeypatch.setattr(
        control.acquisition,
        "_snapshot_payload",
        lambda row, normalized: (
            f"snapshot-{row.stat_type}",
            "fingerprint",
            {
                "source_snapshot_id": f"snapshot-{row.stat_type}",
                "event_id": row.event_id,
                "event_start_time": row.event_start_time,
                "sport": row.sport,
                "player": row.player,
                "stat_type": row.stat_type,
                "line": row.line,
            },
        ),
    )

    class Query:
        data = []

        def upsert(self, *_a, **_k):
            return self

        def execute(self):
            return self

    class DB:
        def table(self, name):
            assert name == "wow_prop_evidence_snapshots"
            return Query()

    def fail_direct_network(*_a, **_k):
        raise AssertionError("recovered schedule must remain request-scoped")

    result = control.acquire_wnba_forward_evidence_batch(
        control.WNBAForwardEvidenceRequest(
            requested_date="2026-09-29",
            max_candidates=1,
        ),
        db=DB(),
        now=NOW,
        http_get=fail_direct_network,
    )
    assert observed == ["override-discovery", "hydration"]
    assert result["persisted"] == 1
    assert result["schedule_source_provider"] == bridge.ESPN_IDENTITY_BRIDGE_PROVIDER
    assert any(
        item.get("code") == "WNBA_IDENTITY_FALLBACK_USED"
        for item in result["source_diagnostics"]
    )
    assert result["probability_publishable"] is False
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["can_execute"] is False


def test_priority_lifecycle_continues_to_backend_when_cdn_bridge_validation_fails():
    repo_root = Path(__file__).resolve().parents[3]
    text = (repo_root / ".github" / "workflows" / "wow-v17-priority-prop-lifecycle.yml").read_text()
    assert "falling back to backend governed identity recovery" in text
    assert 'if [ "${wnba_bridge_ready}" -eq 1 ]; then' in text
    assert "backend identity-recovery transport was ambiguous" in text
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text
