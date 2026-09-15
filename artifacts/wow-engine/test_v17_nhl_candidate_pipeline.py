from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import nhl_candidate_pipeline as nhl


BASE = datetime(2021, 10, 1, 23, 0, tzinfo=timezone.utc)


def raw_game(i: int, *, home="BOS", away="NYR", home_score=3, away_score=2, state="FINAL", game_type=2):
    return {
        "id": 2021020000 + i,
        "season": 20212022,
        "gameType": game_type,
        "gameState": state,
        "neutralSite": False,
        "startTimeUTC": (BASE + timedelta(days=i)).isoformat().replace("+00:00", "Z"),
        "awayTeam": {"id": 3, "abbrev": away, "score": away_score},
        "homeTeam": {"id": 6, "abbrev": home, "score": home_score},
    }


def game(i: int, home: str, away: str, *, season=20212022, home_win=True) -> nhl.NHLGame:
    start = BASE + timedelta(days=i // 8, hours=(i % 8) * 2)
    hs, aws = ((4, 2) if home_win else (2, 4))
    raw = {
        "id": int(f"{season}{i:04d}"[-10:]),
        "season": season,
        "gameType": 2,
        "gameState": "FINAL",
        "neutralSite": False,
        "startTimeUTC": start.isoformat().replace("+00:00", "Z"),
        "awayTeam": {"id": 2, "abbrev": away, "score": aws},
        "homeTeam": {"id": 1, "abbrev": home, "score": hs},
    }
    out = nhl.normalize_game(raw, source_uri="https://api-web.nhle.com/test", retrieved_at="2026-09-15T00:00:00+00:00")
    assert out is not None
    return out


def synthetic_seasons() -> list[nhl.NHLGame]:
    teams = ["BOS", "NYR", "TOR", "MTL", "TBL", "FLA", "CAR", "NJD"]
    games: list[nhl.NHLGame] = []
    idx = 0
    for season_offset, season in enumerate((20212022, 20222023, 20232024, 20242025, 20252026)):
        # 15 round-robin cycles produce 420 games/season across eight clubs.
        for cycle in range(15):
            for h in range(len(teams)):
                for a in range(h + 1, len(teams)):
                    home = teams[h] if (cycle + h + a) % 2 == 0 else teams[a]
                    away = teams[a] if home == teams[h] else teams[h]
                    # Strength ordering creates learnable but imperfect signal.
                    home_rank = teams.index(home)
                    away_rank = teams.index(away)
                    home_win = home_rank <= away_rank
                    if idx % 13 == 0:
                        home_win = not home_win
                    start = BASE.replace(year=2021 + season_offset) + timedelta(days=idx % 170, hours=(idx % 4) * 2)
                    raw = {
                        "id": 10_000_000 + idx,
                        "season": season,
                        "gameType": 2,
                        "gameState": "FINAL",
                        "neutralSite": False,
                        "startTimeUTC": start.isoformat().replace("+00:00", "Z"),
                        "awayTeam": {"id": 2, "abbrev": away, "score": 2 if home_win else 4},
                        "homeTeam": {"id": 1, "abbrev": home, "score": 4 if home_win else 2},
                    }
                    parsed = nhl.normalize_game(raw, source_uri="https://api-web.nhle.com/test", retrieved_at="2026-09-15T00:00:00+00:00")
                    assert parsed is not None
                    games.append(parsed)
                    idx += 1
    return sorted(games, key=lambda row: (row.event_start_time, row.game_id))


def test_normalize_accepts_only_settled_regular_season_games():
    parsed = nhl.normalize_game(raw_game(1), source_uri="u", retrieved_at="2026-09-15T00:00:00+00:00")
    assert parsed is not None
    assert parsed.home_team == "BOS"
    assert parsed.away_team == "NYR"
    assert parsed.historical_reconstruction is True
    assert parsed.can_execute is False
    assert len(parsed.source_payload_sha256) == 64

    assert nhl.normalize_game(raw_game(2, state="FUT"), source_uri="u", retrieved_at="x") is None
    assert nhl.normalize_game(raw_game(3, game_type=3), source_uri="u", retrieved_at="x") is None


def test_team_season_fetch_fails_closed_on_schema_drift(monkeypatch):
    class Response:
        status_code = 200
        def json(self):
            return {"not_games": []}

    class Session:
        @staticmethod
        def get(*args, **kwargs):
            return Response()

    with pytest.raises(nhl.NHLCandidateError) as caught:
        nhl.fetch_team_season_games("BOS", 2025, session=Session())
    assert caught.value.code == "NHL_SOURCE_SCHEMA_DRIFT"


def test_duplicate_official_game_must_reconcile_exactly(monkeypatch):
    first = game(1, "BOS", "NYR")
    conflicting = nhl.NHLGame(**{**first.__dict__, "home_score": first.home_score + 1})
    calls = {"BOS": [first], "NYR": [conflicting]}
    monkeypatch.setattr(nhl, "fetch_team_season_games", lambda team, year, session=None: calls[team])
    with pytest.raises(nhl.NHLCandidateError) as caught:
        nhl.fetch_historical_games([2021], teams=("BOS", "NYR"), session=object())
    assert caught.value.code == "NHL_DUPLICATE_GAME_CONFLICT"


def test_reconstruction_uses_prior_settled_games_only_and_no_market_features():
    games = synthetic_seasons()
    rows, records = nhl.reconstruct_training_rows(games)
    assert len(rows) > 300
    assert len(rows) == len(records)
    assert tuple(rows[0].features) == nhl.FEATURE_NAMES
    assert all(row.feature_as_of < row.event_start_time for row in rows)
    assert all(len(row.source_manifest_sha256) == 64 for row in rows)
    assert all(record["historical_reconstruction"] is True for record in records)
    assert all(record["archived_pregame_snapshot"] is False for record in records)
    assert all(record["market_features_used"] is False for record in records)
    assert all(record["source_manifest"]["reconstruction_basis"] == "PRIOR_SETTLED_GAMES_ONLY" for record in records)
    assert all(record["source_manifest"]["archive_pregame_snapshot_claimed"] is False for record in records)


def test_current_game_outcome_does_not_change_its_own_features():
    games = synthetic_seasons()
    rows1, _ = nhl.reconstruct_training_rows(games)
    target_game = next(game for game in games if game.game_id == rows1[50].event_id)
    changed = [
        nhl.NHLGame(**{**g.__dict__, "home_score": g.away_score, "away_score": g.home_score}) if g.game_id == target_game.game_id else g
        for g in games
    ]
    rows2, _ = nhl.reconstruct_training_rows(changed)
    by1 = {row.event_id: row for row in rows1}
    by2 = {row.event_id: row for row in rows2}
    assert by1[target_game.game_id].features == by2[target_game.game_id].features
    assert by1[target_game.game_id].source_manifest_sha256 == by2[target_game.game_id].source_manifest_sha256
    assert by1[target_game.game_id].positive_outcome != by2[target_game.game_id].positive_outcome


def test_nhl_fit_is_candidate_only_and_requires_later_source_review():
    candidate, records = nhl.fit_nhl_candidate(synthetic_seasons())
    assert candidate.model_family == nhl.MODEL_FAMILY
    assert candidate.probability_publishable is False
    assert candidate.automatic_certification is False
    assert candidate.automatic_promotion is False
    assert candidate.can_execute is False
    assert records
    artifact = nhl.candidate_record(candidate, training_code_sha="a" * 40)
    assert artifact["lifecycle_state"] == "CANDIDATE"
    assert artifact["source_review_status"] == "REQUIRED"
    assert artifact["historical_reconstruction"] is True
    assert artifact["active"] is False
    assert artifact["promoted"] is False
    assert artifact["probability_publishable"] is False
    assert artifact["can_execute"] is False


def test_training_code_sha_is_required_for_candidate_registry_record():
    candidate, _ = nhl.fit_nhl_candidate(synthetic_seasons())
    with pytest.raises(nhl.NHLCandidateError) as caught:
        nhl.candidate_record(candidate, training_code_sha="")
    assert caught.value.code == "NHL_TRAINING_CODE_SHA_REQUIRED"
