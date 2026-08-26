"""
gate_engine/balldontlie/anti_double_count.py
WOW-PATCH-2026-08-08-BALLDONTLIE-TRUSTED-STATS

Odds anti-double-counting for BallDontLie.

Prevents BDL-sourced sportsbook observations from being counted again as
an independent market source if the same book observation already exists
through another adapter (Odds API, ESPN, etc.).

Deduplication key: normalized lowercase book_name + side + price (American odds).
Tolerance for price match: within ±2 cents (handles minor data-source rounding).

Rules
-----
1. BDL odds for a book already present in enrichment["sportsbook_odds"]
   with matching price → mark as CORROBORATED, do NOT add again
2. BDL odds for a book not yet in enrichment → add with BDL provenance
3. BDL odds for a book already present with different price → SOURCE_CONFLICT
4. BDL "player props" from /player_props are counted separately from
   team-level moneyline/spread odds (different market type)

can_execute=False unconditional.
"""
from __future__ import annotations

import re
from typing import Any

can_execute: bool = False  # UNCONDITIONAL

from gate_engine.balldontlie.types import BDLStatus, BDL_SOURCE_NAME

_PRICE_TOLERANCE = 2   # ±2 in American odds units (e.g. -145 vs -143 → same)


def _normalize_book_name(name: str) -> str:
    """Canonical book name: lowercase, alphanum only."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower().strip())


def _safe_int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _prices_match(p1: int | None, p2: int | None) -> bool:
    if p1 is None or p2 is None:
        return False
    return abs(p1 - p2) <= _PRICE_TOLERANCE


# ---------------------------------------------------------------------------
# Main deduplication function
# ---------------------------------------------------------------------------

def deduplicate_odds(
    enrichment_odds:  list[dict[str, Any]],
    bdl_odds:         list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Deduplicate BDL odds against existing enrichment sportsbook_odds.

    Parameters
    ----------
    enrichment_odds : enrichment["sportsbook_odds"] — existing book observations
    bdl_odds        : BDL-sourced odds rows (from /v1/odds or similar)

    Returns
    -------
    (merged_odds, dedup_notes)
      merged_odds : enrichment_odds + any new BDL books not already present
      dedup_notes : list of descriptive deduplication notes
    """
    notes: list[str] = []

    # Index existing books by (normalized_name, side)
    existing_index: dict[tuple[str, str], int] = {}   # → American odds price
    for obs in enrichment_odds:
        book  = _normalize_book_name(obs.get("name") or obs.get("book") or "")
        side  = str(obs.get("side") or obs.get("team") or "").lower().strip()
        price = _safe_int(obs.get("odds") or obs.get("price"))
        if book and side:
            existing_index[(book, side)] = price

    new_observations: list[dict[str, Any]] = []

    for bdl_obs in bdl_odds:
        book  = _normalize_book_name(bdl_obs.get("name") or bdl_obs.get("book") or "")
        side  = str(bdl_obs.get("side") or bdl_obs.get("team") or "").lower().strip()
        price = _safe_int(bdl_obs.get("odds") or bdl_obs.get("price"))

        if not book:
            notes.append("bdl_odds:skipped:no_book_name")
            continue

        key = (book, side)
        if key not in existing_index:
            # New book observation — add with BDL provenance
            obs_with_prov = dict(bdl_obs)
            obs_with_prov["source"]       = BDL_SOURCE_NAME
            obs_with_prov["source_status"] = BDLStatus.OK
            new_observations.append(obs_with_prov)
            notes.append(f"bdl_odds:added:{book}:{side}:{price}")
        elif _prices_match(price, existing_index[key]):
            # Already exists with matching price → corroborated, do NOT add again
            notes.append(f"bdl_odds:CORROBORATED:{book}:{side}:{price}")
        else:
            # Same book, different price → SOURCE_CONFLICT
            notes.append(
                f"bdl_odds:SOURCE_CONFLICT:{book}:{side}:"
                f"existing={existing_index[key]}:bdl={price}"
            )
            # Mark the existing observation as conflicted (do not overwrite)
            for obs in enrichment_odds:
                b = _normalize_book_name(obs.get("name") or obs.get("book") or "")
                s = str(obs.get("side") or obs.get("team") or "").lower()
                if b == book and s == side:
                    obs["bdl_conflict"] = True
                    obs["bdl_conflict_price"] = price
                    break

    merged = list(enrichment_odds) + new_observations
    notes.append(
        f"dedup_summary:existing={len(enrichment_odds)}"
        f":bdl_added={len(new_observations)}"
        f":total={len(merged)}"
    )
    return merged, notes


def deduplicate_player_props(
    enrichment_props: list[dict[str, Any]],
    bdl_props:        list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Deduplicate BDL player props (from /player_props) against existing props.

    Dedup key: (book, player_id, market_type, line).
    Player props are counted separately from team-level odds.
    """
    notes: list[str] = []

    existing_index: set[tuple[str, str, str, str]] = set()
    for prop in enrichment_props:
        key = (
            _normalize_book_name(prop.get("book") or prop.get("name") or ""),
            str(prop.get("player_id") or ""),
            str(prop.get("market_type") or ""),
            str(prop.get("line") or ""),
        )
        existing_index.add(key)

    new_props: list[dict[str, Any]] = []
    for bdl_prop in bdl_props:
        key = (
            _normalize_book_name(bdl_prop.get("book") or bdl_prop.get("name") or ""),
            str(bdl_prop.get("player_id") or ""),
            str(bdl_prop.get("market_type") or ""),
            str(bdl_prop.get("line") or ""),
        )
        if key not in existing_index:
            prop_with_prov = dict(bdl_prop)
            prop_with_prov["source"] = BDL_SOURCE_NAME
            new_props.append(prop_with_prov)
            notes.append(f"bdl_prop:added:{':'.join(key)}")
        else:
            notes.append(f"bdl_prop:CORROBORATED:{':'.join(key)}")

    merged = list(enrichment_props) + new_props
    notes.append(
        f"prop_dedup:existing={len(enrichment_props)}"
        f":added={len(new_props)}:total={len(merged)}"
    )
    return merged, notes
