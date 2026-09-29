from __future__ import annotations

from datetime import datetime, timezone

import pytest

from v17 import wnba_prop_evidence_control_plane as subject

NOW = datetime(2026, 9, 29, 20, 0, tzinfo=timezone.utc)


def _payload(*, game_date: str = "2026-09-29", games=None) -> dict:
    if games is None:
        games = [
            {
                "gameId": "1042600122",
                "gameTimeUTC": "2026-09-29T23:00:00Z",
                "gameStatus": 1,
                "gameStatusText": "7:00 pm ET",
                "homeTeam": {
                    "teamId": 1611661325,
                    "teamTricode": "IND",
                    "teamCity": "Indiana",
                    "teamName": "Fever",
                },
                "awayTeam": {
                    "teamId": 1611661319,
                    "teamTricode": "LVA",
                    "teamCity": "Las Vegas",
                    "teamName": "Aces",
                },
                "pbOdds": {
                    "team": "must-not-enter-governed-evidence",
                    "odds": -110,
                },
            }
        ]
    return {"scoreboard": {"gameDate": game_date, "leagueId": "10", "games": games}}


def test_livedata_recovery_whitelists_identity_and_drops_market_fields(monkeypatch: pytest.MonkeyPatch):
    calls = []

    def fake_request(url, **kwargs):
        calls.append((url, kwargs))
        return _payload()

    monkeypatch.setattr(subject.acquisition.wnba, "_request", fake_request)
    schedule = subject._livedata_schedule_for_date(
        "2026-09-29",
        http_get=lambda *_a, **_k: None,
    )

    assert calls[0][0] == subject.LIVEDATA_SCOREBOARD_URL
    game = schedule["leagueSchedule"]["gameDates"][0]["games"][0]
    assert game == {
        "gameId": "1042600122",
        "gameDateTimeUTC": "2026-09-29T23:00:00Z",
        "gameDateUTC": "2026-09-29T23:00:00Z",
        "gameStatus": 1,
        "gameStatusText": "7:00 pm ET",
        "homeTeam": {
            "teamId": "1611661325",
            "teamTricode": "IND",
            "teamCity": "Indiana",
            "teamName": "Fever",
        },
        "awayTeam": {
            "teamId": "1611661319",
            "teamTricode": "LVA",
            "teamCity": "Las Vegas",
            "teamName": "Aces",
        },
    }
    assert "pbOdds" not in repr(schedule)
    provenance = schedule["wowScheduleProvenance"]
    assert provenance["provider"] == subject.LIVEDATA_SCOREBOARD_PROVIDER
    assert provenance["market_features_used"] is False
    assert provenance["probability_authority"] is False
    assert provenance["can_execute"] is False


def test_livedata_recovery_requires_exact_requested_date(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        subject.acquisition.wnba,
        "_request",
        lambda *_a, **_k: _payload(game_date="2026-09-29"),
    )
    with pytest.raises(subject.acquisition.wnba.WNBAPropHydrationError) as exc_info:
        subject._livedata_schedule_for_date(
            "2026-09-30",
            http_get=lambda *_a, **_k: None,
        )
    assert exc_info.value.code == "WNBA_LIVEDATA_SCOREBOARD_DATE_MISMATCH"


def test_livedata_recovery_accepts_exact_date_off_day(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        subject.acquisition.wnba,
        "_request",
        lambda *_a, **_k: _payload(games=[]),
    )
    schedule = subject._livedata_schedule_for_date(
        "2026-09-29",
        http_get=lambda *_a, **_k: None,
    )
    assert schedule["leagueSchedule"]["gameDates"] == []
    assert schedule["wowScheduleProvenance"]["game_n"] == 0


def test_livedata_recovery_fails_closed_without_official_game_identity(monkeypatch: pytest.MonkeyPatch):
    bad = _payload()
    del bad["scoreboard"]["games"][0]["gameId"]
    monkeypatch.setattr(subject.acquisition.wnba, "_request", lambda *_a, **_k: bad)

    with pytest.raises(subject.acquisition.wnba.WNBAPropHydrationError) as exc_info:
        subject._livedata_schedule_for_date(
            "2026-09-29",
            http_get=lambda *_a, **_k: None,
        )
    assert exc_info.value.code == "WNBA_LIVEDATA_SCOREBOARD_GAME_IDENTITY_INVALID"


def test_live_recovery_runs_only_after_primary_and_stats_recovery_fail(monkeypatch: pytest.MonkeyPatch):
    primary_exc = subject.acquisition.wnba.WNBAPropHydrationError(
        "WNBA_OFFICIAL_SOURCE_UNAVAILABLE",
        "primary transports unavailable",
        detail={
            "primary_source": "WNBA_CDN_SCHEDULE_CURRENT",
            "fallback_source": "WNBA_OFFICIAL_SCHEDULE_WEB_SSR",
            "primary_errors": ["JSONDecodeError:invalid json"],
            "fallback_errors": [
                "WNBAPropHydrationError:WNBA_OFFICIAL_SCHEDULE_WEB_PARSE_EMPTY"
            ],
        },
    )
    monkeypatch.setattr(
        subject.acquisition,
        "_request_schedule",
        lambda **_kwargs: (_ for _ in ()).throw(primary_exc),
    )
    monkeypatch.setattr(
        subject,
        "_scoreboard_schedule_for_date",
        lambda *_a, **_k: (_ for _ in ()).throw(
            subject.acquisition.wnba.WNBAPropHydrationError(
                "WNBA_STATS_SCOREBOARD_V3_UNAVAILABLE",
                "stats unavailable",
            )
        ),
    )
    monkeypatch.setattr(subject, "_livedata_schedule_for_date", lambda *_a, **_k: {
        "leagueSchedule": {"gameDates": []},
        "wowScheduleProvenance": {
            "provider": subject.LIVEDATA_SCOREBOARD_PROVIDER,
            "market_features_used": False,
            "probability_authority": False,
            "can_execute": False,
        },
    })
    monkeypatch.setattr(subject.acquisition, "_schedule_players", lambda *_a, **_k: [])

    result = subject.acquire_wnba_forward_evidence_batch(
        subject.WNBAForwardEvidenceRequest(requested_date="2026-09-29", max_candidates=1),
        db=object(),
        now=NOW,
        http_get=lambda *_a, **_k: object(),
    )

    assert result["status"] == "COMPLETED"
    assert result["schedule_source_provider"] == subject.LIVEDATA_SCOREBOARD_PROVIDER
    assert any(
        item.get("code") == "WNBA_STATS_SCOREBOARD_V3_UNAVAILABLE"
        for item in result["source_diagnostics"]
    )
    live_receipt = next(
        item
        for item in result["source_diagnostics"]
        if item.get("code") == "WNBA_LIVEDATA_SCOREBOARD_RECOVERY_USED"
    )
    assert live_receipt["market_features_used"] is False
    assert result["probability_publishable"] is False
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["can_execute"] is False


def test_total_failure_keeps_existing_blockers_and_adds_live_source_diagnostic(monkeypatch: pytest.MonkeyPatch):
    primary_exc = subject.acquisition.wnba.WNBAPropHydrationError(
        "WNBA_OFFICIAL_SOURCE_UNAVAILABLE",
        "primary transports unavailable",
        detail={
            "url": "https://stats.wnba.com/stats/leaguegamelog?secret=do-not-leak",
            "attempts": 2,
            "errors": ["RuntimeError:HTTP_403"],
        },
    )
    monkeypatch.setattr(
        subject.acquisition,
        "_request_schedule",
        lambda **_kwargs: (_ for _ in ()).throw(primary_exc),
    )
    monkeypatch.setattr(
        subject,
        "_scoreboard_schedule_for_date",
        lambda *_a, **_k: (_ for _ in ()).throw(
            subject.acquisition.wnba.WNBAPropHydrationError(
                "WNBA_STATS_SCOREBOARD_V3_UNAVAILABLE",
                "stats unavailable",
            )
        ),
    )
    monkeypatch.setattr(
        subject,
        "_livedata_schedule_for_date",
        lambda *_a, **_k: (_ for _ in ()).throw(
            subject.acquisition.wnba.WNBAPropHydrationError(
                "WNBA_LIVEDATA_SCOREBOARD_UNAVAILABLE",
                "liveData unavailable",
            )
        ),
    )

    result = subject.acquire_wnba_forward_evidence_batch(
        subject.WNBAForwardEvidenceRequest(requested_date="2026-09-29", max_candidates=1),
        db=object(),
        now=NOW,
        http_get=lambda *_a, **_k: object(),
    )

    assert result["status"] == "DATA_UNOBTAINABLE"
    assert result["blockers"] == [
        "WNBA_OFFICIAL_SOURCE_UNAVAILABLE",
        "WNBA_STATS_SCOREBOARD_V3_UNAVAILABLE",
    ]
    assert result["source_diagnostics"][-1] == {
        "code": "WNBA_LIVEDATA_SCOREBOARD_UNAVAILABLE",
        "provider": subject.LIVEDATA_SCOREBOARD_PROVIDER,
    }
    assert "do-not-leak" not in str(result)
    assert result["can_execute"] is False
