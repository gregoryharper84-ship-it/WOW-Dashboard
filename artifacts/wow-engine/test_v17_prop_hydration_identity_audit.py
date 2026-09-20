from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import prop_auto_hydration_router as router
from prop_auto_hydration import PropAutoHydrationError
import pick_request_runtime as runtime
import v17.daily_prop_acquisition as daily
import v17.interactive_pick_hydration as interactive


def _raw_evidence(*, role_status: dict | None = None) -> dict:
    captured = datetime.now(timezone.utc) - timedelta(minutes=5)
    return {
        "captured_at": captured.isoformat(),
        "game_log": [1.0] * 10,
        "box_score_log": [{"date": f"2026-09-{day:02d}"} for day in range(1, 11)],
        "role_status": role_status or {"status": "READY"},
        "role_timestamp": captured.isoformat(),
        "opportunity_ledger": {"status": "READY"},
        "source_timestamps": {"TEST_SOURCE": captured.isoformat()},
        "evidence_version": "PROP_EVIDENCE_V1",
        "rate_provenance": "TEST_ONLY",
    }


def test_nfl_canonical_event_is_bound_while_espn_id_remains_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        router._nfl,
        "hydrate_nfl_prop_evidence",
        lambda **_kwargs: {
            **_raw_evidence(),
            "role_status": {
                "status": "ACTIVE_CURRENT_ESPN_ROSTER",
                "team": "IND",
                "opponent": "KC",
                "event_id": "401999999",
            },
            "hydration_provider": router.NFL_PROVIDER,
        },
    )

    result = router.auto_hydrate_prop_evidence(
        sport="NFL",
        player="Test Colt",
        stat_type="PASSING_YARDS",
        event_start_time="2026-09-21T00:00:00+00:00",
        opponent="Kansas City Chiefs",
        canonical_event_id="WOW:NFL:COLTS-CHIEFS:2026-09-20",
    )

    role = result["role_status"]
    assert role["canonical_event_id"] == "WOW:NFL:COLTS-CHIEFS:2026-09-20"
    assert role["provider_event_ids"] == {"ESPN": "401999999"}
    assert role["event_id"] == "401999999"  # backward-compatible ESPN alias
    assert role["identity_binding_status"] == "PASS"


def test_nfl_requested_opponent_mismatch_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        router._nfl,
        "hydrate_nfl_prop_evidence",
        lambda **_kwargs: {
            **_raw_evidence(),
            "role_status": {
                "status": "ACTIVE_CURRENT_ESPN_ROSTER",
                "team": "IND",
                "opponent": "KC",
                "event_id": "401999999",
            },
        },
    )

    with pytest.raises(PropAutoHydrationError) as exc_info:
        router.auto_hydrate_prop_evidence(
            sport="NFL",
            player="Test Colt",
            stat_type="PASSING_YARDS",
            event_start_time="2026-09-21T00:00:00+00:00",
            opponent="Seattle Seahawks",
            canonical_event_id="WOW:NFL:COLTS-CHIEFS:2026-09-20",
        )

    assert exc_info.value.code == "PROP_EVENT_IDENTITY_CONFLICT"


def test_request_context_restores_canonical_identity_for_core_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        router._nfl,
        "hydrate_nfl_prop_evidence",
        lambda **_kwargs: {
            **_raw_evidence(),
            "role_status": {
                "status": "ACTIVE_CURRENT_ESPN_ROSTER",
                "team": "IND",
                "opponent": "KC",
                "event_id": "espn-alias-1",
            },
        },
    )
    row = SimpleNamespace(
        sport="NFL",
        player="Context Player",
        event_start_time="2026-09-21T00:00:00+00:00",
        event_id="WOW:CANONICAL:COLTS-CHIEFS",
        opponent="KC",
    )

    with router.hydration_request_context([row]):
        result = router.auto_hydrate_prop_evidence(
            sport="NFL",
            player="Context Player",
            stat_type="RUSHING_YARDS",
            event_start_time=row.event_start_time,
        )

    assert result["role_status"]["canonical_event_id"] == row.event_id
    assert result["role_status"]["provider_event_ids"]["ESPN"] == "espn-alias-1"


def test_mlb_prefixed_canonical_id_must_match_official_game_pk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        router,
        "_hydrate_mlb",
        lambda **_kwargs: {
            **_raw_evidence(),
            "role_status": {
                "status": "OFFICIAL_PROBABLE_PITCHER",
                "team": "TEX",
                "opponent": "HOU",
                "official_game_pk": 123456,
            },
        },
    )

    with pytest.raises(PropAutoHydrationError) as exc_info:
        router.auto_hydrate_prop_evidence(
            sport="MLB",
            player="Test Pitcher",
            stat_type="PITCHER_STRIKEOUTS",
            event_start_time="2026-09-21T00:00:00+00:00",
            opponent="HOU",
            canonical_event_id="MLB:999999",
        )

    assert exc_info.value.code == "PROP_EVENT_IDENTITY_CONFLICT"
    assert exc_info.value.detail["provider_event_id"] == "123456"


def test_unsupported_cfb_route_never_falls_into_mlb_hydrator(monkeypatch: pytest.MonkeyPatch) -> None:
    def should_not_call(**_kwargs):
        pytest.fail("unsupported CFB route reached MLB hydrator")

    monkeypatch.setattr(router, "_hydrate_mlb", should_not_call)
    assert router.provider_for_sport("NCAAF", "PASSING_YARDS") == router.UNREGISTERED_PROVIDER

    with pytest.raises(PropAutoHydrationError) as exc_info:
        router.auto_hydrate_prop_evidence(
            sport="NCAAF",
            player="College QB",
            stat_type="PASSING_YARDS",
            event_start_time="2026-09-21T00:00:00+00:00",
            canonical_event_id="WOW:NCAAF:GAME-1",
        )

    assert exc_info.value.code == "PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE"
    assert exc_info.value.detail["provider"] == router.UNREGISTERED_PROVIDER


def test_wnba_same_tip_time_is_disambiguated_by_player_team(monkeypatch: pytest.MonkeyPatch) -> None:
    event_start = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)

    def original_conflict(*_args, **_kwargs):
        raise router._wnba.WNBAPropHydrationError(
            "PROP_EVENT_IDENTITY_CONFLICT",
            "time-only resolution tied",
        )

    monkeypatch.setattr(router, "_WNBA_ORIGINAL_SCHEDULE", original_conflict)
    monkeypatch.setattr(
        router._wnba,
        "_request",
        lambda *_args, **_kwargs: {
            "leagueSchedule": {
                "gameDates": [
                    {
                        "games": [
                            {"gameId": "A", "gameDateTimeUTC": event_start.isoformat(), "gameStatus": 1},
                            {"gameId": "B", "gameDateTimeUTC": event_start.isoformat(), "gameStatus": 1},
                        ]
                    }
                ]
            }
        },
    )

    def resolve(game, _player, _season, *, http_get):
        del http_get
        if game["gameId"] == "A":
            raise router._wnba.WNBAPropHydrationError(
                "PROP_PLAYER_IDENTITY_UNRESOLVED",
                "player not on this event",
            )
        return {
            "opponent": {
                "teamCity": "Minnesota",
                "teamName": "Lynx",
                "teamTricode": "MIN",
            }
        }

    monkeypatch.setattr(router._wnba, "_resolve_player_and_team", resolve)
    token = router._WNBA_TARGET.set(
        {"player": "Target Player", "opponent": "Minnesota Lynx", "season": 2026}
    )
    try:
        game = router._wnba_schedule_adapter(event_start, http_get=lambda *_a, **_k: None)
    finally:
        router._WNBA_TARGET.reset(token)

    assert game["gameId"] == "B"


def test_interactive_prehydration_forwards_canonical_event_and_opponent(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def hydrate(**kwargs):
        captured.update(kwargs)
        return _raw_evidence(role_status={"status": "READY"})

    monkeypatch.setattr(interactive.pick_runtime, "auto_hydrate_prop_evidence", hydrate)
    row = interactive.PickRequestRow(
        event_id="WOW:NFL:EVENT-123",
        event_start_time=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        sport="NFL",
        player="Test Player",
        stat_type="PASSING_YARDS",
        line=250.5,
        direction="MORE",
        opponent="KC",
    )

    interactive._hydrate(row)

    assert captured["canonical_event_id"] == "WOW:NFL:EVENT-123"
    assert captured["opponent"] == "KC"


def test_frozen_evidence_cannot_rebind_to_different_canonical_event() -> None:
    event_start = datetime.now(timezone.utc) + timedelta(days=1)
    evidence = runtime.RawPropEvidence.model_validate(
        _raw_evidence(
            role_status={
                "status": "READY",
                "canonical_event_id": "WOW:NFL:WRONG-EVENT",
                "provider_event_ids": {"ESPN": "401111111"},
                "identity_binding_status": "PASS",
            }
        )
    )
    row = runtime.PickRequestRow(
        event_id="WOW:NFL:RIGHT-EVENT",
        event_start_time=event_start.isoformat(),
        sport="NFL",
        player="Test Player",
        stat_type="PASSING_YARDS",
        line=250.5,
        direction="MORE",
        evidence=evidence,
    )

    with pytest.raises(ValueError, match="PROP_EVENT_IDENTITY_CONFLICT"):
        runtime._validate_evidence(row, "PASSING_YARDS")


def test_legacy_acquisition_provider_label_is_normalized_by_sport() -> None:
    batch = runtime.PickRequestBatch(
        rows=[
            runtime.PickRequestRow(
                event_id="WOW:NFL:EVENT-123",
                event_start_time=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                sport="NFL",
                player="Test Player",
                stat_type="PASSING_YARDS",
                line=250.5,
                direction="MORE",
            )
        ]
    )
    response = {
        "response_mode": "FULL",
        "rows": [
            {
                "acquisition": {
                    "mode": "AUTO_HYDRATION",
                    "provider": "MLB_STATS_API_OFFICIAL_V1",
                    "status": "PASS",
                }
            }
        ],
    }

    runtime._normalize_hydration_provider_receipts(response, batch)

    assert response["rows"][0]["acquisition"]["provider"] == router.NFL_PROVIDER


def test_daily_mlb_candidates_carry_opponent_with_canonical_game_id() -> None:
    now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    payload = {
        "dates": [
            {
                "games": [
                    {
                        "gamePk": 777,
                        "gameDate": "2026-09-20T23:00:00Z",
                        "teams": {
                            "home": {
                                "probablePitcher": {"fullName": "Home Pitcher"},
                                "team": {"abbreviation": "TEX"},
                            },
                            "away": {
                                "probablePitcher": {"fullName": "Away Pitcher"},
                                "team": {"abbreviation": "HOU"},
                            },
                        },
                    }
                ]
            }
        ]
    }

    rows = daily._schedule_pitchers(
        payload,
        requested_date="2026-09-20",
        requested_timezone="UTC",
        now=now,
    )

    assert rows == [
        {
            "event_id": "MLB:777",
            "event_start_time": "2026-09-20T23:00:00+00:00",
            "player": "Home Pitcher",
            "opponent": "HOU",
        },
        {
            "event_id": "MLB:777",
            "event_start_time": "2026-09-20T23:00:00+00:00",
            "player": "Away Pitcher",
            "opponent": "TEX",
        },
    ]
