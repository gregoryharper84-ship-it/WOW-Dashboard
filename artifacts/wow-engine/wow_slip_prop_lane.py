"""
Thin WOW_PROP_LANE adapter into the paper-trade slip builder.

Maps *already-authorized* eligible prop outputs into PropLegCandidate rows.
Never invents calibrated probabilities, never upgrades uncertified outputs,
and never enables live execution.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from wow_slip_builder import (
    ArchetypeId,
    Direction,
    MatchupTag,
    Modifier,
    PropLegCandidate,
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
    text = str(raw or "STANDARD").strip().upper()
    if text in {"NORMAL", "STANDARD"}:
        return Modifier.STANDARD
    if text == "DEMON":
        return Modifier.DEMON
    if text == "GOBLIN":
        return Modifier.GOBLIN
    return Modifier.STANDARD


def _parse_tags(raw: Any) -> list[MatchupTag]:
    if not raw:
        return []
    out: list[MatchupTag] = []
    values = raw if isinstance(raw, (list, tuple)) else [raw]
    for item in values:
        try:
            out.append(MatchupTag(str(item)))
        except ValueError:
            continue
    return out


def is_certified_eligible(row: Mapping[str, Any]) -> bool:
    """Fail closed unless the row carries explicit certification evidence."""
    if _as_bool(row.get("can_execute"), default=False):
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


def prop_row_to_leg_candidate(row: Mapping[str, Any]) -> PropLegCandidate | None:
    """
    Convert one Prop Lane row into a PropLegCandidate, or None when ineligible.

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
    captured = row.get("captured_at")
    if not isinstance(start, datetime):
        return None
    if not isinstance(captured, datetime):
        # Fail closed without inventing a capture timestamp.
        return None
    leg_id = str(row.get("leg_id") or row.get("row_id") or "").strip()
    player = str(
        row.get("player_or_combo")
        or row.get("player_name")
        or row.get("player_id")
        or ""
    ).strip()
    event_id = str(
        row.get("event_id") or row.get("game_id") or row.get("official_event_id") or ""
    ).strip()
    market = str(row.get("market") or row.get("stat_type") or "").strip()
    if not (leg_id and player and event_id and market):
        return None
    if not _as_bool(row.get("is_confirmed_active"), default=False):
        return None
    sport = str(row.get("sport") or "NBA").strip().upper()
    platform = str(row.get("platform") or "prizepicks").strip().lower()
    if platform not in {"prizepicks", "kalshi"}:
        return None
    try:
        projected_mean = float(row.get("projected_mean", line))
        projected_std = float(row.get("projected_std", 1.0))
        opening = row.get("opening_line")
        opening_line = float(opening) if opening is not None else None
        return PropLegCandidate(
            leg_id=leg_id,
            platform=platform,  # type: ignore[arg-type]
            sport=sport,  # type: ignore[arg-type]
            event_id=event_id,
            team=str(row.get("team") or row.get("team_abbr") or "UNK"),
            player_or_combo=player,
            market=market,
            line=line,
            opening_line=opening_line,
            direction=direction,
            modifier=_parse_modifier(row.get("modifier")),
            is_promo_square=_as_bool(row.get("is_promo_square"), default=False),
            is_protected=_as_bool(row.get("is_protected"), default=True),
            signal_source=str(row.get("signal_source") or "wow_prop_lane"),
            matchup_tags=_parse_tags(row.get("matchup_tags")),
            calibrated_probability=prob,
            calibrated_lower_bound=lower,
            projected_mean=projected_mean,
            projected_std=projected_std,
            is_confirmed_active=True,
            captured_at=captured,
            event_start_time=start,
        )
    except Exception:
        return None


def eligible_candidates_from_prop_rows(
    rows: list[Mapping[str, Any]],
) -> list[PropLegCandidate]:
    """Filter Prop Lane rows to certified PropLegCandidates only."""
    out: list[PropLegCandidate] = []
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
    payout_multiplier: float | None = None,
    slips_built_today: int = 0,
    min_lower_bound: float = 0.0,
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
        payout_multiplier=payout_multiplier,
        slips_built_today=slips_built_today,
        min_lower_bound=min_lower_bound,
    )


# Compat alias
LegCandidate = PropLegCandidate

__all__ = [
    "LegCandidate",
    "build_paper_slip_from_prop_lane",
    "eligible_candidates_from_prop_rows",
    "is_certified_eligible",
    "prop_row_to_leg_candidate",
]
