from __future__ import annotations

from dataclasses import dataclass

from v17 import wnba_prop_evidence_control_plane as subject


@dataclass
class _Response:
    payload: dict
    status_code: int = 200
    content: bytes = b"{}"
    text: str = "{}"

    def json(self):
        return self.payload


def _scoreboard_payload(*, games=None):
    return {
        "scoreboard": {
            "games": games
            if games is not None
            else [
                {
                    "gameId": "1042600122",
                    "gameTimeUTC": "2026-09-30T00:00:00Z",
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
                }
            ]
        }
    }


def test_scoreboard_recovery_preserves_official_game_and_team_ids(monkeypatch):
    calls = []

    def fake_request(url, **kwargs):
        calls.append((url, kwargs))
        return _scoreboard_payload()

    monkeypatch.setattr(subject.acquisition.wnba, "_request", fake_request)
    schedule = subject._scoreboard_schedule_for_date("2026-09-29", http_get=lambda *a, **k: None)
    game = schedule["leagueSchedule"]["gameDates"][0]["games"][0]

    assert calls[0][0] == subject.STATS_SCOREBOARD_URL
    assert calls[0][1]["params"] == {"LeagueID": "10", "GameDate": "2026-09-29"}
    assert game["gameId"] == "1042600122"
    assert game["homeTeam"]["teamId"] == "1611661325"
    assert game["awayTeam"]["teamId"] == "1611661319"
    assert not game["gameId"].startswith("espn-")
    provenance = schedule["wowScheduleProvenance"]
    assert provenance["provider"] == subject.STATS_SCOREBOARD_PROVIDER
    assert provenance["market_features_used"] is False
    assert provenance["probability_authority"] is False
    assert provenance["can_execute"] is False


def test_scoreboard_recovery_accepts_valid_off_day(monkeypatch):
    monkeypatch.setattr(
        subject.acquisition.wnba,
        "_request",
        lambda *args, **kwargs: _scoreboard_payload(games=[]),
    )
    schedule = subject._scoreboard_schedule_for_date("2026-09-29", http_get=lambda *a, **k: None)
    assert schedule["leagueSchedule"]["gameDates"] == []
    assert schedule["wowScheduleProvenance"]["game_n"] == 0


def test_scoreboard_recovery_fails_closed_without_official_game_id(monkeypatch):
    bad = _scoreboard_payload()
    del bad["scoreboard"]["games"][0]["gameId"]
    monkeypatch.setattr(subject.acquisition.wnba, "_request", lambda *a, **k: bad)

    try:
        subject._scoreboard_schedule_for_date("2026-09-29", http_get=lambda *a, **k: None)
    except subject.acquisition.wnba.WNBAPropHydrationError as exc:
        assert exc.code == "WNBA_STATS_SCOREBOARD_V3_GAME_IDENTITY_INVALID"
    else:
        raise AssertionError("missing official WNBA gameId must fail closed")


def test_schedule_replay_uses_recovered_schedule_only_for_schedule_url():
    recovered = {"leagueSchedule": {"gameDates": []}}
    passthrough_calls = []

    def base_get(url, params=None, headers=None, **kwargs):
        passthrough_calls.append(url)
        return _Response({"ok": True})

    replay_get = subject._schedule_replay_get(base_get, recovered)
    schedule_response = replay_get(subject.acquisition.wnba.WNBA_SCHEDULE_URL)
    assert schedule_response.json() == recovered
    assert passthrough_calls == []

    other = replay_get("https://stats.wnba.com/stats/leaguegamelog")
    assert other.json() == {"ok": True}
    assert passthrough_calls == ["https://stats.wnba.com/stats/leaguegamelog"]


def test_scoreboard_provenance_never_masquerades_as_cdn():
    raw = {
        "captured_at": "2026-09-29T20:00:00+00:00",
        "source_timestamps": {"WNBA_CDN_SCHEDULE_CURRENT": "2026-09-29T20:00:00+00:00"},
        "role_status": {"official_game_id": "1042600122"},
    }
    updated = subject._apply_scoreboard_provenance(raw)
    assert updated["role_status"]["official_game_id"] == "1042600122"
    assert updated["role_status"]["schedule_source_provider"] == subject.STATS_SCOREBOARD_PROVIDER
    assert updated["schedule_source_provider"] == subject.STATS_SCOREBOARD_PROVIDER
    assert "WNBA_CDN_SCHEDULE_CURRENT" not in updated["source_timestamps"]
    assert updated["source_timestamps"][subject.STATS_SCOREBOARD_PROVIDER]
