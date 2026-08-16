"""
gate_engine/universal_agent/lanes/mlb_props/field_map.py
WOW-PATCH-2026-08-16-UNIVERSAL-AGENT-CORE-V1-B5

Pure deterministic field extraction for MLB props evidence rows.

All functions accept the combined evidence dict ({**enrichment, **row},
row takes precedence) as read-only and return structured dicts / scalars.
Missing fields → "MISSING" / "UNKNOWN" sentinels. Never fabricates.

Key MLB-specific logic
─────────────────────
ip_to_outs(ip_val):
    Converts baseball innings-pitched notation to whole outs.
    4.2 IP = 4 full innings + 2 outs = 14 outs (NOT 4.2 * 3 = 12.6).
    Formula: whole = int(ip_val); partial_outs = round(ip_val * 10) % 10
             total_outs = whole * 3 + partial_outs
    The round() + modulo approach is used to avoid floating-point drift
    (e.g. 4.2 % 1 may produce 0.19999... instead of 0.2 in IEEE 754).

can_execute = False
"""
from __future__ import annotations

from typing import Any, Optional

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

_MISSING = "MISSING"
_UNKNOWN = "UNKNOWN"

_AVAILABLE_STATUSES: frozenset[str] = frozenset({
    "PRIMARY_RETRIEVED", "FALLBACK_RETRIEVED", "RECONSTRUCTED",
})

SOURCE_ROW_FIELDS_USED: tuple[str, ...] = (
    "event_id", "canonical_event_id", "sport", "market", "stat_key", "prop_type",
    "player", "player_id", "team", "opponent", "game",
    "line", "direction", "side", "ip_value", "innings_pitched",
    "slate_date", "event_date",
    "role_status",
    "hit_probability", "calibrated_probability", "model_used",
    "pulled_at", "as_of", "data_stale", "is_stale",
    "source_conflicts", "gates",
    "pitcher_hand", "starter_flag", "lineup_confirmed", "batting_order",
    "pitch_count_limit", "leash_flag",
    # enrichment fields
    "event_status", "game_log", "box_score_log",
    "l5_ledger", "l10_ledger",
    "market_comparison", "matchup",
    "news_contradiction_check", "acquisition_audit",
)


# ── MLB-specific helpers ──────────────────────────────────────────────────────

def ip_to_outs(ip_val: Any) -> Optional[int]:
    """
    Convert an innings-pitched float to a whole-outs integer.

    4.2 IP → 14 outs (4 * 3 + 2 = 14), NOT int(4.2 * 3) = 12.
    Returns None if ip_val is None or cannot be parsed.

    The partial-innings digit (tenths) represents outs in that inning,
    not a fractional inning. Valid partial values are 0, 1, 2 only.
    A value of 4.3 is structurally invalid (no such partial inning) but
    this function clips to 2 and continues rather than raising, letting
    downstream gates reject the row.
    """
    if ip_val is None:
        return None
    try:
        ip_f = float(ip_val)
    except (TypeError, ValueError):
        return None
    if ip_f < 0:
        return None
    whole_innings = int(ip_f)
    # round(ip_f * 10) % 10 avoids IEEE-754 drift (e.g. 4.2 % 1 ≈ 0.199…)
    partial_outs = round(ip_f * 10) % 10
    # Clip to valid range [0, 2] — outs in an inning max at 2 before third out
    partial_outs = min(partial_outs, 2)
    return whole_innings * 3 + partial_outs


# ── Sub-dict accessors ────────────────────────────────────────────────────────

def _acq_gate(c: dict) -> dict:
    return (c.get("gates") or {}).get("mlb_evidence_acquisition") or {}

def _role_status(c: dict) -> dict:
    rs = c.get("role_status")
    return rs if isinstance(rs, dict) else {}

def _market_comparison(c: dict) -> dict:
    mc = c.get("market_comparison")
    return mc if isinstance(mc, dict) else {}

def _news_contradiction(c: dict) -> dict:
    nc = c.get("news_contradiction_check")
    return nc if isinstance(nc, dict) else {}

def _matchup(c: dict) -> dict:
    m = c.get("matchup")
    return m if isinstance(m, dict) else {}


# ── Identity extraction ───────────────────────────────────────────────────────

def extract_canonical_event_id(c: dict) -> str:
    return str(
        c.get("event_id") or c.get("canonical_event_id") or _MISSING
    ).strip()

def extract_event_name(c: dict) -> Optional[str]:
    game = (c.get("game") or "").strip()
    if game:
        return game
    team = (c.get("team") or "").strip()
    opp  = (c.get("opponent") or "").strip()
    if team and opp:
        return f"{team} vs {opp}"
    return None

def extract_event_date(c: dict) -> Optional[str]:
    for key in ("event_date", "slate_date", "as_of"):
        v = c.get(key)
        if v and str(v).strip():
            return str(v).strip()
    return None

def extract_team_identity(c: dict) -> dict:
    return {
        "team_id":           str(c.get("team") or _MISSING).strip(),
        "team_name":         str(c.get("team") or _MISSING).strip(),
        "opponent_team_id":  str(c.get("opponent") or _MISSING).strip(),
        "opponent_team_name": str(c.get("opponent") or _MISSING).strip(),
    }

def extract_player_identity(c: dict) -> dict:
    return {
        "player_id":   c.get("player_id"),
        "player_name": str(c.get("player") or _MISSING).strip(),
    }

def extract_stat_key(c: dict) -> str:
    """Return the canonical stat_key (lower-cased) or 'UNKNOWN'."""
    raw = c.get("stat_key") or c.get("prop_type") or ""
    return str(raw).strip().lower() or _UNKNOWN


# ── Market / line extraction ──────────────────────────────────────────────────

def extract_market_snapshot(c: dict) -> dict:
    stat_key = extract_stat_key(c)
    raw_line = c.get("line")
    try:
        line_val = float(raw_line) if raw_line is not None else None
    except (TypeError, ValueError):
        line_val = None

    # For pitcher_outs: also derive the outs equivalent from ip_value
    ip_raw = c.get("ip_value") or c.get("innings_pitched")
    outs_equivalent = ip_to_outs(ip_raw)

    snapshot: dict = {
        "stat_key":          stat_key,
        "prop_type":         str(c.get("prop_type") or c.get("stat_key") or _MISSING),
        "line":              line_val,
        "direction":         str(c.get("direction") or _MISSING),
        "side":              str(c.get("side") or _MISSING),
        "market":            str(c.get("market") or _MISSING),
    }
    if outs_equivalent is not None:
        snapshot["outs_equivalent"] = outs_equivalent
    return snapshot

def extract_source_timestamps(c: dict) -> dict:
    ts: dict = {}
    for key in ("pulled_at", "as_of", "event_date", "slate_date"):
        v = c.get(key)
        if v:
            ts[key] = str(v)
    return ts

def extract_source_provenance(c: dict) -> dict:
    acq = _acq_gate(c)
    sources_used = acq.get("sources_used") or []
    prov: dict = {
        "sources_used":  list(sources_used),
        "packet_status": acq.get("packet_status") or _UNKNOWN,
        "model_used":    str(c.get("model_used") or _UNKNOWN),
    }
    return prov

def extract_deterministic_model_inputs(c: dict) -> dict:
    stat_key = extract_stat_key(c)

    # Common inputs
    inputs: dict = {
        "stat_key":             stat_key,
        "hit_probability":      c.get("hit_probability"),
        "calibrated_probability": c.get("calibrated_probability"),
        "model_used":           c.get("model_used"),
        "is_stale":             bool(c.get("data_stale") or c.get("is_stale")),
        "l5_ledger":            c.get("l5_ledger"),
        "l10_ledger":           c.get("l10_ledger"),
    }

    # Pitcher-specific
    inputs["pitcher_hand"]      = c.get("pitcher_hand")
    inputs["starter_flag"]      = c.get("starter_flag")
    inputs["pitch_count_limit"] = c.get("pitch_count_limit")
    inputs["leash_flag"]        = c.get("leash_flag")

    # Batter-specific
    inputs["lineup_confirmed"]  = c.get("lineup_confirmed")
    inputs["batting_order"]     = c.get("batting_order")

    # Innings notation for outs props
    ip_raw = c.get("ip_value") or c.get("innings_pitched")
    outs   = ip_to_outs(ip_raw)
    if outs is not None:
        inputs["outs_equivalent"] = outs
        inputs["ip_raw"]          = ip_raw

    return inputs

def extract_source_failures(c: dict) -> dict:
    acq = _acq_gate(c)
    fields_unresolved = list(acq.get("fields_unresolved") or [])
    return {"fields_unresolved": fields_unresolved} if fields_unresolved else {}

def extract_source_conflicts(c: dict) -> dict:
    conflicts_raw = c.get("source_conflicts")
    if isinstance(conflicts_raw, list) and conflicts_raw:
        return {"conflicts": conflicts_raw}
    if isinstance(conflicts_raw, dict) and conflicts_raw:
        return conflicts_raw
    return {}


# ── MLB-specific metadata ─────────────────────────────────────────────────────

def extract_pitcher_metadata(c: dict) -> dict:
    """Extract pitcher-specific metadata for the sport_specialist role."""
    return {
        "pitcher_hand":      c.get("pitcher_hand"),
        "starter_flag":      c.get("starter_flag"),
        "pitch_count_limit": c.get("pitch_count_limit"),
        "leash_flag":        c.get("leash_flag"),
        "ip_value":          c.get("ip_value") or c.get("innings_pitched"),
        "outs_equivalent":   ip_to_outs(
            c.get("ip_value") or c.get("innings_pitched")
        ),
    }

def extract_batter_metadata(c: dict) -> dict:
    """Extract batter-specific metadata for the sport_specialist role."""
    return {
        "lineup_confirmed": c.get("lineup_confirmed"),
        "batting_order":    c.get("batting_order"),
    }


# ── Data gaps ─────────────────────────────────────────────────────────────────

_REQUIRED_EVIDENCE_FIELDS: tuple[str, ...] = (
    "hit_probability",
    "l10_ledger",
    "role_status",
    "event_status",
)

def build_data_gaps(c: dict) -> list[str]:
    """
    Return a list of "MISSING:{field}" strings for absent required evidence.
    Empty list means all critical evidence is present (COMPLETE).
    """
    gaps: list[str] = []
    for field in _REQUIRED_EVIDENCE_FIELDS:
        if not c.get(field):
            gaps.append(f"MISSING:{field}")
    return gaps
