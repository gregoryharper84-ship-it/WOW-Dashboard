"""
WOW-PATCH-2026-08-16-ACQUISITION-ORCHESTRATOR
Pre-scoring acquisition orchestration layer.

Purpose
-------
Runs BEFORE the gate-engine pipeline on every /gate-engine/run call and
produces an explicit per-row acquisition_report that surfaces data-gap
blockers BEFORE cryptic pipeline gate failures.

Player-prop rows
  - Checks whether game_log is populated in enrichment after auto-enrich.
  - If missing and sport is supported, attempts BallDontLie player-ID lookup
    (NBA/WNBA) so a subsequent fetch_missing_game_logs call can use it.
  - Records GAME_LOG_ACQUISITION_UNAVAILABLE:reason per row.

OUTRIGHT_WINNER (moneyline) rows
  - Delegates to gate_engine.moneyline.team_acquisition for supported sports.
  - Records MONEYLINE_ACQUISITION_UNAVAILABLE:sport_not_supported for others.

Invariants
----------
- can_execute = False — advisory pre-check only
- Never fabricates game_log values or probabilities
- Fail-closed: missing sources produce explicit per-row blockers
- Idempotent: running twice does not double-register anything
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Sports where game-log auto-acquisition is attempted
_GAME_LOG_SUPPORTED: frozenset[str] = frozenset({"NBA", "WNBA", "MLB", "TENNIS"})

# Sports where moneyline team data acquisition is supported
_MONEYLINE_TEAM_SUPPORTED: frozenset[str] = frozenset({"NBA", "MLB"})

can_execute = False
PATCH_ID    = "WOW-PATCH-2026-08-16-ACQUISITION-ORCHESTRATOR"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(
    rows: list[dict[str, Any]],
    enrichment: dict[str, Any],
    *,
    target_date: str | None = None,
    auto_enrich_attempted: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Pre-scoring acquisition pre-check.

    Parameters
    ----------
    rows              : normalized row dicts (may include OUTRIGHT_WINNER)
    enrichment        : current enrichment dict, mutated in-place with
                        acquisition_status fields when needed
    target_date       : ISO date string for context
    auto_enrich_attempted : whether build_auto_enrichment already ran

    Returns
    -------
    (enrichment, acquisition_report)

    acquisition_report : {row_id: {
        "status": "ACQUIRED" | "UNAVAILABLE" | "NOT_ATTEMPTED" | "UNSUPPORTED",
        "reason": str,
        "fields_populated": list[str],
        "acquisition_attempted": bool,
    }}
    """
    acquisition_report: dict[str, dict] = {}

    for row in rows:
        row_id    = str(row.get("row_id") or row.get("player") or "unknown")
        mkt_fam   = (row.get("market_family") or "PLAYER_PROP").upper()
        sport     = (row.get("sport") or "").upper()

        if mkt_fam == "OUTRIGHT_WINNER":
            result = _check_moneyline_acquisition(row, enrichment, sport)
        else:
            result = _check_prop_game_log(
                row, enrichment, sport,
                auto_enrich_attempted=auto_enrich_attempted,
            )

        acquisition_report[row_id] = result

    return enrichment, acquisition_report


# ---------------------------------------------------------------------------
# Player-prop game-log check
# ---------------------------------------------------------------------------

def _check_prop_game_log(
    row: dict[str, Any],
    enrichment: dict[str, Any],
    sport: str,
    *,
    auto_enrich_attempted: bool,
) -> dict[str, Any]:
    row_id    = str(row.get("row_id") or row.get("player") or "unknown")
    enr_entry = enrichment.get(row_id) or {}

    # Already populated (by caller or auto_enrich)?
    game_log = enr_entry.get("game_log") or row.get("game_log")
    if game_log:
        return {
            "status":               "ACQUIRED",
            "reason":               "game_log_present",
            "fields_populated":     ["game_log"],
            "acquisition_attempted": False,
        }

    if sport not in _GAME_LOG_SUPPORTED:
        return {
            "status":               "UNSUPPORTED",
            "reason":               f"GAME_LOG_UNSUPPORTED:sport={sport}",
            "fields_populated":     [],
            "acquisition_attempted": False,
        }

    # Game log expected but missing — determine why and stamp enrichment
    player_id = row.get("player_id")
    if not player_id and sport in ("NBA", "WNBA"):
        player_id = _resolve_bdl_player_id(row.get("player") or "", sport)
        if player_id:
            row["player_id"] = player_id  # carry forward for pipeline fetch

    reason = (
        "GAME_LOG_ACQUISITION_UNAVAILABLE:player_id_missing"
        if not player_id
        else "GAME_LOG_ACQUISITION_UNAVAILABLE:fetch_failed_or_no_recent_games"
    )

    # Stamp acquisition_status in enrichment so the pipeline gate can see it
    if row_id not in enrichment:
        enrichment[row_id] = {}
    enrichment[row_id].setdefault("acquisition_status", reason)

    return {
        "status":               "UNAVAILABLE",
        "reason":               reason,
        "fields_populated":     [],
        "acquisition_attempted": auto_enrich_attempted or bool(player_id),
    }


def _resolve_bdl_player_id(player_name: str, sport: str) -> str | None:
    """
    Try to resolve player_id from BallDontLie player-search.
    Returns BDL player ID as string, or None on failure.
    """
    if not player_name:
        return None
    try:
        from gate_engine.balldontlie.client import fetch_all as _bdl_fetch_all
        endpoint = (
            "https://api.balldontlie.io/v1/players"
            if sport == "NBA"
            else "https://api.balldontlie.io/wnba/v1/players"
        )
        resp = _bdl_fetch_all(endpoint, params={"search": player_name}, max_pages=1, per_page=5)
        if resp and resp.ok and resp.data:
            return str(resp.data[0]["id"])
    except Exception as exc:
        logger.debug("BDL player-ID resolve failed for %r: %s", player_name, exc)
    return None


# ---------------------------------------------------------------------------
# Moneyline acquisition check
# ---------------------------------------------------------------------------

def _check_moneyline_acquisition(
    row: dict[str, Any],
    enrichment: dict[str, Any],
    sport: str,
) -> dict[str, Any]:
    if sport not in _MONEYLINE_TEAM_SUPPORTED:
        return {
            "status":               "UNSUPPORTED",
            "reason":               (
                f"MONEYLINE_ACQUISITION_UNAVAILABLE:sport_not_supported:{sport}"
            ),
            "fields_populated":     [],
            "acquisition_attempted": False,
        }

    row_id    = str(row.get("row_id") or row.get("team") or row.get("player") or "unknown")
    enr_entry = enrichment.get(row_id) or {}

    populated = [
        k for k in (
            "home_win_pct", "away_win_pct",
            "home_power",   "away_power",
            "game_log",
        )
        if enr_entry.get(k) is not None
    ]

    if populated:
        return {
            "status":               "ACQUIRED",
            "reason":               "team_non_market_data_present",
            "fields_populated":     populated,
            "acquisition_attempted": False,
        }

    # Attempt team acquisition
    try:
        from gate_engine.moneyline.team_acquisition import acquire_team_data
        team_data = acquire_team_data(row, sport)
        if team_data:
            if row_id not in enrichment:
                enrichment[row_id] = {}
            enrichment[row_id].update(team_data)
            populated = list(team_data.keys())
            return {
                "status":               "ACQUIRED",
                "reason":               f"team_data_fetched:{sport}",
                "fields_populated":     populated,
                "acquisition_attempted": True,
            }
    except Exception as exc:
        logger.warning("Moneyline team acquisition failed for %s/%s: %s", sport, row_id, exc)

    return {
        "status":               "UNAVAILABLE",
        "reason":               (
            f"MONEYLINE_ACQUISITION_UNAVAILABLE:team_data_fetch_failed:{sport}"
        ),
        "fields_populated":     [],
        "acquisition_attempted": True,
    }
