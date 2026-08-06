"""
gate_engine/wnba/acquisition_packet.py
WOW-PATCH-2026-08-06-WNBA-EVIDENCE-ACQUISITION-STRUCTURAL

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
# Packet status vocabulary
# ---------------------------------------------------------------------------

class PacketStatus:
    PACKET_COMPLETE            = "PACKET_COMPLETE"
    PACKET_RECONSTRUCTED       = "PACKET_RECONSTRUCTED"
    PACKET_INCOMPLETE_REJECTED = "PACKET_INCOMPLETE_REJECTED"


# ---------------------------------------------------------------------------
# Per-field acquisition terminal status vocabulary
# Replaces NOT_CALLED as a terminal status — NOT_CALLED is never a final state.
# ---------------------------------------------------------------------------

class AcquisitionFieldStatus:
    PRIMARY_RETRIEVED                = "PRIMARY_RETRIEVED"
    FALLBACK_RETRIEVED               = "FALLBACK_RETRIEVED"
    MULTI_SOURCE_RECONSTRUCTED       = "MULTI_SOURCE_RECONSTRUCTED"
    PROXY_ONLY                       = "PROXY_ONLY"
    SOURCE_CONFLICT                  = "SOURCE_CONFLICT"
    DATA_UNOBTAINABLE_AFTER_EXHAUSTION = "DATA_UNOBTAINABLE_AFTER_EXHAUSTION"
    # Intermediate (never final) — kept for in-flight tracking only
    _NOT_YET_ATTEMPTED               = "_NOT_YET_ATTEMPTED"


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
    source      = (claim.get("source") or "").strip()
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
        "source":          source,
        "retrieved_at":    retrieved_at,
        "source_grade":    claim.get("source_grade", SourceGrade.C),
        "freshness_age":   freshness_age,
        "conflict_status": claim.get("conflict_status", "NONE"),
        "acquisition_method": claim.get("acquisition_method", AcquisitionMethod.PRIMARY_API),
    }
    return True, normalized


# ---------------------------------------------------------------------------
# Raw ledger reconstruction (spec item 7)
# Data assembly ONLY — no hit-rate probability, no new qualification labels.
# ---------------------------------------------------------------------------

def _extract_float(game: dict[str, Any], keys: list[str]) -> float | None:
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


def reconstruct_raw_ledger_rows(box_score_log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Convert raw per-game box-score dicts into structured ledger rows.

    Each output row contains exactly the fields specified in
    WOW-PATCH-2026-08-06-WNBA-EVIDENCE-ACQUISITION-STRUCTURAL §7:
      date, opponent, starter, minutes, points, rebounds, assists,
      pra (=points+rebounds+assists), field_goal_attempts,
      three_point_attempts, free_throw_attempts, team_result, margin,
      fouls (where available — null otherwise).

    This is raw data assembly.  No statistics, probability, or
    calibration are computed here.
    """
    ledger_rows: list[dict[str, Any]] = []

    for game in box_score_log:
        if not isinstance(game, dict):
            continue

        pts = _extract_float(game, ["PTS", "pts", "points", "Points", "Pts"])
        reb = _extract_float(game, ["REB", "reb", "rebounds", "TRB", "Reb", "TREB"])
        ast = _extract_float(game, ["AST", "ast", "assists", "Ast", "Assists"])
        mins = _extract_float(game, ["MIN", "min", "minutes", "MP", "min_played", "Minutes"])
        fga = _extract_float(game, ["FGA", "fga", "field_goal_attempts", "FGAttempts", "FG_A"])
        tpa = _extract_float(game, [
            "3PA", "3pa", "three_point_attempts", "ThreePtAttempts", "3P_A", "3PT_A", "TP_A",
        ])
        fta = _extract_float(game, ["FTA", "fta", "free_throw_attempts", "FTAttempts", "FT_A"])
        fouls = _extract_float(game, ["PF", "pf", "fouls", "personal_fouls", "Fouls"])

        # PRA = points + rebounds + assists; null if any component is null
        pra: float | None = None
        if pts is not None and reb is not None and ast is not None:
            pra = pts + reb + ast

        # Starter flag — accept common truthy representations
        starter_raw = game.get("starter") or game.get("GS") or game.get("started")
        starter: bool | None = None
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
            "free_throw_attempts":   fta,
            "team_result":           team_result,
            "margin":                margin_raw,
            "fouls":                 fouls,
        }
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

def _build_matchup_section(enr: dict[str, Any]) -> dict[str, Any]:
    """
    Build the matchup sub-section.  Values may be null/PROXY_ONLY — never
    fabricated.  The calling module marks any null field as needing fallback.
    """
    matchup_raw = enr.get("matchup") or {}

    return {
        "pace":               matchup_raw.get("pace"),
        "opponent_defense":   matchup_raw.get("opponent_defense") or matchup_raw.get("opp_defensive_rating"),
        "position_defense":   matchup_raw.get("position_defense") or matchup_raw.get("positional_defense"),
        "rebound_environment": matchup_raw.get("rebound_environment") or matchup_raw.get("rebound_rate_allowed"),
        "assist_environment":  matchup_raw.get("assist_environment") or matchup_raw.get("assist_rate_allowed"),
        "_proxy_fields":      matchup_raw.get("_proxy_fields") or [],
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

    # Box score log — raw per-game dicts from enrichment
    box_score_log_raw: list[dict[str, Any]] = enr.get("box_score_log") or []

    # Raw ledger reconstruction (data assembly only)
    raw_ledger_rows = reconstruct_raw_ledger_rows(box_score_log_raw)
    l5_ledger, l10_ledger, season_ledger = _split_ledger(raw_ledger_rows)

    # Matchup section
    matchup = _build_matchup_section(enr)

    # Source audit — collect any explicitly passed source metadata
    source_audit_raw = enr.get("source_audit") or {}

    packet: dict[str, Any] = {
        "candidate_id":  candidate_id,
        "player":        player,
        "team":          team,
        "opponent":      opponent,
        "event_id":      event_id,
        "market":        market,
        "line":          line,
        "side":          side,
        "as_of":         as_of,
        "event_status":  event_status,
        "role_status":   role_status,
        "box_score_log": box_score_log_raw,
        "l5_ledger":     l5_ledger,
        "l10_ledger":    l10_ledger,
        "season_ledger": season_ledger,
        "matchup":       matchup,
        "source_audit":  source_audit_raw,
        # Set by orchestrator after fallback routing:
        "packet_status":      None,
        "field_status_map":   {},   # field → AcquisitionFieldStatus
        "acquisition_audit":  None,
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
