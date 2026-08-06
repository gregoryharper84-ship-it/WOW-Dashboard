"""
gate_engine/wnba/acquisition_packet.py
WOW-PATCH-2026-08-06-WNBA-EVIDENCE-ACQUISITION-STRUCTURAL
WOW-PATCH-2026-08-06-WNBA-EXTERNAL-EVIDENCE-ADAPTERS
WOW-PATCH-2026-08-06-WNBA-ACQUISITION-CONTRACT-REPAIR

WNBA Opportunity Packet schema, construction, source normalization,
and raw ledger reconstruction.

This module is plumbing and observability ONLY.  It does NOT compute
any probability estimate, calibration score, hit rate, or qualification
label.  All statistical thresholds and gate decisions remain in their
existing modules unchanged.

can_execute=False is unconditional.
"""
from __future__ import annotations

import datetime
from typing import Any

can_execute = False

# ---------------------------------------------------------------------------
# Packet status vocabulary (4 values per WOW-PATCH-2026-08-06-WNBA-EXTERNAL-EVIDENCE-ADAPTERS)
# ---------------------------------------------------------------------------

class PacketStatus:
    PACKET_COMPLETE              = "PACKET_COMPLETE"
    # All critical + qualification fields satisfied (via primary or successful fallback)
    PACKET_RECONSTRUCTED_COMPLETE = "PACKET_RECONSTRUCTED_COMPLETE"
    # Critical fields satisfied; ≥1 qualification-blocking field unresolved
    PACKET_PARTIAL_HOLD          = "PACKET_PARTIAL_HOLD"
    # Any critical-blocking field unresolved after full exhaustion → row blocked
    PACKET_INCOMPLETE_REJECTED   = "PACKET_INCOMPLETE_REJECTED"


# ---------------------------------------------------------------------------
# Per-field acquisition terminal status vocabulary
# Replaces NOT_CALLED as a terminal status — NOT_CALLED is never a final state.
# ---------------------------------------------------------------------------

class AcquisitionFieldStatus:
    PRIMARY_RETRIEVED                  = "PRIMARY_RETRIEVED"
    FALLBACK_RETRIEVED                 = "FALLBACK_RETRIEVED"
    MULTI_SOURCE_RECONSTRUCTED         = "MULTI_SOURCE_RECONSTRUCTED"
    PROXY_ONLY                         = "PROXY_ONLY"
    SOURCE_CONFLICT                    = "SOURCE_CONFLICT"
    DATA_UNOBTAINABLE_AFTER_EXHAUSTION = "DATA_UNOBTAINABLE_AFTER_EXHAUSTION"
    # Intermediate (never final) — kept for in-flight tracking only
    _NOT_YET_ATTEMPTED                 = "_NOT_YET_ATTEMPTED"


# ---------------------------------------------------------------------------
# Source grading
# ---------------------------------------------------------------------------

class SourceGrade:
    A = "A"   # Official league / official box score / verified primary API
    B = "B"   # Trusted statistical database / verified beat reporter
    C = "C"   # Aggregator / reconstruction estimate / proxy


class AcquisitionMethod:
    PRIMARY_API    = "PRIMARY_API"
    WEB_FALLBACK   = "WEB_FALLBACK"
    RECONSTRUCTED  = "RECONSTRUCTED"
    PROXY_ESTIMATE = "PROXY_ESTIMATE"
    NOT_ATTEMPTED  = "NOT_ATTEMPTED"  # structural stub: route configured but not executed


# ---------------------------------------------------------------------------
# Source claim normalization
# ---------------------------------------------------------------------------

def normalize_source_claim(claim: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """
    Validate and normalize a source claim.

    A source claim MUST have both ``source`` (non-empty string) and
    ``retrieved_at`` (non-empty string parseable as a timestamp).
    Without both, the claim is rejected back into missing_fields.

    Returns (valid: bool, normalized_claim: dict).
    The normalized claim adds ``freshness_age`` (seconds since retrieved_at)
    and ``conflict_status`` defaulting to "NONE".
    """
    source       = (claim.get("source") or "").strip()
    retrieved_at = (claim.get("retrieved_at") or "").strip()

    if not source or not retrieved_at:
        return False, {**claim, "_validation_error": "missing source or retrieved_at"}

    # Attempt to parse retrieved_at for freshness computation
    freshness_age: float | None = None
    try:
        if retrieved_at.endswith("Z"):
            retrieved_at_clean = retrieved_at[:-1] + "+00:00"
        else:
            retrieved_at_clean = retrieved_at
        dt = datetime.datetime.fromisoformat(retrieved_at_clean)
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        freshness_age = (now_utc - dt).total_seconds()
    except (ValueError, TypeError):
        pass  # non-fatal; freshness_age stays None

    normalized = {
        **claim,
        "source":             source,
        "retrieved_at":       retrieved_at,
        "source_grade":       claim.get("source_grade", SourceGrade.C),
        "freshness_age":      freshness_age,
        "conflict_status":    claim.get("conflict_status", "NONE"),
        "acquisition_method": claim.get("acquisition_method", AcquisitionMethod.PRIMARY_API),
    }
    return True, normalized


# ---------------------------------------------------------------------------
# Raw ledger reconstruction (spec item 7)
# Data assembly ONLY — no hit-rate probability, no new qualification labels.
# ---------------------------------------------------------------------------

# Canonical stat registry for single-stat row normalization (BUG-002 fix).
# Maps Odds API market key (lowercase) → the primary ledger field.
# Only ACTIVE WNBA prop markets are listed.  Markets absent from this map
# must fail visibly with STAT_MAPPING_UNRESOLVED — never silently guess.
# Do NOT infer the market from the numeric value of "stat".
_MARKET_TO_STAT_KEY: dict[str, str] = {
    "player_points":                  "points",
    "player_rebounds":                "rebounds",
    "player_assists":                 "assists",
    # Composite — single "stat" value allowed only when source query IS for PRA
    "player_points_rebounds_assists": "pra",
    "player_pra":                     "pra",
    "player_threes":                  "three_pointers_made",
    "player_steals":                  "steals",
    "player_blocks":                  "blocks",
    # Short-form aliases used inside the engine
    "points":    "points",
    "rebounds":  "rebounds",
    "assists":   "assists",
    "pra":       "pra",
    "threes":    "three_pointers_made",
    "steals":    "steals",
    "blocks":    "blocks",
}


def _extract_float(game: dict[str, Any], keys: list[str]) -> "float | None":
    for k in keys:
        v = game.get(k)
        if v is None:
            continue
        try:
            f = float(v)
            return f if f >= 0 else None
        except (TypeError, ValueError):
            pass
    return None


def reconstruct_raw_ledger_rows(
    box_score_log: list[dict[str, Any]],
    market_type: "str | None" = None,
) -> list[dict[str, Any]]:
    """
    Convert raw per-game box-score dicts into structured ledger rows.

    Each output row contains the fields from
    WOW-PATCH-2026-08-06-WNBA-EVIDENCE-ACQUISITION-STRUCTURAL §7 plus
    extension fields added by WOW-PATCH-2026-08-06-WNBA-ACQUISITION-CONTRACT-REPAIR:
      date, opponent, starter, minutes, points, rebounds, assists,
      pra, field_goal_attempts, three_point_attempts, free_throw_attempts,
      three_pointers_made, steals, blocks,
      team_result, margin, fouls,
      line, hit,                          ← preserved from player_logs.py rows
      raw_stat_value, canonical_stat_type, source_market_type,
                                          ← single-stat normalization audit
      stat_mapping_unresolved             ← True when mapping fails visibly

    Single-stat row normalization (BUG-002 fix):
      When a row contains only the "stat" key (services/player_logs.py
      provider-neutral shape: {date, opponent, stat, line, hit}) and all
      category-specific keys are absent, the stat value is mapped to the
      correct ledger field via _MARKET_TO_STAT_KEY[market_type].

      Rules:
        1. Never infer the stat category from the numeric value itself.
        2. For composite PRA: set pra=stat only when market_type IS a PRA
           market.  Component reconstruction (pts+reb+ast) is used when all
           three raw components are present for the same event.
        3. Unsupported / ambiguous market types set stat_mapping_unresolved=True
           and leave all stat fields null — never silently guess.

    This is raw data assembly.  No statistics, probability, or calibration
    are computed here.
    """
    mt_lower = (market_type or "").lower().strip()
    ledger_rows: list[dict[str, Any]] = []

    for game in box_score_log:
        if not isinstance(game, dict):
            continue

        pts  = _extract_float(game, ["PTS", "pts", "points", "Points", "Pts"])
        reb  = _extract_float(game, ["REB", "reb", "rebounds", "TRB", "Reb", "TREB"])
        ast  = _extract_float(game, ["AST", "ast", "assists", "Ast", "Assists"])
        mins = _extract_float(game, ["MIN", "min", "minutes", "MP", "min_played", "Minutes"])
        fga  = _extract_float(game, ["FGA", "fga", "field_goal_attempts", "FGAttempts", "FG_A"])
        tpa  = _extract_float(game, [
            "3PA", "3pa", "three_point_attempts", "ThreePtAttempts", "3P_A", "3PT_A", "TP_A",
        ])
        fta   = _extract_float(game, ["FTA", "fta", "free_throw_attempts", "FTAttempts", "FT_A"])
        fouls = _extract_float(game, ["PF", "pf", "fouls", "personal_fouls", "Fouls"])
        tpm   = _extract_float(game, ["3PM", "3pm", "three_pointers_made", "ThreePtMade", "3P_M"])
        stl   = _extract_float(game, ["STL", "stl", "steals", "Steals"])
        blk   = _extract_float(game, ["BLK", "blk", "blocks", "Blocks"])

        # Single-stat normalization (BUG-002): map game["stat"] → correct field
        # when all category-specific keys are absent (player_logs.py format).
        raw_stat_val      = game.get("stat")
        canonical_st_type: "str | None" = None
        stat_unresolved   = False

        all_cat_null = (
            pts is None and reb is None and ast is None
            and tpm is None and stl is None and blk is None
        )
        if all_cat_null and raw_stat_val is not None:
            try:
                raw_stat_float = float(raw_stat_val)
            except (TypeError, ValueError):
                raw_stat_float = None

            if raw_stat_float is not None and mt_lower:
                canonical = _MARKET_TO_STAT_KEY.get(mt_lower)
                if canonical is None:
                    # Unsupported market — fail visibly, never guess
                    stat_unresolved   = True
                    canonical_st_type = "UNRESOLVED"
                else:
                    canonical_st_type = canonical
                    if canonical == "points":
                        pts = raw_stat_float
                    elif canonical == "rebounds":
                        reb = raw_stat_float
                    elif canonical == "assists":
                        ast = raw_stat_float
                    elif canonical == "pra":
                        # Source query was for PRA — direct assignment allowed
                        pass  # pra set below from components or direct
                    elif canonical == "three_pointers_made":
                        tpm = raw_stat_float
                    elif canonical == "steals":
                        stl = raw_stat_float
                    elif canonical == "blocks":
                        blk = raw_stat_float
            elif raw_stat_val is not None and not mt_lower:
                # stat key present but no market_type context → unresolved
                stat_unresolved   = True
                canonical_st_type = "UNRESOLVED"

        # PRA: prefer component sum; fall back to direct pra-market single stat
        pra: "float | None" = None
        if pts is not None and reb is not None and ast is not None:
            pra = pts + reb + ast
        elif (mt_lower in ("pra", "player_pra", "player_points_rebounds_assists")
              and raw_stat_val is not None and all_cat_null):
            try:
                pra = float(raw_stat_val)
            except (TypeError, ValueError):
                pass

        # Starter flag
        starter_raw = game.get("starter") or game.get("GS") or game.get("started")
        starter: "bool | None" = None
        if starter_raw is not None:
            if isinstance(starter_raw, bool):
                starter = starter_raw
            elif str(starter_raw).strip().lower() in ("1", "true", "yes", "y", "start"):
                starter = True
            elif str(starter_raw).strip().lower() in ("0", "false", "no", "n", "bench", ""):
                starter = False

        team_result = (
            str(game.get("result") or game.get("team_result") or game.get("W_L") or "").upper()
            or None
        )
        margin_raw = _extract_float(game, ["margin", "MARGIN", "point_diff", "score_diff"])

        row: dict[str, Any] = {
            "date":                  game.get("date") or game.get("GAME_DATE") or game.get("game_date"),
            "opponent":              game.get("opponent") or game.get("OPP") or game.get("opp"),
            "starter":               starter,
            "minutes":               mins,
            "points":                pts,
            "rebounds":              reb,
            "assists":               ast,
            "pra":                   pra,
            "field_goal_attempts":   fga,
            "three_point_attempts":  tpa,
            "three_pointers_made":   tpm,
            "free_throw_attempts":   fta,
            "steals":                stl,
            "blocks":                blk,
            "team_result":           team_result,
            "margin":                margin_raw,
            "fouls":                 fouls,
            # Preserved from player_logs.py provider-neutral rows
            "line":                  game.get("line"),
            "hit":                   game.get("hit"),
            # Single-stat normalization audit fields
            "raw_stat_value":        raw_stat_val if all_cat_null else None,
            "canonical_stat_type":   canonical_st_type,
            "source_market_type":    market_type if all_cat_null and raw_stat_val is not None else None,
        }
        if stat_unresolved:
            row["stat_mapping_unresolved"] = True
            row["stat_mapping_error"] = (
                f"STAT_MAPPING_UNRESOLVED: "
                f"market_type={market_type!r} has no canonical mapping in _MARKET_TO_STAT_KEY"
                if mt_lower else
                "STAT_MAPPING_UNRESOLVED: 'stat' key present but no market_type context provided"
            )
        ledger_rows.append(row)

    return ledger_rows


def _split_ledger(raw_rows: list[dict[str, Any]]) -> tuple[
    list[dict[str, Any]],   # l5
    list[dict[str, Any]],   # l10
    list[dict[str, Any]],   # season
]:
    """
    Split raw ledger into l5 / l10 / season sub-ledgers.
    Rows are assumed to be in reverse-chronological order (most recent first);
    if not, we use them as-is.

    l5  = first 5 rows
    l10 = first 10 rows
    season = all rows
    """
    l5  = raw_rows[:5]
    l10 = raw_rows[:10]
    return l5, l10, raw_rows


# ---------------------------------------------------------------------------
# Role status section builder
# ---------------------------------------------------------------------------

def _build_role_status_section(row: dict[str, Any], enr: dict[str, Any]) -> dict[str, Any]:
    """
    Build the role_status sub-section of the packet from whatever is
    available on the row (set by status_role gate) and the enrichment dict.
    """
    # status_role gate stamps row["role_status"]
    role_raw = row.get("role_status") or {}

    # enrichment may carry additional role metadata
    status_payload = enr.get("status_payload") or {}

    active_status      = role_raw.get("active_status") or status_payload.get("status")
    expected_start     = role_raw.get("expected_start") or status_payload.get("expected_start")
    projected_minutes  = role_raw.get("projected_minutes") or status_payload.get("projected_minutes")
    minutes_low        = role_raw.get("minutes_low")  or status_payload.get("minutes_low")
    minutes_high       = role_raw.get("minutes_high") or status_payload.get("minutes_high")
    usage_role         = role_raw.get("usage_role")   or status_payload.get("usage_role")
    role_timestamp     = (
        role_raw.get("role_timestamp")
        or enr.get("role_timestamp")
        or status_payload.get("confirmed_at")
        or status_payload.get("role_timestamp")
    )

    # Sources: collect any source claims embedded in the status payload
    sources: list[dict[str, Any]] = []
    raw_sources = status_payload.get("sources") or role_raw.get("sources") or []
    if isinstance(raw_sources, list):
        for s in raw_sources:
            if isinstance(s, dict):
                valid, norm = normalize_source_claim(s)
                if valid:
                    sources.append(norm)

    return {
        "active_status":     active_status,
        "expected_start":    expected_start,
        "projected_minutes": projected_minutes,
        "minutes_low":       minutes_low,
        "minutes_high":      minutes_high,
        "usage_role":        usage_role,
        "role_timestamp":    role_timestamp,
        "sources":           sources,
    }


# ---------------------------------------------------------------------------
# Matchup section builder
# ---------------------------------------------------------------------------

def _build_matchup_section(enr: dict[str, Any]) -> "dict[str, Any] | None":
    """
    Build the matchup sub-section.

    Returns ``None`` when no substantive matchup data is present in the
    enrichment dict.  Returning None (rather than a dict full of None values)
    ensures ``detect_missing`` correctly flags the field as absent, which lets
    the fallback router assign PROXY_ONLY status instead of the false-positive
    PRIMARY_RETRIEVED label that a non-empty all-None dict would produce.
    """
    matchup_raw         = enr.get("matchup") or {}
    pace                = matchup_raw.get("pace")
    opponent_defense    = (matchup_raw.get("opponent_defense")
                           or matchup_raw.get("opp_defensive_rating"))
    position_defense    = (matchup_raw.get("position_defense")
                           or matchup_raw.get("positional_defense"))
    rebound_environment = (matchup_raw.get("rebound_environment")
                           or matchup_raw.get("rebound_rate_allowed"))
    assist_environment  = (matchup_raw.get("assist_environment")
                           or matchup_raw.get("assist_rate_allowed"))

    # If every substantive field is None there is nothing actionable here.
    # Return None so that detect_missing flags the field and the fallback router
    # can emit the correct PROXY_ONLY status rather than PRIMARY_RETRIEVED.
    if all(v is None for v in [pace, opponent_defense, position_defense,
                                rebound_environment, assist_environment]):
        return None

    return {
        "pace":                pace,
        "opponent_defense":    opponent_defense,
        "position_defense":    position_defense,
        "rebound_environment": rebound_environment,
        "assist_environment":  assist_environment,
        "_proxy_fields":       matchup_raw.get("_proxy_fields") or [],
    }


# ---------------------------------------------------------------------------
# Main packet builder
# ---------------------------------------------------------------------------

def build_packet(
    row: dict[str, Any],
    enrichment: dict[str, Any] | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """
    Build a WNBAOpportunityPacket from the current row and enrichment state.

    This snapshot is taken AFTER the status_role gate has run, so
    row["role_status"] is populated.

    packet_status is NOT set here — it is determined by validate_packet()
    after the missing-field detector and fallback router have run.

    Returns a packet dict with all top-level sections.
    """
    enr = enrichment or {}

    # Candidate identity
    candidate_id = row.get("row_id") or row.get("candidate_id") or ""
    player       = row.get("player") or row.get("team") or ""
    team         = row.get("team") or ""
    opponent     = enr.get("opponent") or row.get("opponent") or ""
    event_id     = (
        row.get("event_id")
        or enr.get("event_id")
        or row.get("game_id")
        or ""
    )
    market  = row.get("prop_type") or row.get("market") or ""
    line    = row.get("line")
    side    = row.get("direction") or row.get("side") or ""

    if as_of is None:
        as_of = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Event status — from enrichment or fallback
    event_status = enr.get("event_status") or enr.get("game_status") or row.get("event_status")

    # Role status
    role_status = _build_role_status_section(row, enr)

    # Box score log — raw per-game dicts from enrichment.
    # BUG-001 fix: accept both keys with strict precedence so that the scan
    # flow's "game_log" alias is consumed here rather than silently dropped.
    #   Precedence: (1) box_score_log if present and non-empty
    #               (2) game_log if present and non-empty
    #               (3) empty list
    # The canonical field is always packet["box_score_log"] — one representation.
    _bsl_primary = enr.get("box_score_log")
    _bsl_alt     = enr.get("game_log")
    if _bsl_primary and isinstance(_bsl_primary, list):
        box_score_log_raw: list[dict[str, Any]] = list(_bsl_primary)
        _bsl_source_key = "box_score_log"
    elif _bsl_alt and isinstance(_bsl_alt, list):
        box_score_log_raw = list(_bsl_alt)
        _bsl_source_key   = "game_log"
    else:
        box_score_log_raw = []
        _bsl_source_key   = "absent"

    # Raw ledger reconstruction (data assembly only).
    # BUG-002 fix: pass market_type so the reconstructor can map single-stat
    # rows (player_logs.py shape: {stat, line, hit, ...}) to the correct field.
    market_type_ctx = market or None
    raw_ledger_rows = reconstruct_raw_ledger_rows(box_score_log_raw, market_type=market_type_ctx)
    l5_ledger, l10_ledger, season_ledger = _split_ledger(raw_ledger_rows)

    # Audit fields for the key-alias resolution
    _bsl_audit: dict[str, Any] = {
        "source_input_key":              _bsl_source_key,
        "source_row_count":              len(box_score_log_raw),
        "normalized_box_score_row_count": len(box_score_log_raw),
        "l5_row_count":                  len(l5_ledger),
        "l10_row_count":                 len(l10_ledger),
        "market_type_used_for_mapping":  market_type_ctx,
    }

    # Matchup section
    matchup = _build_matchup_section(enr)

    # Market comparison — from enrichment (filled by market_comparison adapter)
    market_comparison = enr.get("market_comparison") or None

    # News contradiction check — from enrichment (filled by news_contradiction adapter)
    news_contradiction_check = enr.get("news_contradiction_check") or None

    # Source audit — collect any explicitly passed source metadata
    source_audit_raw = enr.get("source_audit") or {}

    packet: dict[str, Any] = {
        "candidate_id":           candidate_id,
        "player":                 player,
        "team":                   team,
        "opponent":               opponent,
        "event_id":               event_id,
        "market":                 market,
        "line":                   line,
        "side":                   side,
        "as_of":                  as_of,
        "event_status":           event_status,
        "role_status":            role_status,
        "box_score_log":          box_score_log_raw,
        "l5_ledger":              l5_ledger,
        "l10_ledger":             l10_ledger,
        "season_ledger":          season_ledger,
        "box_score_audit":        _bsl_audit,
        "matchup":                matchup,
        "market_comparison":      market_comparison,
        "news_contradiction_check": news_contradiction_check,
        "source_audit":           source_audit_raw,
        # Set by orchestrator after fallback routing:
        "packet_status":          None,
        "field_status_map":       {},   # field → AcquisitionFieldStatus
        "acquisition_audit":      None,
    }

    return packet


# ---------------------------------------------------------------------------
# Source claim validator (used by pipeline and tests)
# ---------------------------------------------------------------------------

def validate_role_source_claims(role_status: dict[str, Any]) -> list[str]:
    """
    Return a list of source claim validation errors in the role_status section.

    A source claim without both ``source`` and ``retrieved_at`` is invalid
    and must be treated as missing per spec §4.
    """
    errors: list[str] = []
    sources = role_status.get("sources") or []
    for i, s in enumerate(sources):
        if not isinstance(s, dict):
            errors.append(f"sources[{i}]: not a dict")
            continue
        if not (s.get("source") or "").strip():
            errors.append(f"sources[{i}]: missing 'source'")
        if not (s.get("retrieved_at") or "").strip():
            errors.append(f"sources[{i}]: missing 'retrieved_at'")
    return errors
