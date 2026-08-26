"""
gate_engine/universal_agent/lanes/generic_moneyline/field_map.py
WOW-PATCH-2026-08-16-UNIVERSAL-AGENT-CORE-V1-B7

Pure deterministic field extraction for generic moneyline evidence rows.

Design principle
────────────────
This lane does NOT implement sport-specific model logic. Probability fields
are extracted and passed through to the sport_specialist role payload, which
references the existing LLP probability specialist. If probability data is
absent, the row degrades but is never fabricated.

can_execute = False
"""
from __future__ import annotations

from typing import Any, Optional

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

_MISSING = "MISSING"
_UNKNOWN = "UNKNOWN"

SOURCE_ROW_FIELDS_USED: tuple[str, ...] = (
    "event_id", "canonical_event_id", "sport", "market",
    "team", "opponent", "player", "game",
    "team_id", "team_name", "opponent_id", "opponent_name",
    "line", "direction", "side",
    "slate_date", "event_date",
    "role_status",
    "hit_probability", "calibrated_probability", "model_used",
    "pulled_at", "as_of", "data_stale", "is_stale",
    "source_conflicts", "gates",
    "spread", "spread_side",
    "implied_probability", "vig_adjusted_probability",
    "llp_decision", "llp_label", "edge",
    # enrichment
    "event_status", "l5_ledger", "l10_ledger",
    "market_comparison", "matchup",
    "news_contradiction_check", "acquisition_audit",
)


# ── Sub-dict accessors ────────────────────────────────────────────────────────

def _role_status(c: dict) -> dict:
    rs = c.get("role_status")
    return rs if isinstance(rs, dict) else {}

def _market_comparison(c: dict) -> dict:
    mc = c.get("market_comparison")
    return mc if isinstance(mc, dict) else {}

def _news_contradiction(c: dict) -> dict:
    nc = c.get("news_contradiction_check")
    return nc if isinstance(nc, dict) else {}


# ── Identity extraction ───────────────────────────────────────────────────────

def extract_canonical_event_id(c: dict) -> str:
    return str(
        c.get("event_id") or c.get("canonical_event_id") or _MISSING
    ).strip()

def extract_event_name(c: dict) -> Optional[str]:
    game = (c.get("game") or "").strip()
    if game:
        return game
    team = (c.get("team") or c.get("team_name") or "").strip()
    opp  = (c.get("opponent") or c.get("opponent_name") or "").strip()
    if team and opp:
        sport = str(c.get("sport") or "").upper()
        return f"{team} vs {opp}" + (f" ({sport})" if sport else "")
    return None

def extract_event_date(c: dict) -> Optional[str]:
    for key in ("event_date", "slate_date", "as_of"):
        v = c.get(key)
        if v and str(v).strip():
            return str(v).strip()
    return None

def extract_team_identity(c: dict) -> dict:
    team = str(c.get("team") or c.get("team_name") or _MISSING).strip()
    opp  = str(c.get("opponent") or c.get("opponent_name") or _MISSING).strip()
    return {
        "team_id":            str(c.get("team_id") or team),
        "team_name":          team,
        "opponent_team_id":   str(c.get("opponent_id") or opp),
        "opponent_team_name": opp,
    }

def extract_sport(c: dict) -> str:
    return str(c.get("sport") or _UNKNOWN).strip().lower()


# ── Market / line extraction ──────────────────────────────────────────────────

def extract_market_snapshot(c: dict) -> dict:
    raw_line = c.get("line")
    try:
        line_val = float(raw_line) if raw_line is not None else None
    except (TypeError, ValueError):
        line_val = None

    spread_raw = c.get("spread")
    try:
        spread_val = float(spread_raw) if spread_raw is not None else None
    except (TypeError, ValueError):
        spread_val = None

    return {
        "market":     str(c.get("market") or _MISSING),
        "sport":      extract_sport(c),
        "line":       line_val,
        "direction":  str(c.get("direction") or _MISSING),
        "side":       str(c.get("side") or _MISSING),
        "spread":     spread_val,
        "spread_side": c.get("spread_side"),
    }

def extract_source_timestamps(c: dict) -> dict:
    ts: dict = {}
    for key in ("pulled_at", "as_of", "event_date", "slate_date"):
        v = c.get(key)
        if v:
            ts[key] = str(v)
    return ts

def extract_source_provenance(c: dict) -> dict:
    acq = (c.get("gates") or {}).get("evidence_acquisition") or {}
    return {
        "sources_used":  list(acq.get("sources_used") or []),
        "packet_status": acq.get("packet_status") or _UNKNOWN,
        "model_used":    str(c.get("model_used") or _UNKNOWN),
    }

def extract_deterministic_model_inputs(c: dict) -> dict:
    return {
        "sport":                   extract_sport(c),
        "hit_probability":         c.get("hit_probability"),
        "calibrated_probability":  c.get("calibrated_probability"),
        "implied_probability":     c.get("implied_probability"),
        "vig_adjusted_probability": c.get("vig_adjusted_probability"),
        "model_used":              c.get("model_used"),
        "llp_decision":            c.get("llp_decision"),
        "llp_label":               c.get("llp_label"),
        "edge":                    c.get("edge"),
        "is_stale":                bool(c.get("data_stale") or c.get("is_stale")),
        "l5_ledger":               c.get("l5_ledger"),
        "l10_ledger":              c.get("l10_ledger"),
    }

def extract_source_failures(c: dict) -> dict:
    acq = (c.get("gates") or {}).get("evidence_acquisition") or {}
    fields_unresolved = list(acq.get("fields_unresolved") or [])
    return {"fields_unresolved": fields_unresolved} if fields_unresolved else {}

def extract_source_conflicts(c: dict) -> dict:
    conflicts_raw = c.get("source_conflicts")
    if isinstance(conflicts_raw, list) and conflicts_raw:
        return {"conflicts": conflicts_raw}
    if isinstance(conflicts_raw, dict) and conflicts_raw:
        return conflicts_raw
    return {}


# ── Data gaps ─────────────────────────────────────────────────────────────────

_REQUIRED_EVIDENCE_FIELDS: tuple[str, ...] = (
    "hit_probability",
    "l10_ledger",
    "role_status",
    "event_status",
)

def build_data_gaps(c: dict) -> list[str]:
    return [
        f"MISSING:{f}" for f in _REQUIRED_EVIDENCE_FIELDS
        if not c.get(f)
    ]
