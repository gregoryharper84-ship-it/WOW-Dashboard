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
                            "gameId": "1042600122",
                            "gameDateTimeUTC": "2026-09-29T22:30:00Z",
                            "gameStatus": 1,
                            "homeTeam": {"teamId": "1611661325", "teamTricode": "IND"},
                            "awayTeam": {"teamId": "1611661319", "teamTricode": "LVA"},
                        }
                    ]
                }
            ]
        }
    }


def test_request_scoped_bridge_bypasses_render_network_and_resets():
    calls = []

    def fail_network(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("official schedule override must bypass Render egress")

    with bridge.official_schedule_override(
        _schedule(), provider=bridge.OIDC_BRIDGE_PROVIDER
    ):
        first = bridge.request_with_official_web_fallback(
            wnba.WNBA_SCHEDULE_URL,
            http_get=fail_network,
            headers=wnba._cdn_headers(),
        )
        first["leagueSchedule"]["gameDates"][0]["games"][0]["gameId"] = "mutated"
        second = bridge.request_with_official_web_fallback(
            wnba.WNBA_SCHEDULE_URL,
            http_get=fail_network,
            headers=wnba._cdn_headers(),
        )
        assert second["leagueSchedule"]["gameDates"][0]["games"][0]["gameId"] == "1042600122"
        assert bridge._SCHEDULE_SOURCE.get()[0] == bridge.OIDC_BRIDGE_PROVIDER
        assert calls == []

    # Outside the request scope the bridge is gone and direct acquisition resumes.
    def direct(url, **kwargs):
        calls.append((url, kwargs))

        class Response:
            status_code = 200

            def json(self):
                return _schedule()

        return Response()

    payload = bridge.request_with_official_web_fallback(
        wnba.WNBA_SCHEDULE_URL,
        http_get=direct,
        headers=wnba._cdn_headers(),
    )
    assert payload["leagueSchedule"]["gameDates"]
    assert len(calls) == 1


def test_bridge_rejects_unknown_provider_and_malformed_payload():
    with pytest.raises(wnba.WNBAPropHydrationError) as provider_exc:
        with bridge.official_schedule_override(_schedule(), provider="UNTRUSTED"):
            pass
    assert provider_exc.value.code == "WNBA_OIDC_SCHEDULE_PROVIDER_INVALID"

    with pytest.raises(wnba.WNBAPropHydrationError) as payload_exc:
        with bridge.official_schedule_override(
            {"leagueSchedule": {"gameDates": []}},
            provider=bridge.OIDC_BRIDGE_PROVIDER,
        ):
            pass
    assert payload_exc.value.code == "WNBA_OIDC_SCHEDULE_PAYLOAD_INVALID"


def test_control_plane_bridge_covers_discovery_and_nested_hydration(monkeypatch: pytest.MonkeyPatch):
    observed = []

    def request_schedule(**kwargs):
        observed.append("discovery")
        return bridge.request_with_official_web_fallback(
            wnba.WNBA_SCHEDULE_URL,
            http_get=kwargs["http_get"],
            headers=wnba._cdn_headers(),
        )

    player = {
        "event_id": "WNBA:1042600122",
        "official_game_id": "1042600122",
        "event_start_time": EVENT.isoformat(),
        "player": "Alpha Guard",
        "player_id": "1001",
        "team": "LVA",
        "opponent": "IND",
    }

    monkeypatch.setattr(control.acquisition, "_request_schedule", request_schedule)
    monkeypatch.setattr(control.acquisition, "_schedule_players", lambda *_a, **_k: [player])

    def hydrate(**kwargs):
        observed.append("hydration")
        bridged = bridge.request_with_official_web_fallback(
            wnba.WNBA_SCHEDULE_URL,
            http_get=kwargs["http_get"],
            headers=wnba._cdn_headers(),
        )
        assert bridged["leagueSchedule"]["gameDates"]
        return {
            "captured_at": NOW.isoformat(),
            "game_log": [float(i) for i in range(10)],
            "box_score_log": [{"game": i} for i in range(10)],
            "role_status": {"status": "ACTIVE", "team_tricode": "LVA", "opponent_tricode": "IND"},
            "role_timestamp": NOW.isoformat(),
            "opportunity_ledger": {"status": "READY", "availability_gate": "PASS"},
            "source_timestamps": {bridge.OIDC_BRIDGE_PROVIDER: NOW.isoformat()},
            "evidence_version": "PROP_EVIDENCE_V1",
            "rate_provenance": "official WNBA OIDC bridge test",
        }

    monkeypatch.setattr(control.acquisition, "auto_hydrate_prop_evidence", hydrate)
    monkeypatch.setattr(control.acquisition, "_validate_evidence", lambda row, stat: row.evidence.model_dump())
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
    monkeypatch.setattr(control, "_existing_snapshot", lambda *_a, **_k: False)

    class Query:
        def __init__(self):
            self.data = []
        def upsert(self, *_a, **_k):
            return self
        def execute(self):
            return self

    class DB:
        def table(self, name):
            assert name == "wow_prop_evidence_snapshots"
            return Query()

    def fail_network(*_a, **_k):
        raise AssertionError("bridged schedule must not use Render network")

    result = control.acquire_wnba_forward_evidence_batch(
        control.WNBAForwardEvidenceRequest(
            requested_date="2026-09-29",
            max_candidates=1,
            official_schedule_provider=bridge.OIDC_BRIDGE_PROVIDER,
            official_schedule=_schedule(),
        ),
        db=DB(),
        now=NOW,
        http_get=fail_network,
    )
    assert observed == ["discovery", "hydration"]
    assert result["persisted"] == 1
    assert result["schedule_source_provider"] == bridge.OIDC_BRIDGE_PROVIDER
    assert result["probability_publishable"] is False
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["can_execute"] is False


def test_control_plane_bridge_invalid_provider_fails_closed():
    result = control.acquire_wnba_forward_evidence_batch(
        control.WNBAForwardEvidenceRequest(
            requested_date="2026-09-29",
            max_candidates=1,
            official_schedule_provider="UNTRUSTED",
            official_schedule=_schedule(),
        ),
        db=object(),
        now=NOW,
        http_get=lambda *_a, **_k: object(),
    )
    assert result["status"] == "DATA_UNOBTAINABLE"
    assert result["blockers"] == ["WNBA_OIDC_SCHEDULE_PROVIDER_INVALID"]
    assert result["can_execute"] is False


def test_priority_lifecycle_fetches_only_official_cdn_and_posts_file_backed_bridge():
    repo_root = Path(__file__).resolve().parents[3]
    text = (repo_root / ".github" / "workflows" / "wow-v17-priority-prop-lifecycle.yml").read_text()
    assert "https://cdn.wnba.com/static/json/staticData/scheduleLeagueV2.json" in text
    assert "WNBA_CDN_SCHEDULE_GITHUB_OIDC_BRIDGE" in text
    assert "--data-binary \"@${request_file}\"" in text
    assert "official_schedule_provider" in text
    assert "WOW_CAN_EXECUTE: \"false\"" in text
    assert "WOW_DRY_RUN_ONLY: \"true\"" in text
