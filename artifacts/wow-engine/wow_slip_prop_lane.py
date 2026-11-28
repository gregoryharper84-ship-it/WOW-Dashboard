"""
Thin WOW_PROP_LANE adapter into the paper-trade slip builder.

Maps *already-authorized* eligible prop outputs into LegCandidate rows.
Never invents calibrated probabilities, never upgrades uncertified outputs,
and never enables live execution.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from wow_slip_builder import (
    ArchetypeId,
    Direction,
    LegCandidate,
    Modifier,
    SlipSnapshot,
    build_paper_slip,
)

# Explicit certification labels that may enter paper-trade construction.
_ELIGIBLE_CERT_LABELS = frozenset(
    {
        "CERTIFIED_FITTED",
        "PROSPECTIVE_CERTIFIED",
        "CHAMPION",
        "CERTIFIED",
    }
)


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return default


def _parse_direction(raw: Any) -> Direction | None:
    if raw is None:
        return None
    text = str(raw).strip().upper()
    if text in {"MORE", "OVER", "O"}:
        return Direction.MORE
    if text in {"LESS", "UNDER", "U"}:
        return Direction.LESS
    return None


def _parse_modifier(raw: Any) -> Modifier:
    text = str(raw or "NORMAL").strip().upper()
    if text == "DEMON":
        return Modifier.DEMON
    if text == "GOBLIN":
        return Modifier.GOBLIN
    return Modifier.NORMAL


def is_certified_eligible(row: Mapping[str, Any]) -> bool:
    """Fail closed unless the row carries explicit certification evidence."""
    if _as_bool(row.get("can_execute"), default=False):
        # Live-execution rows are never admitted into paper-trade construction.
        return False
    label = str(
        row.get("certification_label")
        or row.get("prediction_class")
        or row.get("model_certification_state")
        or ""
    ).strip().upper()
    if label in _ELIGIBLE_CERT_LABELS:
        return True
    if _as_bool(row.get("probability_publishable"), default=False) and _as_bool(
        row.get("model_package_valid"), default=False
    ):
        return True
    return False


def prop_row_to_leg_candidate(row: Mapping[str, Any]) -> LegCandidate | None:
    """
    Convert one Prop Lane row into a LegCandidate, or None when ineligible.

    Uncertified / incomplete rows return None — they are not replaced with
    qualitative or market-implied probability.
    """
    if not is_certified_eligible(row):
        return None
    direction = _parse_direction(row.get("direction") or row.get("side"))
    if direction is None:
        return None
    try:
        lower = float(row["calibrated_lower_bound"])
        prob = float(row["calibrated_probability"])
        line = float(row["line"])
    except (KeyError, TypeError, ValueError):
        return None
    start = row.get("event_start_time") or row.get("event_start_time_utc")
    if not isinstance(start, datetime):
        return None
    leg_id = str(row.get("leg_id") or row.get("row_id") or "").strip()
    player_id = str(row.get("player_id") or "").strip()
    game_id = str(row.get("game_id") or row.get("official_event_id") or "").strip()
    market = str(row.get("market") or row.get("stat_type") or "").strip()
    if not (leg_id and player_id and game_id and market):
        return None
    if not _as_bool(row.get("is_confirmed_active"), default=False):
        return None
    try:
        return LegCandidate(
            leg_id=leg_id,
            player_id=player_id,
            player_name=str(row.get("player_name") or player_id),
            game_id=game_id,
            market=market,
            direction=direction,
            line=line,
            modifier=_parse_modifier(row.get("modifier")),
            calibrated_probability=prob,
            calibrated_lower_bound=lower,
            event_start_time=start,
            is_confirmed_active=True,
            is_promo_square=_as_bool(row.get("is_promo_square"), default=False),
        )
    except Exception:
        return None


def eligible_candidates_from_prop_rows(
    rows: list[Mapping[str, Any]],
) -> list[LegCandidate]:
    """Filter Prop Lane rows to certified LegCandidates only."""
    out: list[LegCandidate] = []
    for row in rows:
        cand = prop_row_to_leg_candidate(row)
        if cand is not None:
            out.append(cand)
    return out


def build_paper_slip_from_prop_lane(
    archetype: ArchetypeId | str,
    rows: list[Mapping[str, Any]],
    captured_at: datetime,
    *,
    min_lower_bound: float = 0.55,
) -> SlipSnapshot:
    """
    Build a paper slip from Prop Lane rows.

    Uncertified rows are dropped; insufficient certified inventory fails closed
    inside build_paper_slip().
    """
    candidates = eligible_candidates_from_prop_rows(rows)
    return build_paper_slip(
        archetype,
        candidates,
        captured_at,
        min_lower_bound=min_lower_bound,
    )


__all__ = [
    "build_paper_slip_from_prop_lane",
    "eligible_candidates_from_prop_rows",
    "is_certified_eligible",
    "prop_row_to_leg_candidate",
]
