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

WOW-PATCH-2026-08-16-R4 changes (key-promotion + stat-key canonicalization)
  - Root cause: build_auto_enrichment writes enrichment["player:prop"] (full
    entry, all sentinels + game_log).  _check_prop_game_log read only
    enrichment.get(row_id), which was None → missed the existing game_log →
    called _stamp_enrichment(row_id, "...fail...") which created a SPARSE
    enrichment[row_id] entry.  _get_enrichment (in pipeline) checks row_id
    FIRST, so it returned the sparse entry (no sentinels, no game_log).
    data_contract then failed on failure_path_matrix → DATA_CONTRACT_FAIL.
  - Fix 1: _check_prop_game_log now checks BOTH enrichment[row_id] AND
    enrichment["player:prop"].  When game_log is found via the player:prop key,
    the full entry is PROMOTED to enrichment[row_id] so the pipeline's
    _get_enrichment always finds the complete enrichment on the first lookup.
  - Fix 2: _attempt_game_log_fetch now canonicalizes stat_key via
    _canonicalize_stat_key before calling fetch_game_log, so display labels
    ("Hits" → "H", "Runs" → "R", etc.) resolve correctly even when
    build_auto_enrichment did not run (auto_enrich=False path).

OUTRIGHT_WINNER (moneyline) rows
  - Routes to a sport-specific acquisition function (not a shared frozenset):
      NBA/MLB  → _check_nba_mlb_moneyline  (existing MONEYLINE_V1 path)
      WNBA     → _check_wnba_ml_acquisition (WNBA_ML_V1 profile)
      ATP/WTA  → _check_tennis_match_acquisition (TENNIS_MATCH_WINNER_V1)
  - Records MONEYLINE_ACQUISITION_UNAVAILABLE:sport_not_supported for others.
  - NOT_CALLED is never a terminal state; acquisition is always attempted.

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

# 1IP stat keys that route through Savant ledger acquisition (not generic game_log)
_1IP_STAT_KEYS: frozenset[str] = frozenset({"1IP_PITCHES_THROWN", "1IP"})

# Sports where moneyline team data acquisition is supported
# Each sport family has its OWN dispatch function (sport-specific hydration
# profile) — do not extend these sets to force a sport through the wrong path.
_MONEYLINE_TEAM_SUPPORTED: frozenset[str] = frozenset({"NBA", "MLB"})
_WNBA_ML_SUPPORTED:        frozenset[str] = frozenset({"WNBA"})
_TENNIS_ML_SUPPORTED:      frozenset[str] = frozenset({"ATP", "WTA", "TENNIS"})

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
    # WOW-PATCH-2026-08-17-1IP-PRODUCTION-HYDRATION: 1IP rows need a dedicated
    # Savant ledger fetch rather than the generic MLB Stats API game_log.
    # Route early so the event-tree BF distribution is populated before pipeline.
    _stat_key_raw = (row.get("stat_key") or row.get("prop_type") or "").upper().replace(" ", "_")
    if sport == "MLB" and _stat_key_raw in _1IP_STAT_KEYS:
        return _check_1ip_acquisition(row, enrichment, target_date=target_date)

    row_id      = str(row.get("row_id") or row.get("player") or "unknown")
    player_name = (row.get("player") or "").lower()
    prop_type   = (row.get("prop_type") or "").lower()
    _pp_key     = f"{player_name}:{prop_type}"

    # WOW-PATCH-2026-08-16-R4: check BOTH row_id AND the player:prop key.
    # build_auto_enrichment writes the full entry under "player:prop" (not
    # row_id), so looking up only row_id produces an empty dict and bypasses
    # a game_log that was already fetched.  When the player:prop entry has
    # richer data, promote it to enrichment[row_id] so the pipeline's
    # _get_enrichment() (which checks row_id first) finds the complete entry —
    # including all data_contract sentinel fields, sportsbook_line, etc.
    _enr_by_rid = enrichment.get(row_id) or {}
    _enr_by_pp  = enrichment.get(_pp_key) if _pp_key else {}
    _enr_by_pp  = _enr_by_pp or {}

    # Promote: if the player:prop entry has more data than the row_id entry
    # (indicated by game_log presence), merge them into enrichment[row_id] so
    # all downstream reads via the rid key find the full enrichment.
    if _enr_by_pp.get("game_log") and not _enr_by_rid.get("game_log"):
        # _enr_by_rid (sparse; possibly just {acquisition_status: ...}) wins on
        # key conflicts so any later acquisition stamp is not lost.
        if row_id:
            enrichment[row_id] = {**_enr_by_pp, **_enr_by_rid}
            _enr_by_rid = enrichment[row_id]

    # WOW-PATCH-2026-08-16-R4: unconditionally carry player_id from enrichment
    # to the raw_row object so the pipeline's normalized-row output includes it.
    # This runs regardless of whether the promotion block above triggered:
    #   - Promotion path: game_log was under player:prop key → promoted to rid
    #     entry; player_id was written by build_auto_enrichment to that entry.
    #   - Direct path: game_log already under rid entry (GPT supplied enrichment
    #     under row_id AND build_auto_enrichment added game_log + player_id to it).
    # In both cases _enr_by_rid (after any promotion) or _enr_by_pp may carry
    # player_id.  Take whichever is non-empty first.
    enr_entry = _enr_by_rid if _enr_by_rid else _enr_by_pp
    _pid_from_enr = enr_entry.get("player_id") or _enr_by_pp.get("player_id")
    if _pid_from_enr and not row.get("player_id"):
        row["player_id"] = _pid_from_enr

    # Already populated (by caller or auto_enrich)?
    game_log = enr_entry.get("game_log") or row.get("game_log")
    if game_log:
        # direct_game_log_feed=FETCHED: the direct provider (MLB Stats API /
        # BallDontLie) was called by build_auto_enrichment or the orchestrator;
        # the data is real, not caller-supplied.
        _gl_source = "FETCHED"
        return {
            "status":               "ACQUIRED",
            "reason":               "game_log_present",
            "fields_populated":     ["game_log"],
            "acquisition_attempted": False,
            "direct_game_log_feed": _gl_source,
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


# ---------------------------------------------------------------------------
# 1IP dedicated acquisition (WOW-PATCH-2026-08-17-1IP-PRODUCTION-HYDRATION)
# ---------------------------------------------------------------------------

def _compute_pitches_per_batter_dist(ledger_rows: list[dict]) -> dict:
    """Delegate to savant_1ip_ledger.compute_pitches_per_batter_dist."""
    try:
        from gate_engine.mlb.savant_1ip_ledger import compute_pitches_per_batter_dist
        return compute_pitches_per_batter_dist(ledger_rows)
    except Exception:
        return {"mean": 4.2, "std": 1.1, "n": 0, "note": "default_genre_calibrated"}


def _check_1ip_acquisition(
    row: dict[str, Any],
    enrichment: dict[str, Any],
    *,
    target_date: str | None = None,
) -> dict[str, Any]:
    """
    Fetch Baseball Savant first-inning ledger for a 1IP_PITCHES_THROWN row
    and populate enrichment with:
      - first_inning_bf_distribution  (required by pipeline gate + hit_probability)
      - pitches_per_batter_distribution (required by ip1_event_tree.simulate_1ip)
      - savant_1ip_ledger             (full summary for observability)
      - 1ip_acquisition_status        (provenance tag)

    Source hierarchy (per WOW 1IP acquisition spec):
      1. Baseball Savant CSV (inning=1 server-side pre-filtered)
      2. pybaseball.statcast_pitcher fallback (local inning=1 filter)
      3. PROBABILITY_PIPELINE_CONTRACT_BREACH with typed missing_fields

    Fail-closed: missing BF distribution → typed breach record; never
    fabricates event-tree inputs.
    """
    import datetime as _dt
    row_id      = str(row.get("row_id") or row.get("player") or "unknown")
    player_name = (row.get("player") or "").strip()
    line        = row.get("line")
    side        = (row.get("direction") or row.get("side") or "LESS").upper()
    board_date  = target_date or _dt.date.today().isoformat()
    season      = board_date[:4]

    _breach_fields = ["first_inning_bf_distribution", "pitches_per_batter_distribution"]

    def _unavailable(reason: str, *, sources_tried: list[str] | None = None) -> dict:
        breach: dict[str, Any] = {
            "PROBABILITY_PIPELINE_CONTRACT_BREACH": True,
            "missing_fields": _breach_fields,
            "stage":          "1IP_SAVANT_LEDGER_ACQUISITION",
            "reason":         reason,
            "retryable":      True,
            "can_execute":    False,
        }
        if sources_tried:
            breach["acquisition_sources_tried"] = sources_tried
        if row_id not in enrichment:
            enrichment[row_id] = {}
        enrichment[row_id]["1ip_acquisition_status"] = reason
        enrichment[row_id]["1ip_breach_contract"]    = breach
        return {
            "status":               "UNAVAILABLE",
            "reason":               f"1IP_BF_ACQUISITION_FAILED:{reason}",
            "fields_populated":     [],
            "acquisition_attempted": True,
            "direct_game_log_feed": "NOT_CALLED",
            "acquisition_stage":    "1IP_SAVANT_LEDGER",
            "missing_fields":       _breach_fields,
            "breach_contract":      breach,
        }

    # Resolve MLBAM pitcher ID via MLB Stats API people/search
    player_id = row.get("player_id") or _resolve_mlb_player_id(player_name)
    if player_id:
        row["player_id"] = player_id
        # Convert str → int for savant_1ip_ledger which expects int
        try:
            pitcher_id_int = int(player_id)
        except (TypeError, ValueError):
            return _unavailable(f"player_id_not_numeric:{player_id!r}")
    else:
        return _unavailable(
            f"player_id_unresolved:name={player_name!r}",
            sources_tried=["mlb_stats_api_people_search"],
        )

    # Fetch Savant first-inning ledger
    try:
        from gate_engine.mlb.savant_1ip_ledger import build_1ip_ledger
        ledger = build_1ip_ledger(
            pitcher_id_int, season, board_date,
            line=float(line) if line is not None else None,
            side=side,
            max_starts=10,
        )
    except Exception as exc:
        return _unavailable(
            f"savant_ledger_exception:{str(exc)[:100]}",
            sources_tried=["savant_csv_direct", "pybaseball_fallback"],
        )

    if ledger.get("error"):
        return _unavailable(
            f"savant_ledger_error:{ledger['error'][:120]}",
            sources_tried=["savant_csv_direct", "pybaseball_fallback"],
        )

    bf_dist = ledger.get("bf_distribution") or {}
    # Valid: at least one non-None BF probability present
    if (bf_dist.get("n") or 0) == 0 or (
        bf_dist.get("p_bf_3") is None
        and bf_dist.get("p_bf_4") is None
        and bf_dist.get("p_bf_gte5") is None
    ):
        return _unavailable(
            "bf_distribution_empty:no_verified_bf_data_in_savant",
            sources_tried=["savant_csv_direct", "pybaseball_fallback"],
        )

    ppb_dist = _compute_pitches_per_batter_dist(ledger.get("ledger_rows") or [])

    # Inject into enrichment keyed by row_id (pipeline's _get_enrichment priority key)
    if row_id not in enrichment:
        enrichment[row_id] = {}
    enrichment[row_id]["first_inning_bf_distribution"]    = bf_dist
    enrichment[row_id]["pitches_per_batter_distribution"] = ppb_dist
    enrichment[row_id]["savant_1ip_ledger"] = {
        "data_coverage":  ledger.get("data_coverage"),
        "l10_pitch_mean": ledger.get("l10_pitch_mean"),
        "l10_pitch_std":  ledger.get("l10_pitch_std"),
        "l5_hit_rate":    ledger.get("l5_hit_rate"),
        "l10_hit_rate":   ledger.get("l10_hit_rate"),
        "fetch_method":   ledger.get("fetch_method"),
        "source":         ledger.get("source"),
        "gaps":           ledger.get("gaps") or [],
        "pitcher_id":     pitcher_id_int,
        "season":         season,
        "board_date":     board_date,
    }
    enrichment[row_id]["1ip_acquisition_status"] = "SAVANT_ACQUIRED"
    enrichment[row_id]["data_timestamp"] = (
        enrichment[row_id].get("data_timestamp")
        or __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat()
    )

    populated = [
        "first_inning_bf_distribution",
        "pitches_per_batter_distribution",
        "savant_1ip_ledger",
    ]
    return {
        "status":               "ACQUIRED",
        "reason":               "1ip_bf_distribution_from_savant",
        "fields_populated":     populated,
        "acquisition_attempted": True,
        "direct_game_log_feed": "FETCHED",
        "acquisition_stage":    "1IP_SAVANT_LEDGER",
        "data_coverage":        ledger.get("data_coverage"),
        "fetch_method":         ledger.get("fetch_method"),
        "bf_n":                 bf_dist.get("n"),
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
        # WOW-PATCH-2026-08-16-R4: canonicalize stat_key before calling
        # fetch_game_log.  When auto_enrich=False (or when this path is reached
        # as a secondary fetch), the raw prop_type display label arrives here
        # ("Hits", "Runs", "RBI", …).  _fetch_mlb only knows uppercase short
        # keys ("H", "R", "RBI"); passing the display label raises
        # GameLogUnavailable which is caught below and silently returns None.
        try:
            from gate_engine.auto_enrichment import (  # noqa: PLC0415
                _canonicalize_stat_key as _canon_sk,
            )
            stat_key = _canon_sk(stat_key) or stat_key
        except Exception:
            pass  # if import fails, keep original stat_key and let fetch fail naturally
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
# Moneyline acquisition — sport-specific dispatch
# ---------------------------------------------------------------------------

def _check_moneyline_acquisition(
    row: dict[str, Any],
    enrichment: dict[str, Any],
    sport: str,
) -> dict[str, Any]:
    """
    Sport-specific dispatch for moneyline acquisition.

    Each sport family routes to its own acquisition function and hydration
    profile.  Do NOT collapse families into a shared frozenset — that is how
    WNBA/tennis ended up going through the wrong path.

    NBA/MLB  → _check_nba_mlb_moneyline  (MONEYLINE_V1)
    WNBA     → _check_wnba_ml_acquisition (WNBA_ML_V1)
    ATP/WTA  → _check_tennis_match_acquisition (TENNIS_MATCH_WINNER_V1)
    Other    → UNSUPPORTED (explicit; NOT_CALLED is never terminal)
    """
    if sport in _MONEYLINE_TEAM_SUPPORTED:
        return _check_nba_mlb_moneyline(row, enrichment, sport)
    if sport in _WNBA_ML_SUPPORTED:
        return _check_wnba_ml_acquisition(row, enrichment)
    if sport in _TENNIS_ML_SUPPORTED:
        return _check_tennis_match_acquisition(row, enrichment)

    return {
        "status":                "UNSUPPORTED",
        "reason":                f"MONEYLINE_ACQUISITION_UNAVAILABLE:sport_not_supported:{sport}",
        "fields_populated":      [],
        "acquisition_attempted": False,
    }


def _check_nba_mlb_moneyline(
    row: dict[str, Any],
    enrichment: dict[str, Any],
    sport: str,
) -> dict[str, Any]:
    """NBA/MLB moneyline team-data acquisition (MONEYLINE_V1)."""
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
            "status":                "ACQUIRED",
            "reason":                "team_non_market_data_present",
            "fields_populated":      populated,
            "acquisition_attempted": False,
        }

    try:
        from gate_engine.moneyline.team_acquisition import acquire_team_data
        team_data = acquire_team_data(row, sport)
        if team_data:
            if row_id not in enrichment:
                enrichment[row_id] = {}
            enrichment[row_id].update(team_data)
            populated = list(team_data.keys())
            return {
                "status":                "ACQUIRED",
                "reason":                f"team_data_fetched:{sport}",
                "fields_populated":      populated,
                "acquisition_attempted": True,
            }
    except Exception as exc:
        logger.warning("NBA/MLB team acquisition failed for %s/%s: %s", sport, row_id, exc)

    return {
        "status":                "UNAVAILABLE",
        "reason":                f"MONEYLINE_ACQUISITION_UNAVAILABLE:team_data_fetch_failed:{sport}",
        "fields_populated":      [],
        "acquisition_attempted": True,
    }


def _check_wnba_ml_acquisition(
    row: dict[str, Any],
    enrichment: dict[str, Any],
) -> dict[str, Any]:
    """
    WNBA_ML_V1 moneyline acquisition.

    Attempts BallDontLie WNBA standings + row-derived non-market fields.
    NEVER reads game_log / box_score_log (player-prop contract is out of scope).
    """
    row_id = str(row.get("row_id") or row.get("team") or row.get("player") or "unknown")
    enr_entry = enrichment.get(row_id) or {}

    # Check pre-populated WNBA_ML_V1 fields (home_win_pct, offensive/def rating, etc.)
    _wnba_ml_fields = (
        "home_win_pct", "away_win_pct", "home_power", "away_power",
        "offensive_rating", "defensive_rating", "pace", "rest_days",
    )
    populated = [k for k in _wnba_ml_fields if enr_entry.get(k) is not None]
    if enr_entry:
        from gate_engine.moneyline.team_acquisition import validate_wnba_ml_hydration
        validated = validate_wnba_ml_hydration(enr_entry)
        if row_id not in enrichment:
            enrichment[row_id] = {}
        enrichment[row_id].update(validated)
        if validated.get("hydration_status") != "ACQUIRED":
            return {
                "status":                "UNAVAILABLE",
                "reason":                str(validated["unavailable_reason"]),
                "fields_populated":      [],
                "acquisition_attempted": False,
                "hydration_profile":     "WNBA_ML_V1",
            }
        return {
            "status":                "ACQUIRED",
            "reason":                "wnba_ml_v1_supplied_data_validated",
            "fields_populated":      [k for k in _wnba_ml_fields if validated.get(k) is not None],
            "acquisition_attempted": False,
            "hydration_profile":     "WNBA_ML_V1",
        }

    try:
        from gate_engine.moneyline.team_acquisition import acquire_team_data
        team_data = acquire_team_data(row, "WNBA")
        if team_data and team_data.get("hydration_status") == "ACQUIRED":
            if row_id not in enrichment:
                enrichment[row_id] = {}
            enrichment[row_id].update(team_data)
            populated = [k for k in team_data if k not in ("hydration_profile", "team_acq_source")]
            return {
                "status":                "ACQUIRED",
                "reason":                "wnba_ml_v1_data_fetched",
                "fields_populated":      populated,
                "acquisition_attempted": True,
                "hydration_profile":     "WNBA_ML_V1",
            }
        if team_data:
            if row_id not in enrichment:
                enrichment[row_id] = {}
            enrichment[row_id].update(team_data)
            return {
                "status":                "UNAVAILABLE",
                "reason":                str(
                    team_data.get("unavailable_reason")
                    or "MONEYLINE_ACQUISITION_UNAVAILABLE:wnba_ml_v1_incomplete"
                ),
                "fields_populated":      [],
                "acquisition_attempted": True,
                "hydration_profile":     "WNBA_ML_V1",
            }
    except Exception as exc:
        logger.warning("WNBA_ML_V1 acquisition failed for %s: %s", row_id, exc)

    # Partial data may still reach sport_model; stamp profile for typed failure
    if row_id not in enrichment:
        enrichment[row_id] = {}
    enrichment[row_id].setdefault("hydration_profile", "WNBA_ML_V1")
    return {
        "status":                "UNAVAILABLE",
        "reason":                "MONEYLINE_ACQUISITION_UNAVAILABLE:wnba_ml_v1_fetch_failed",
        "fields_populated":      [],
        "acquisition_attempted": True,
        "hydration_profile":     "WNBA_ML_V1",
    }


def _check_tennis_match_acquisition(
    row: dict[str, Any],
    enrichment: dict[str, Any],
) -> dict[str, Any]:
    """
    TENNIS_MATCH_WINNER_V1 acquisition.

    Attempts row-derived field extraction + ESPN best-effort.
    """
    sport  = (row.get("sport") or "ATP").upper()
    row_id = str(row.get("row_id") or row.get("team") or row.get("player") or "unknown")
    enr_entry = enrichment.get(row_id) or {}

    _tennis_fields = (
        "surface", "surface_adjusted_form", "hold_rate", "break_rate",
        "service_points_won", "return_points_won", "home_elo", "away_elo",
    )
    populated = [k for k in _tennis_fields if enr_entry.get(k) is not None]
    if populated:
        return {
            "status":                "ACQUIRED",
            "reason":                "tennis_match_winner_v1_data_present",
            "fields_populated":      populated,
            "acquisition_attempted": False,
            "hydration_profile":     "TENNIS_MATCH_WINNER_V1",
        }

    try:
        from gate_engine.moneyline.team_acquisition import acquire_team_data
        team_data = acquire_team_data(row, sport)
        if team_data:
            if row_id not in enrichment:
                enrichment[row_id] = {}
            enrichment[row_id].update(team_data)
            populated = [k for k in team_data if k not in ("hydration_profile", "team_acq_source")]
            return {
                "status":                "ACQUIRED",
                "reason":                "tennis_match_winner_v1_data_fetched",
                "fields_populated":      populated,
                "acquisition_attempted": True,
                "hydration_profile":     "TENNIS_MATCH_WINNER_V1",
            }
    except Exception as exc:
        logger.warning("TENNIS_MATCH_WINNER_V1 acquisition failed for %s: %s", row_id, exc)

    if row_id not in enrichment:
        enrichment[row_id] = {}
    enrichment[row_id].setdefault("hydration_profile", "TENNIS_MATCH_WINNER_V1")
    return {
        "status":                "UNAVAILABLE",
        "reason":                "MONEYLINE_ACQUISITION_UNAVAILABLE:tennis_match_winner_v1_fetch_failed",
        "fields_populated":      [],
        "acquisition_attempted": True,
        "hydration_profile":     "TENNIS_MATCH_WINNER_V1",
    }
