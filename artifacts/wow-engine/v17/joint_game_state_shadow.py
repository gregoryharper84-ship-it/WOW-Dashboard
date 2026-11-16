"""V17 research-only projection of aligned fitted joint game-state draws.

This module does not fit parameters, simulate independent variables, infer missing
specialists, access odds, certify probabilities, or participate in V17 routing.
Its inputs must come from a separately governed fitted generator. Sharing each
sample index across outcomes preserves dependence already present in that source.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from re import fullmatch
from typing import Mapping, Sequence

MIN_SIMULATION_DRAWS = 50_000
CUSTOM_GPT_IDENTITY = "WOW_BETTING_ENGINE"
RUNTIME_GENERATION = "V17_ACTIVE"
CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
GLOBAL_TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = True
_TIE_RULES = frozenset({"PUSH", "THREE_WAY", "NO_TIES"})


def _real(value: object, name: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not isfinite(result) or (nonnegative and result < 0):
        raise ValueError(f"{name} must be finite" + (" and nonnegative" if nonnegative else ""))
    return result


def _scores(values: Sequence[int], name: str) -> tuple[int, ...]:
    if not isinstance(values, (tuple, list)):
        raise ValueError(f"{name} must be a score sequence")
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 1_000_000:
            raise ValueError(f"{name} must contain nonnegative bounded integer scores")
    return tuple(values)


def _as_utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone aware")
    return value.astimezone(timezone.utc)


def _fractions(*, more: int, exact: int, less: int, count: int) -> dict[str, float]:
    return {"more": more / count, "exact": exact / count, "less": less / count}


def project_joint_shadow(
    *,
    event_id: str,
    fitted_source_model_id: str,
    fitted_artifact_sha256: str,
    captured_at: datetime,
    event_start_at: datetime,
    home_scores: Sequence[int],
    away_scores: Sequence[int],
    home_spread: float,
    game_total: float,
    moneyline_tie_rule: str,
    simulation_seed: int,
    player_props: Mapping[str, tuple[float, Sequence[float]]] | None = None,
    source_sport: str | None = None,
    source_settlement_basis: str | None = None,
    source_score_unit: str | None = None,
) -> dict:
    """Summarize *existing* aligned draws without granting probability authority.

    home_spread is the signed handicap applied to the home final score.
    Player props map stable identifiers to (line, draws), all same-index aligned.
    ``exact`` mass is retained for integer lines; consumers must apply their
    own approved, sport-and-market-specific settlement policy.
    """
    # These describe the caller's source-score contract, not third-party
    # certification. They are optional for legacy shadow consumers; downstream
    # seven-sport settlement refuses to accept their absence.
    source_fields = (source_sport, source_settlement_basis, source_score_unit)
    if any(v is not None for v in source_fields):
        if not all(isinstance(v, str) and v.strip() for v in source_fields):
            raise ValueError("source sport, settlement basis and score unit must be bound together")
        if source_score_unit not in {"FINAL_SCORE", "MATCH_GAMES"}:
            raise ValueError("source score unit unsupported")
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("event_id must be explicit")
    if not isinstance(fitted_source_model_id, str) or not fitted_source_model_id.strip():
        raise ValueError("fitted_source_model_id must be explicit")
    if not isinstance(fitted_artifact_sha256, str) or not fullmatch(r"[0-9a-f]{64}", fitted_artifact_sha256):
        raise ValueError("fitted_artifact_sha256 must be a lowercase SHA-256 digest")
    if not isinstance(simulation_seed, int) or isinstance(simulation_seed, bool) or simulation_seed < 0:
        raise ValueError("simulation_seed must be a nonnegative integer")
    if moneyline_tie_rule not in _TIE_RULES:
        raise ValueError("moneyline_tie_rule must specify PUSH, THREE_WAY or NO_TIES")
    if not _as_utc(captured_at, "captured_at") < _as_utc(event_start_at, "event_start_at"):
        raise ValueError("source capture must strictly precede event start")
    home = _scores(home_scores, "home_scores")
    away = _scores(away_scores, "away_scores")
    count = len(home)
    if count < MIN_SIMULATION_DRAWS or len(away) != count:
        raise ValueError("joint final-score draws must be aligned and meet the 50000-draw minimum")
    spread = _real(home_spread, "home_spread")
    total = _real(game_total, "game_total", nonnegative=True)
    home_wins = sum(h > a for h, a in zip(home, away))
    away_wins = sum(h < a for h, a in zip(home, away))
    draws = count - home_wins - away_wins
    if moneyline_tie_rule == "NO_TIES" and draws:
        raise ValueError("unresolved final-score ties violate NO_TIES moneyline settlement")
    spread_more = sum(h - a + spread > 0 for h, a in zip(home, away))
    spread_exact = sum(h - a + spread == 0 for h, a in zip(home, away))
    total_more = sum(h + a > total for h, a in zip(home, away))
    total_exact = sum(h + a == total for h, a in zip(home, away))
    props = {}
    if player_props is not None:
        if not isinstance(player_props, Mapping):
            raise ValueError("player_props must map stable prop IDs to lines and aligned draws")
        for prop_id, pair in player_props.items():
            if not isinstance(prop_id, str) or not prop_id.strip():
                raise ValueError("player prop must have a stable nonempty identity")
            if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                raise ValueError("player prop must contain exactly line and aligned draws")
            line = _real(pair[0], "player prop line", nonnegative=True)
            values = pair[1]
            if not isinstance(values, (tuple, list)) or len(values) != count:
                raise ValueError("player prop draws must align with final-score draws")
            numeric = tuple(_real(value, "player prop draw", nonnegative=True) for value in values)
            more = sum(x > line for x in numeric)
            exact = sum(x == line for x in numeric)
            props[prop_id] = {"line": line, **_fractions(more=more, exact=exact, less=count - more - exact, count=count)}
    return {
        "source_sport": source_sport,
        "source_settlement_basis": source_settlement_basis,
        "source_score_unit": source_score_unit,
        "event_id": event_id,
        "fitted_source_model_id": fitted_source_model_id,
        "fitted_artifact_sha256": fitted_artifact_sha256,
        "captured_at": captured_at.isoformat(),
        "event_start_at": event_start_at.isoformat(),
        "simulation_seed": simulation_seed,
        "draw_count": count,
        "moneyline_tie_rule": moneyline_tie_rule,
        "moneyline": {"home_win": home_wins / count, "away_win": away_wins / count, "draw": draws / count},
        "home_spread": {"line": spread, **_fractions(more=spread_more, exact=spread_exact, less=count - spread_more - spread_exact, count=count)},
        "game_total": {"line": total, **_fractions(more=total_more, exact=total_exact, less=count - total_more - total_exact, count=count)},
        "player_props": props,
        "shadow_only": True,
        "probability_publishable": False,
        "automatic_certification": False,
        "can_execute": False,
        "custom_gpt_identity": CUSTOM_GPT_IDENTITY,
        "runtime_generation": RUNTIME_GENERATION,
        "terminal_authority": GLOBAL_TERMINAL_AUTHORITY,
        "dry_run_only_no_live_trading_no_market_orders": True,
    }
