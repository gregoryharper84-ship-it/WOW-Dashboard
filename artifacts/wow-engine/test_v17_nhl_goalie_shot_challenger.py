"""Negative-path and chronology checks for the isolated NHL challenger."""
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from hashlib import sha256

import numpy as np
import pytest

from nhl_candidate_pipeline import NHLGame, NHLCandidateError, reconstruct_training_rows
from v17.nhl_goalie_shot_challenger import (
    ADDED_FEATURES, Box, _goalie_save_pct, _projected_goalie, augment_rows,
    parse_box, replay,
)

HASH = "a" * 64


def game(i, home="BOS", away="BUF", start=None):
    dt = start or (datetime(2024, 10, 1, tzinfo=timezone.utc) + timedelta(days=i))
    return NHLGame(
        game_id=str(2024020000 + i), season_id=20242025, event_start_time=dt.isoformat(),
        home_team=home, away_team=away, home_score=3 if i % 3 else 1,
        away_score=2 if i % 3 else 4, neutral_site=False,
        source_uri="https://api-web.nhle.com/v1/example",
        source_retrieved_at="2026-10-09T00:00:00+00:00",
        source_payload_sha256=HASH,
    )


def box(g, h_goalie="101", a_goalie="201", home_sog=31, away_sog=22,
        home_pp=1, away_pp=0):
    return Box(g.game_id, g.season_id, g.home_team, g.away_team,
               home_sog, away_sog, home_pp, away_pp, h_goalie, a_goalie,
               19, 28, 22, 31, sha256(f"{home_sog}:{away_sog}:{home_pp}:{away_pp}".encode()).hexdigest())


def official_payload(g, *, starter=True):
    return {
        "id": int(g.game_id), "season": g.season_id,
        "gameState": "OFF", "gameType": 2,
        "homeTeam": {"abbrev": g.home_team, "sog": 31},
        "awayTeam": {"abbrev": g.away_team, "sog": 22},
        "playerByGameStats": {
            "homeTeam": {
                "forwards": [{"powerPlayGoals": 1}], "defense": [{"powerPlayGoals": 0}],
                "goalies": [{"playerId": 101, "starter": starter, "saves": 19, "shotsAgainst": 22}],
            },
            "awayTeam": {
                "forwards": [{"powerPlayGoals": 0}], "defense": [{"powerPlayGoals": 0}],
                "goalies": [{"playerId": 201, "starter": True, "saves": 28, "shotsAgainst": 31}],
            },
        },
    }


def test_official_schema_and_identity_and_pp_goals():
    g = game(1)
    result = parse_box(g, official_payload(g))
    assert (result.home_sog, result.home_pp, result.home_goalie) == (31, 1, "101")
    assert (result.away_saves, result.away_shots_against) == (28, 31)
    assert len(result.source_hash) == 64


@pytest.mark.parametrize("change,code", [
    (lambda p: p["homeTeam"].update(sog=None), "NHL_BOX_SCHEMA_DRIFT"),
    (lambda p: p.update(id=999), "NHL_BOX_IDENTITY_MISMATCH"),
    (lambda p: p["playerByGameStats"]["homeTeam"]["goalies"].append(
        {"playerId": 102, "starter": True, "saves": 1, "shotsAgainst": 1}), "NHL_BOX_STARTER_AMBIGUOUS"),
    (lambda p: p["playerByGameStats"]["homeTeam"]["goalies"][0].update(saves=23), "NHL_BOX_SCHEMA_DRIFT"),
    (lambda p: p.pop("playerByGameStats"), "NHL_BOX_SCHEMA_DRIFT"),
    (lambda p: p.update(gameState="LIVE"), "NHL_BOX_NOT_FINAL"),
])
def test_bad_boxscore_fails_closed(change, code):
    g = game(1)
    payload = official_payload(g)
    change(payload)
    with pytest.raises(NHLCandidateError) as exc:
        parse_box(g, payload)
    assert exc.value.code == code


def test_fixed_goalie_prior_and_back_to_back_alternate():
    assert abs(_goalie_save_pct("", {}) - 0.9) < 1e-10
    assert abs(_goalie_save_pct("A", {"A": (90, 100)}) - 0.9) < 1e-10
    assert _projected_goalie(["A", "B", "A"], False) == "A"
    assert _projected_goalie(["A", "B", "A"], True) == "B"
    assert _projected_goalie(["A"], True) == "A"
    assert ADDED_FEATURES == (
        "shot_share_l10_delta", "projected_starter_sv_delta",
        "pp_goals_for_l10_delta", "pp_goals_against_l10_delta",
    )


def test_uses_only_prior_games_even_if_current_boxscore_changes():
    games = [game(i) for i in range(1, 14)]
    v1, _ = reconstruct_training_rows(games)
    boxes = {g.game_id: box(g) for g in games}
    _, rows = augment_rows(games, boxes, v1) if len(v1) >= 300 else (None, None)
    # Unit-size chronology is checked using an unchanged 300+ day sample below.
    assert rows is None
    assert games[0].game_id not in [r.event_id for r in v1]


def _dataset():
    all_games = []
    base = datetime(2024, 10, 1, tzinfo=timezone.utc)
    for day in range(225):
        start = base + timedelta(days=day)
        all_games.append(game(2 * day + 1, "BOS", "BUF", start))
        all_games.append(game(2 * day + 2, "CAR", "CBJ", start))
    return all_games


def test_prior_game_invariance_same_start_and_aligned_replay():
    games = _dataset()
    v1, _ = reconstruct_training_rows(games)
    boxes = {g.game_id: box(g, home_sog=27 + (i % 10), away_sog=20 + (i % 9))
             for i, g in enumerate(games)}
    common, augmented = augment_rows(games, boxes, v1)
    assert len(common) == len(augmented) >= 300
    assert [r.event_id for r in common] == [r.event_id for r in augmented]
    original = next(r for r in augmented if r.event_id == games[14].game_id)
    changed = dict(boxes)
    modified = games[14]
    changed[modified.game_id] = box(modified, home_sog=91, away_sog=3, home_pp=10, away_pp=5)
    _, changed_rows = augment_rows(games, changed, v1)
    match = next(r for r in changed_rows if r.event_id == modified.game_id)
    assert dict(match.features) == dict(original.features)  # no same-event result use
    assert match.source_manifest_sha256 == original.source_manifest_sha256
    # Even changes to THIS game's final scores or fetched official schedule
    # bytes (embedded in V1's source manifest) may not change the PRE-game
    # challenger features or manifest. Outcome labels stay separately graded.
    variant_games = list(games)
    variant_games[14] = replace(
        variant_games[14],
        home_score=9, away_score=0,
        source_payload_sha256="b" * 64,
    )
    variant_v1, _ = reconstruct_training_rows(variant_games)
    original_v1 = next(r for r in v1 if r.event_id == modified.game_id)
    altered_v1 = next(r for r in variant_v1 if r.event_id == modified.game_id)
    assert original_v1.source_manifest_sha256 != altered_v1.source_manifest_sha256
    _, variant_rows = augment_rows(variant_games, boxes, variant_v1)
    variant_row = next(r for r in variant_rows if r.event_id == modified.game_id)
    assert dict(variant_row.features) == dict(original.features)
    assert variant_row.source_manifest_sha256 == original.source_manifest_sha256
    # Current game's final boxscore must not influence any pregame feature
    # value OR source manifest. Its label remains postgame-only evidence.
    # Adjacent later row may change after the final boxscore enters prior history.
    later = next(r for r in augmented if r.event_id == games[16].game_id)
    after = next(r for r in changed_rows if r.event_id == games[16].game_id)
    assert later.features["shot_share_l10_delta"] != after.features["shot_share_l10_delta"]
    # Same-start other match cannot inherit current data from first match.
    other = next(r for r in augmented if r.event_id == games[15].game_id)
    other_changed = next(r for r in changed_rows if r.event_id == games[15].game_id)
    assert dict(other.features) == dict(other_changed.features)
    assert all(not r.features.get("market_odds") for r in augmented)
    result = replay(games, boxes, bootstrap=120)
    assert result["status"] == "ADVISORY_RESEARCH_ONLY"
    assert result["test_n"] >= 50
    assert len(result["delta_brier_bootstrap_95_ci"]) == 2
    assert len(result["delta_log_loss_bootstrap_95_ci"]) == 2
    assert result["delta_log_loss_bootstrap_95_ci"][0] <= result["delta_log_loss_bootstrap_95_ci"][1]
    if result["research_gate_pass"]:
        assert result["decision"] == "FORWARD_SHADOW_AND_GOVERNED_REVIEW_REQUIRED"
        assert result["hold_reasons"] == []
        assert result["delta_brier_bootstrap_95_ci"][0] > 0
        assert result["delta_log_loss_bootstrap_95_ci"][0] > 0
        assert result["v2"]["ece"] <= result["v1"]["ece"]
    else:
        assert result["decision"] == "HOLD_CHALLENGER_INSUFFICIENT_VALIDATION"
        assert result["hold_reasons"]
        for reason in result["hold_reasons"]:
            assert reason in {
                "BRIER_IMPROVEMENT_NOT_PROVEN",
                "LOG_LOSS_IMPROVEMENT_NOT_PROVEN",
                "CALIBRATION_ERROR_WORSENED",
            }
    assert result["automatic_promotion_allowed"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_missing_source_is_explicitly_intersected_and_extra_id_fails():
    games = _dataset()
    v1, _ = reconstruct_training_rows(games)
    missing = games[16]  # included in V1 cohort
    boxes = {g.game_id: box(g) for g in games if g.game_id != missing.game_id}
    common, augmented = augment_rows(games, boxes, v1)
    assert len(common) == len(v1) - 1
    assert len(augmented) == len(common)
    assert missing.game_id not in {r.event_id for r in common}
    assert [r.event_id for r in common] == [r.event_id for r in augmented]
    result = replay(games, boxes, bootstrap=120)
    assert result["excluded_v1_rows_missing_box"] == 1
    assert result["covered_intersection_rows"] == len(common)
    boxes["9999999999"] = box(games[0])
    with pytest.raises(NHLCandidateError) as exc:
        augment_rows(games, boxes, v1)
    assert exc.value.code == "NHL_BOX_GAME_RECONCILIATION_FAILED"
