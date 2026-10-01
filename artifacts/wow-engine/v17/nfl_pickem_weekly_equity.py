"""Downstream weekly-win-equity optimizer for governed NFL Pick'em boards.

This module never creates, calibrates, or modifies sporting probabilities. It consumes
an already-governed NFL Pick'em board and a separately sourced opponent pick-share
snapshot, then searches for a card that improves weekly first-place equity.

Opponent pick shares describe pool behavior only. They are forbidden from becoming
sporting probability, calibration, lower-bound, market, or model-ownership inputs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from math import isclose
from typing import Any, Iterable, Mapping

import numpy as np

WEEKLY_OBJECTIVE = "MAX_WEEKLY_WIN_EQUITY"
OPTIMIZER_VERSION = "V17_NFL_PICKEM_WEEKLY_EQUITY_V1"
EQUITY_METHOD = "IID_PICK_SHARE_EQUAL_TIE_PROXY_V1"
CAN_EXECUTE = False

WEEKLY_EQUITY_READY = "WEEKLY_EQUITY_READY"
WEEKLY_EQUITY_BLOCKED = "WEEKLY_EQUITY_BLOCKED"

_EPS = 1e-9
_MAX_GAMES = 16
_MAX_POOL_ENTRIES = 100_000
_MAX_SEARCH_FLIPS = 6
_DEFAULT_SEARCH_FLIPS = 3


@dataclass(frozen=True)
class _Game:
    event_id: str
    home_team: str
    away_team: str
    home_probability: float
    away_probability: float
    baseline_pick: str


@dataclass(frozen=True)
class _PickShare:
    event_id: str
    home_pick_share: float
    away_pick_share: float
    source: str
    snapshot_id: str
    observed_at: str
    freshness_status: str


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if np.isfinite(parsed) else None


def _text(value: Any) -> str | None:
    token = str(value or "").strip()
    return token or None


def _blocked(*blockers: str, pool_entries: int | None = None) -> dict[str, Any]:
    return {
        "optimizer_version": OPTIMIZER_VERSION,
        "decision_objective": WEEKLY_OBJECTIVE,
        "status": WEEKLY_EQUITY_BLOCKED,
        "pool_entries": pool_entries,
        "blockers": sorted({str(value) for value in blockers if str(value).strip()}),
        "sporting_probability_modified": False,
        "market_probability_used": False,
        "sportsbook_price_used": False,
        "pool_popularity_used_as_sporting_probability": False,
        "can_execute": False,
    }


def _parse_games(board: Mapping[str, Any]) -> tuple[list[_Game], list[str]]:
    blockers: list[str] = []
    if board.get("submission_ready") is not True:
        blockers.append("PICKEM_BASELINE_BOARD_NOT_READY")
    picks = board.get("picks")
    if not isinstance(picks, list) or not picks:
        blockers.append("PICKEM_BASELINE_PICKS_REQUIRED")
        return [], blockers

    games: list[_Game] = []
    seen: set[str] = set()
    for raw in picks:
        if not isinstance(raw, Mapping):
            blockers.append("PICKEM_BASELINE_PICK_INVALID")
            continue
        event_id = _text(raw.get("official_event_id"))
        home = _text(raw.get("home_team"))
        away = _text(raw.get("away_team"))
        baseline = _text(raw.get("pool_pick"))
        home_p = _float(raw.get("home_probability"))
        away_p = _float(raw.get("away_probability"))
        if not event_id or not home or not away or baseline not in {home, away}:
            blockers.append("PICKEM_BASELINE_IDENTITY_INVALID")
            continue
        if event_id in seen:
            blockers.append("PICKEM_BASELINE_DUPLICATE_EVENT")
            continue
        seen.add(event_id)
        if home_p is None or away_p is None:
            blockers.append("PICKEM_BASELINE_TWO_SIDED_PROBABILITY_REQUIRED")
            continue
        if not (0.0 <= home_p <= 1.0 and 0.0 <= away_p <= 1.0):
            blockers.append("PICKEM_BASELINE_PROBABILITY_OUT_OF_RANGE")
            continue
        if not isclose(home_p + away_p, 1.0, abs_tol=_EPS):
            blockers.append("PICKEM_BASELINE_OUTCOME_SPACE_NOT_NORMALIZED")
            continue
        max_side = home if home_p >= away_p else away
        if baseline != max_side:
            blockers.append("PICKEM_BASELINE_NOT_MAX_EXPECTED_CORRECT")
            continue
        games.append(
            _Game(
                event_id=event_id,
                home_team=home,
                away_team=away,
                home_probability=home_p,
                away_probability=away_p,
                baseline_pick=baseline,
            )
        )
    if len(games) > _MAX_GAMES:
        blockers.append("PICKEM_WEEKLY_EQUITY_GAME_COUNT_EXCEEDS_BOUND")
    return games, blockers


def _parse_pick_shares(
    raw_shares: Iterable[Mapping[str, Any]], games: list[_Game]
) -> tuple[list[_PickShare], list[str]]:
    blockers: list[str] = []
    by_id: dict[str, _PickShare] = {}
    for raw in raw_shares:
        if not isinstance(raw, Mapping):
            blockers.append("PICKEM_OPPONENT_PICK_SHARE_INVALID")
            continue
        event_id = _text(raw.get("official_event_id"))
        home_share = _float(raw.get("home_pick_share"))
        away_share = _float(raw.get("away_pick_share"))
        source = _text(raw.get("source"))
        snapshot_id = _text(raw.get("snapshot_id"))
        observed_at = _text(raw.get("observed_at"))
        freshness_status = str(raw.get("freshness_status") or "").strip().upper()
        if not event_id:
            blockers.append("PICKEM_OPPONENT_PICK_SHARE_EVENT_ID_REQUIRED")
            continue
        if event_id in by_id:
            blockers.append("PICKEM_OPPONENT_PICK_SHARE_DUPLICATE_EVENT")
            continue
        if home_share is None or away_share is None:
            blockers.append("PICKEM_OPPONENT_PICK_SHARE_TWO_SIDED_REQUIRED")
            continue
        if not (0.0 <= home_share <= 1.0 and 0.0 <= away_share <= 1.0):
            blockers.append("PICKEM_OPPONENT_PICK_SHARE_OUT_OF_RANGE")
            continue
        if not isclose(home_share + away_share, 1.0, abs_tol=_EPS):
            blockers.append("PICKEM_OPPONENT_PICK_SHARE_NOT_NORMALIZED")
            continue
        if not source or not snapshot_id or not observed_at:
            blockers.append("PICKEM_OPPONENT_PICK_SHARE_PROVENANCE_REQUIRED")
            continue
        try:
            parsed_observed_at = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
            if parsed_observed_at.tzinfo is None:
                raise ValueError("timezone required")
        except ValueError:
            blockers.append("PICKEM_OPPONENT_PICK_SHARE_OBSERVED_AT_INVALID")
            continue
        if freshness_status != "CURRENT":
            blockers.append("PICKEM_OPPONENT_PICK_SHARE_NOT_CURRENT")
            continue
        by_id[event_id] = _PickShare(
            event_id=event_id,
            home_pick_share=home_share,
            away_pick_share=away_share,
            source=source,
            snapshot_id=snapshot_id,
            observed_at=observed_at,
            freshness_status=freshness_status,
        )

    game_ids = {game.event_id for game in games}
    if set(by_id) != game_ids:
        missing = sorted(game_ids - set(by_id))
        extra = sorted(set(by_id) - game_ids)
        if missing:
            blockers.append("PICKEM_OPPONENT_PICK_SHARE_MISSING_EVENT")
        if extra:
            blockers.append("PICKEM_OPPONENT_PICK_SHARE_UNKNOWN_EVENT")
    return [by_id[game.event_id] for game in games if game.event_id in by_id], blockers


def _expected_tie_share(p_less: np.ndarray, p_equal: np.ndarray, opponents: int) -> np.ndarray:
    """Expected first-place prize share when ties split equally."""
    p_less = np.asarray(p_less, dtype=float)
    p_equal = np.asarray(p_equal, dtype=float)
    if opponents == 0:
        return np.ones_like(p_less)
    out = np.empty_like(p_less)
    nonzero = p_equal > 1e-15
    out[~nonzero] = np.power(p_less[~nonzero], opponents)
    if np.any(nonzero):
        a = p_less[nonzero]
        e = p_equal[nonzero]
        out[nonzero] = (
            np.power(a + e, opponents + 1) - np.power(a, opponents + 1)
        ) / ((opponents + 1) * e)
    return np.clip(out, 0.0, 1.0)


def _outcome_state(
    games: list[_Game], shares: list[_PickShare], pool_entries: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    game_count = len(games)
    outcome_count = 1 << game_count
    masks = np.arange(outcome_count, dtype=np.uint32)
    shifts = np.arange(game_count, dtype=np.uint32)
    home_wins = ((masks[:, None] >> shifts[None, :]) & 1).astype(bool)

    home_prob = np.array([game.home_probability for game in games], dtype=float)
    outcome_prob = np.prod(np.where(home_wins, home_prob, 1.0 - home_prob), axis=1)
    outcome_prob /= outcome_prob.sum()

    home_share = np.array([share.home_pick_share for share in shares], dtype=float)
    opponent_correct = np.where(home_wins, home_share, 1.0 - home_share)

    score_dist = np.zeros((outcome_count, game_count + 1), dtype=float)
    score_dist[:, 0] = 1.0
    for idx in range(game_count):
        q = opponent_correct[:, idx]
        prev = score_dist[:, : idx + 1].copy()
        score_dist[:, : idx + 1] *= (1.0 - q)[:, None]
        score_dist[:, 1 : idx + 2] += prev * q[:, None]

    cumulative = np.cumsum(score_dist, axis=1)
    opponents = pool_entries - 1
    tie_equity = np.zeros_like(score_dist)
    sole_win = np.zeros_like(score_dist)
    for score in range(game_count + 1):
        p_less = cumulative[:, score - 1] if score > 0 else np.zeros(outcome_count)
        p_equal = score_dist[:, score]
        tie_equity[:, score] = _expected_tie_share(p_less, p_equal, opponents)
        sole_win[:, score] = np.power(p_less, opponents) if opponents else 1.0

    popcount = np.fromiter(
        (int(value).bit_count() for value in range(outcome_count)),
        dtype=np.uint8,
        count=outcome_count,
    )
    return masks, outcome_prob, tie_equity, sole_win, popcount


def _mask_for_picks(games: list[_Game], picks: Mapping[str, str]) -> int:
    mask = 0
    for idx, game in enumerate(games):
        selected = picks[game.event_id]
        if selected == game.home_team:
            mask |= 1 << idx
    return mask


def _card_from_mask(games: list[_Game], mask: int) -> dict[str, str]:
    return {
        game.event_id: (game.home_team if mask & (1 << idx) else game.away_team)
        for idx, game in enumerate(games)
    }


def _card_expected_correct(games: list[_Game], mask: int) -> float:
    return float(
        sum(
            game.home_probability if mask & (1 << idx) else game.away_probability
            for idx, game in enumerate(games)
        )
    )


def _evaluate_mask(
    mask: int,
    *,
    game_count: int,
    masks: np.ndarray,
    outcome_prob: np.ndarray,
    tie_equity: np.ndarray,
    sole_win: np.ndarray,
    popcount: np.ndarray,
) -> tuple[float, float]:
    scores = game_count - popcount[np.bitwise_xor(masks, np.uint32(mask))]
    indices = np.arange(masks.size)
    tie_value = float(np.sum(outcome_prob * tie_equity[indices, scores]))
    sole_value = float(np.sum(outcome_prob * sole_win[indices, scores]))
    return tie_value, sole_value


def _candidate_masks(baseline_mask: int, game_count: int, max_flips: int) -> Iterable[int]:
    yield baseline_mask
    indexes = tuple(range(game_count))
    for flip_count in range(1, max_flips + 1):
        for combo in combinations(indexes, flip_count):
            candidate = baseline_mask
            for idx in combo:
                candidate ^= 1 << idx
            yield candidate


def optimize_weekly_win_equity(
    board: Mapping[str, Any],
    *,
    pool_entries: int,
    opponent_pick_shares: Iterable[Mapping[str, Any]],
    max_candidate_flips: int = _DEFAULT_SEARCH_FLIPS,
) -> dict[str, Any]:
    """Optimize a governed NFL Pick'em card for weekly first-place equity."""
    if isinstance(pool_entries, bool) or not isinstance(pool_entries, int):
        return _blocked("PICKEM_POOL_ENTRIES_INVALID", pool_entries=None)
    if not 2 <= pool_entries <= _MAX_POOL_ENTRIES:
        return _blocked("PICKEM_POOL_ENTRIES_INVALID", pool_entries=None)
    if isinstance(max_candidate_flips, bool) or not isinstance(max_candidate_flips, int):
        return _blocked("PICKEM_WEEKLY_EQUITY_SEARCH_RADIUS_INVALID", pool_entries=pool_entries)
    if not 0 <= max_candidate_flips <= _MAX_SEARCH_FLIPS:
        return _blocked("PICKEM_WEEKLY_EQUITY_SEARCH_RADIUS_INVALID", pool_entries=pool_entries)

    games, game_blockers = _parse_games(board)
    if game_blockers:
        return _blocked(*game_blockers, pool_entries=pool_entries)
    shares, share_blockers = _parse_pick_shares(opponent_pick_shares, games)
    if share_blockers:
        return _blocked(*share_blockers, pool_entries=pool_entries)

    baseline_picks = {game.event_id: game.baseline_pick for game in games}
    baseline_mask = _mask_for_picks(games, baseline_picks)
    masks, outcome_prob, tie_equity, sole_win, popcount = _outcome_state(
        games, shares, pool_entries
    )

    baseline_equity, baseline_sole = _evaluate_mask(
        baseline_mask,
        game_count=len(games),
        masks=masks,
        outcome_prob=outcome_prob,
        tie_equity=tie_equity,
        sole_win=sole_win,
        popcount=popcount,
    )

    best_mask = baseline_mask
    best_equity = baseline_equity
    best_sole = baseline_sole
    candidates_evaluated = 0
    for candidate in _candidate_masks(baseline_mask, len(games), max_candidate_flips):
        candidates_evaluated += 1
        equity, sole = _evaluate_mask(
            candidate,
            game_count=len(games),
            masks=masks,
            outcome_prob=outcome_prob,
            tie_equity=tie_equity,
            sole_win=sole_win,
            popcount=popcount,
        )
        if equity > best_equity + 1e-15 or (
            isclose(equity, best_equity, abs_tol=1e-15) and sole > best_sole + 1e-15
        ):
            best_mask, best_equity, best_sole = candidate, equity, sole

    optimized = _card_from_mask(games, best_mask)
    baseline = _card_from_mask(games, baseline_mask)
    differences: list[dict[str, Any]] = []
    picks: list[dict[str, Any]] = []
    share_by_id = {share.event_id: share for share in shares}
    for game in games:
        selected = optimized[game.event_id]
        selected_probability = (
            game.home_probability if selected == game.home_team else game.away_probability
        )
        share = share_by_id[game.event_id]
        selected_pool_share = (
            share.home_pick_share if selected == game.home_team else share.away_pick_share
        )
        item = {
            "official_event_id": game.event_id,
            "home_team": game.home_team,
            "away_team": game.away_team,
            "baseline_pick": baseline[game.event_id],
            "weekly_equity_pick": selected,
            "selection_changed": selected != baseline[game.event_id],
            "selected_governed_probability": selected_probability,
            "home_governed_probability": game.home_probability,
            "away_governed_probability": game.away_probability,
            "selected_pool_pick_share": selected_pool_share,
            "opponent_pick_share_snapshot_id": share.snapshot_id,
            "opponent_pick_share_source": share.source,
            "opponent_pick_share_observed_at": share.observed_at,
            "opponent_pick_share_freshness_status": share.freshness_status,
            "sporting_probability_modified": False,
            "pool_popularity_used_as_sporting_probability": False,
            "can_execute": False,
        }
        picks.append(item)
        if item["selection_changed"]:
            differences.append(item)

    return {
        "optimizer_version": OPTIMIZER_VERSION,
        "decision_objective": WEEKLY_OBJECTIVE,
        "status": WEEKLY_EQUITY_READY,
        "pool_entries": pool_entries,
        "game_count": len(games),
        "equity_method": EQUITY_METHOD,
        "assumptions": [
            "GAME_OUTCOMES_INDEPENDENT_GIVEN_GOVERNED_MARGINALS",
            "OPPONENT_ENTRIES_IID_FROM_EVENT_PICK_SHARES",
            "TIES_USE_EQUAL_SHARE_PROXY_UNLESS_SEPARATE_TIEBREAKER_MODEL_IS_APPLIED",
        ],
        "search_radius_flips": max_candidate_flips,
        "global_optimum_proven": max_candidate_flips >= len(games),
        "candidates_evaluated": candidates_evaluated,
        "baseline_expected_correct": _card_expected_correct(games, baseline_mask),
        "optimized_expected_correct": _card_expected_correct(games, best_mask),
        "baseline_first_place_equity": baseline_equity,
        "optimized_first_place_equity": best_equity,
        "first_place_equity_gain": best_equity - baseline_equity,
        "baseline_sole_first_probability": baseline_sole,
        "optimized_sole_first_probability": best_sole,
        "sole_first_probability_gain": best_sole - baseline_sole,
        "changed_pick_count": len(differences),
        "picks": picks,
        "differences_from_max_expected_correct": differences,
        "opponent_pick_share_provenance_complete": True,
        "sporting_probability_modified": False,
        "market_probability_used": False,
        "sportsbook_price_used": False,
        "pool_popularity_used": True,
        "pool_popularity_used_as_sporting_probability": False,
        "tiebreaker_opponent_guess_distribution_modeled": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "EQUITY_METHOD",
    "OPTIMIZER_VERSION",
    "WEEKLY_EQUITY_BLOCKED",
    "WEEKLY_EQUITY_READY",
    "WEEKLY_OBJECTIVE",
    "optimize_weekly_win_equity",
]
