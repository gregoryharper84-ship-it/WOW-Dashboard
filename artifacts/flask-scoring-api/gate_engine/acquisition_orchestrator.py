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
  - If missing and sport is supported, resolves player_id and ACTIVELY FETCHES
    the game log from the appropriate source (MLB Stats API for MLB, BallDontLie
    for NBA/WNBA), writing the result to enrichment[row_id] so the pipeline's
    _get_enrichment() finds it on the first key (rid).
  - Records GAME_LOG_ACQUISITION_UNAVAILABLE:reason per row when fetch fails.

WOW-PATCH-2026-08-16-R2 changes
  - _check_prop_game_log now calls _attempt_game_log_fetch after player_id
    resolution; advisory-only behaviour was the root cause of
    direct_game_log_feed=NOT_CALLED in production.
  - _resolve_mlb_player_id added (mirrors _resolve_bdl_player_id) with
    Unicode accent-strip fallback ("Jeremy Peña" → "Jeremy Pena" retry).
  - target_date threaded through to _attempt_game_log_fetch for correct
    MLB Stats API season selection.

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
    Pre-scoring acquisition pre-check + active fetch.

    Parameters
    ----------
    rows              : normalized row dicts (may include OUTRIGHT_WINNER)
    enrichment        : current enrichment dict, mutated in-place when game_log
                        is fetched successfully (keyed by row_id)
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
        "direct_game_log_feed": str,   # "FETCHED" | "NOT_CALLED" (prop rows only)
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
                target_date=target_date,
            )

        acquisition_report[row_id] = result

    return enrichment, acquisition_report


# ---------------------------------------------------------------------------
# Player-prop game-log check + active fetch
# ---------------------------------------------------------------------------

def _check_prop_game_log(
    row: dict[str, Any],
    enrichment: dict[str, Any],
    sport: str,
    *,
    auto_enrich_attempted: bool,
    target_date: str | None = None,
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
            "direct_game_log_feed": "PRESENT",
        }

    if sport not in _GAME_LOG_SUPPORTED:
        return {
            "status":               "UNSUPPORTED",
            "reason":               f"GAME_LOG_UNSUPPORTED:sport={sport}",
            "fields_populated":     [],
            "acquisition_attempted": False,
            "direct_game_log_feed": "NOT_CALLED",
        }

    # Resolve player_id — needed to call the game-log API
    player_id = row.get("player_id")
    player_name = row.get("player") or ""

    if not player_id:
        if sport in ("NBA", "WNBA"):
            player_id = _resolve_bdl_player_id(player_name, sport)
            if player_id:
                row["player_id"] = player_id
        elif sport == "MLB":
            player_id = _resolve_mlb_player_id(player_name)
            if player_id:
                row["player_id"] = player_id
        # TENNIS uses a different player_id scheme; fall through to honest gap

    # If player_id is now available, attempt an actual game_log fetch and write
    # the result into enrichment keyed by row_id so the pipeline's
    # _get_enrichment() finds it on the first lookup (rid path).
    if player_id:
        stat_key = row.get("stat_key") or row.get("prop_type") or ""
        values = _attempt_game_log_fetch(
            row_id=row_id,
            player_id=player_id,
            sport=sport,
            stat_key=stat_key,
            enrichment=enrichment,
            target_date=target_date,
        )
        if values:
            return {
                "status":               "ACQUIRED",
                "reason":               "game_log_fetched_by_orchestrator",
                "fields_populated":     ["game_log"],
                "acquisition_attempted": True,
                "direct_game_log_feed": "FETCHED",
            }
        # Fetch attempted but returned nothing — fail-closed
        reason = "GAME_LOG_ACQUISITION_UNAVAILABLE:fetch_failed_or_no_recent_games"
        _stamp_enrichment(enrichment, row_id, reason)
        return {
            "status":               "UNAVAILABLE",
            "reason":               reason,
            "fields_populated":     [],
            "acquisition_attempted": True,
            "direct_game_log_feed": "FAILED",
        }

    # No player_id — honest gap; stamp enrichment for pipeline visibility
    reason = "GAME_LOG_ACQUISITION_UNAVAILABLE:player_id_missing"
    _stamp_enrichment(enrichment, row_id, reason)
    return {
        "status":               "UNAVAILABLE",
        "reason":               reason,
        "fields_populated":     [],
        "acquisition_attempted": auto_enrich_attempted,
        "direct_game_log_feed": "NOT_CALLED",
    }


def _stamp_enrichment(enrichment: dict, row_id: str, reason: str) -> None:
    """Stamp acquisition_status in enrichment so the pipeline gate can see it."""
    if row_id not in enrichment:
        enrichment[row_id] = {}
    enrichment[row_id].setdefault("acquisition_status", reason)


# ---------------------------------------------------------------------------
# Player-ID resolution helpers
# ---------------------------------------------------------------------------

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


def _resolve_mlb_player_id(player_name: str) -> str | None:
    """
    Try to resolve MLB player_id via MLB Stats API /people/search.
    Includes Unicode accent-strip fallback ("Jeremy Peña" → "Jeremy Pena").

    WOW-PATCH-2026-08-16-R2: root-cause fix for NOT_CALLED on accented names.
    """
    if not player_name:
        return None

    def _query(name: str) -> str | None:
        try:
            import json
            import urllib.parse
            import urllib.request
            name_enc = urllib.parse.quote(name.strip())
            url = (
                f"https://statsapi.mlb.com/api/v1/people/search"
                f"?names={name_enc}&sportIds=1"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "WOW/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
            people = data.get("people") or []
            if people:
                pid = people[0].get("id")
                return str(pid) if pid else None
        except Exception as exc:
            logger.debug("MLB player-ID lookup failed for %r: %s", name, exc)
        return None

    result = _query(player_name)
    if result:
        return result

    # Fallback: strip Unicode combining marks (NFD decomposition)
    # so accented names like "Jeremy Peña" retry as "Jeremy Pena".
    try:
        import unicodedata
        ascii_name = "".join(
            c for c in unicodedata.normalize("NFD", player_name)
            if unicodedata.category(c) != "Mn"
        )
        if ascii_name != player_name:
            logger.debug(
                "Retrying MLB player-ID lookup with ASCII name: %r → %r",
                player_name, ascii_name,
            )
            return _query(ascii_name)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Game-log fetch + enrichment write
# ---------------------------------------------------------------------------

def _attempt_game_log_fetch(
    row_id: str,
    player_id: str,
    sport: str,
    stat_key: str,
    enrichment: dict[str, Any],
    target_date: str | None,
) -> list | None:
    """
    Fetch game_log via fetch_game_log and write it into enrichment[row_id].

    Writing to enrichment[row_id] ensures the pipeline's _get_enrichment()
    finds it on the first key lookup (rid path), avoiding write-key mismatches
    that occur when the pipeline's normalize_board() changes prop_type.

    Returns the fetched values list on success, None on any failure.
    Fail-closed: exceptions are caught and logged; never raises.
    """
    if not player_id or not stat_key:
        return None
    try:
        from gate_engine.auto_game_log import fetch_game_log, GameLogUnavailable  # noqa: F401
        result = fetch_game_log(
            player_id=player_id,
            sport=sport,
            stat_key=stat_key,
            target_date=target_date,
        )
        values = result.get("values")
        if not values:
            return None

        # Merge into enrichment[row_id], preserving any fields already set
        entry = dict(enrichment.get(row_id) or {})
        entry["game_log"] = values
        if entry.get("l5_values") is None:
            entry["l5_values"] = values[:5]
        if entry.get("l10_values") is None:
            entry["l10_values"] = values[:10]
        if result.get("game_date") and entry.get("game_date") is None:
            entry["game_date"] = result["game_date"]
        if result.get("opponent") and entry.get("opponent") is None:
            entry["opponent"] = result["opponent"]
        enrichment[row_id] = entry

        logger.info(
            "Orchestrator: fetched %d game_log values for row_id=%s sport=%s stat=%s",
            len(values), row_id, sport, stat_key,
        )
        return values
    except Exception as exc:
        logger.debug(
            "Orchestrator game_log fetch failed row_id=%s sport=%s stat=%s: %s",
            row_id, sport, stat_key, exc,
        )
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
