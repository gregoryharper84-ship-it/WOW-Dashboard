from __future__ import annotations

from datetime import datetime, timezone

import pytest

from v17 import nfl_prop_event_aware_player_identity as subject


def _event(team: str, opponent: str, event_id: str) -> dict:
    return {
        "event_id": event_id,
        "team": team,
        "opponent": opponent,
        "provider_season": 2026,
        "provider_week": 4,
        "provider_home_team": team,
        "provider_away_team": opponent,
        "canonical_home_team": team,
        "canonical_away_team": opponent,
        "verified_canonical_event_id": f"2026_04_{opponent}_{team}",
    }


def test_duplicate_exact_name_resolves_by_target_event_and_opponent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject, "_search_exact_nfl_candidates", lambda *_args, **_kwargs: [
        ("4241478", "DeVonta Smith"),
        ("4594449", "DeVonta Smith"),
    ])
    monkeypatch.setattr(subject.nfl, "_athlete_team", lambda athlete_id, **_kwargs: {
        "4241478": "PHI",
        "4594449": "CAR",
    }[athlete_id])
    monkeypatch.setattr(subject.nfl, "_target_event", lambda *, team, **_kwargs: {
        "PHI": _event("PHI", "DAL", "eagles"),
        "CAR": _event("CAR", "ATL", "panthers"),
    }[team])

    resolved = subject.select_espn_athlete_for_event(
        player="DeVonta Smith",
        event_start_time="2026-09-28T20:15:00+00:00",
        opponent="Dallas Cowboys",
        http_get=lambda *_a, **_k: None,
    )
    assert resolved == ("4241478", "DeVonta Smith")


def test_duplicate_exact_name_remains_typed_failure_when_target_event_is_still_ambiguous(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject, "_search_exact_nfl_candidates", lambda *_args, **_kwargs: [
        ("1", "Josh Allen"),
        ("2", "Josh Allen"),
    ])
    monkeypatch.setattr(subject.nfl, "_athlete_team", lambda athlete_id, **_kwargs: {"1": "BUF", "2": "ARI"}[athlete_id])
    monkeypatch.setattr(subject.nfl, "_target_event", lambda *, team, **_kwargs: {
        "BUF": _event("BUF", "LAC", "buf"),
        "ARI": _event("ARI", "SF", "ari"),
    }[team])

    with pytest.raises(subject.nfl.NFLPropHydrationError) as exc_info:
        subject.select_espn_athlete_for_event(
            player="Josh Allen",
            event_start_time="2026-09-28T20:15:00+00:00",
            opponent=None,
            http_get=lambda *_a, **_k: None,
        )
    assert exc_info.value.code == "PROP_PLAYER_IDENTITY_UNRESOLVED"
    assert exc_info.value.detail["search_match_n"] == 2
    assert exc_info.value.detail["match_n"] == 2


def test_provider_event_alias_can_select_unique_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject, "_search_exact_nfl_candidates", lambda *_args, **_kwargs: [
        ("1", "Justin Jefferson"),
        ("2", "Justin Jefferson"),
    ])
    monkeypatch.setattr(subject.nfl, "_athlete_team", lambda athlete_id, **_kwargs: {"1": "MIN", "2": "CLE"}[athlete_id])
    monkeypatch.setattr(subject.nfl, "_target_event", lambda *, team, **_kwargs: {
        "MIN": _event("MIN", "TB", "401-min"),
        "CLE": _event("CLE", "CAR", "401-cle"),
    }[team])

    resolved = subject.select_espn_athlete_for_event(
        player="Justin Jefferson",
        event_start_time="2026-09-28T20:15:00+00:00",
        opponent=None,
        provider_event_id="401-min",
        http_get=lambda *_a, **_k: None,
    )
    assert resolved == ("1", "Justin Jefferson")


def test_zero_event_survivors_stays_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject, "_search_exact_nfl_candidates", lambda *_args, **_kwargs: [
        ("1", "Duplicate Name"),
        ("2", "Duplicate Name"),
    ])
    monkeypatch.setattr(subject.nfl, "_athlete_team", lambda athlete_id, **_kwargs: {"1": "BUF", "2": "ARI"}[athlete_id])

    def no_event(**_kwargs):
        raise subject.nfl.NFLPropHydrationError("PROP_EVENT_IDENTITY_CONFLICT", "no target event")

    monkeypatch.setattr(subject.nfl, "_target_event", no_event)
    with pytest.raises(subject.nfl.NFLPropHydrationError) as exc_info:
        subject.select_espn_athlete_for_event(
            player="Duplicate Name",
            event_start_time="2026-09-28T20:15:00+00:00",
            opponent="DAL",
            http_get=lambda *_a, **_k: None,
        )
    assert exc_info.value.code == "PROP_PLAYER_IDENTITY_UNRESOLVED"
    assert exc_info.value.detail["match_n"] == 0
