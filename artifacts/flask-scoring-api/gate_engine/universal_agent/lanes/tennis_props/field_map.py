"""
gate_engine/universal_agent/lanes/tennis_props/field_map.py
WOW-PATCH-2026-08-16-UNIVERSAL-AGENT-CORE-V1-B6

Pure deterministic field extraction for tennis props evidence rows.

All functions accept the combined evidence dict ({**enrichment, **row},
row takes precedence) as read-only and return structured dicts / scalars.
Missing fields → "MISSING" / "UNKNOWN" sentinels. Never fabricates.

Tennis-specific notes
─────────────────────
- Surface type (clay/grass/hard/carpet) is key model input; absent surface
  degrades the row but does not fail it.
- Three-outcome simplex probabilities (under/exact/over) for total_games
  are stored as raw full-precision floats — never rounded to 6dp — to
  prevent FP drift in downstream Markov chain comparisons.
- First-set markets (first_set_winner, first_set_games) are structurally
  distinct from full-match markets and must not be cross-assigned.

can_execute = False
"""
from __future__ import annotations

from typing import Any, Optional

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

_MISSING = "MISSING"
_UNKNOWN = "UNKNOWN"

# Stat keys that require Markov chain model routing.
MARKOV_CHAIN_STAT_KEYS: frozenset[str] = frozenset({
    "total_games", "games_total",
    "set_games",
    "first_set_games",
})

# Stat keys that are first-set scoped markets.
FIRST_SET_STAT_KEYS: frozenset[str] = frozenset({
    "first_set_winner", "first_set_games",
})

# Valid surface strings (lower-cased).
VALID_SURFACES: frozenset[str] = frozenset({
    "clay", "grass", "hard", "carpet", "indoor_hard", "outdoor_hard",
    "indoor_clay", "outdoor_clay",
})

SOURCE_ROW_FIELDS_USED: tuple[str, ...] = (
    "event_id", "canonical_event_id", "sport", "market", "stat_key", "prop_type",
    "player", "player_id", "team", "opponent",
    "player_1", "player_2", "player_1_id", "player_2_id",
    "tournament", "surface", "round",
    "line", "direction", "side",
    "best_of", "set_number",
    "slate_date", "event_date",
    "role_status",
    "hit_probability", "calibrated_probability", "model_used",
    "simplex_under", "simplex_exact", "simplex_over",
    "player_1_rank", "player_2_rank",
    "h2h_surface_wins", "h2h_surface_losses",
    "serve_hold_rate", "break_point_rate",
    "pulled_at", "as_of", "data_stale", "is_stale",
    "source_conflicts", "gates",
    # enrichment fields
    "event_status", "game_log", "l5_ledger", "l10_ledger",
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

def _matchup(c: dict) -> dict:
    m = c.get("matchup")
    return m if isinstance(m, dict) else {}


# ── Tennis-specific helpers ───────────────────────────────────────────────────

def extract_surface(c: dict) -> str:
    """Return lower-cased surface string or 'UNKNOWN'."""
    raw = c.get("surface") or _matchup(c).get("surface") or ""
    s = str(raw).strip().lower()
    return s if s else _UNKNOWN

def extract_stat_key(c: dict) -> str:
    raw = c.get("stat_key") or c.get("prop_type") or ""
    return str(raw).strip().lower() or _UNKNOWN

def is_markov_chain_required(c: dict) -> bool:
    return extract_stat_key(c) in MARKOV_CHAIN_STAT_KEYS

def is_first_set_market(c: dict) -> bool:
    return extract_stat_key(c) in FIRST_SET_STAT_KEYS

def extract_simplex_probabilities(c: dict) -> Optional[dict]:
    """
    Extract three-outcome simplex (under/exact/over) probabilities.
    Stores as raw full-precision floats — never rounded — to prevent
    FP drift in Markov chain comparisons.
    Returns None when none of the three values are present.
    """
    under = c.get("simplex_under")
    exact = c.get("simplex_exact")
    over  = c.get("simplex_over")

    if under is None and exact is None and over is None:
        return None

    result: dict = {}
    for k, v in (("under", under), ("exact", exact), ("over", over)):
        if v is not None:
            try:
                result[k] = float(v)  # full precision, not rounded
            except (TypeError, ValueError):
                result[k] = None
        else:
            result[k] = None

    # Validate simplex constraint (advisory; does not block result)
    defined = [v for v in result.values() if v is not None]
    if len(defined) == 3:
        total = sum(defined)
        result["simplex_sum"] = total
        result["simplex_valid"] = abs(total - 1.0) < 1e-6
    else:
        result["simplex_valid"] = False

    return result


# ── Identity extraction ───────────────────────────────────────────────────────

def extract_canonical_event_id(c: dict) -> str:
    return str(
        c.get("event_id") or c.get("canonical_event_id") or _MISSING
    ).strip()

def extract_event_name(c: dict) -> Optional[str]:
    # Tennis: player_1 vs player_2 in tournament
    p1 = (c.get("player_1") or c.get("player") or "").strip()
    p2 = (c.get("player_2") or c.get("opponent") or "").strip()
    tour = (c.get("tournament") or "").strip()
    if p1 and p2:
        name = f"{p1} vs {p2}"
        if tour:
            name += f" ({tour})"
        return name
    return None

def extract_event_date(c: dict) -> Optional[str]:
    for key in ("event_date", "slate_date", "as_of"):
        v = c.get(key)
        if v and str(v).strip():
            return str(v).strip()
    return None

def extract_team_identity(c: dict) -> dict:
    # Tennis has no teams; use player names as team-slot stand-ins
    p1 = str(c.get("player_1") or c.get("player") or c.get("team") or _MISSING).strip()
    p2 = str(c.get("player_2") or c.get("opponent") or _MISSING).strip()
    return {
        "team_id":            p1,
        "team_name":          p1,
        "opponent_team_id":   p2,
        "opponent_team_name": p2,
    }

def extract_player_identity(c: dict) -> dict:
    player_name = (
        c.get("player_1") or c.get("player") or _MISSING
    )
    return {
        "player_id":   c.get("player_1_id") or c.get("player_id"),
        "player_name": str(player_name).strip(),
    }


# ── Market / line extraction ──────────────────────────────────────────────────

def extract_market_snapshot(c: dict) -> dict:
    stat_key = extract_stat_key(c)
    raw_line = c.get("line")
    try:
        line_val = float(raw_line) if raw_line is not None else None
    except (TypeError, ValueError):
        line_val = None

    simplex = extract_simplex_probabilities(c)

    snapshot: dict = {
        "stat_key":              stat_key,
        "prop_type":             str(c.get("prop_type") or c.get("stat_key") or _MISSING),
        "line":                  line_val,
        "direction":             str(c.get("direction") or _MISSING),
        "side":                  str(c.get("side") or _MISSING),
        "market":                str(c.get("market") or _MISSING),
        "surface":               extract_surface(c),
        "best_of":               c.get("best_of"),
        "set_number":            c.get("set_number"),
        "is_first_set_market":   is_first_set_market(c),
        "markov_chain_required": is_markov_chain_required(c),
    }
    if simplex:
        snapshot["simplex"] = simplex
    return snapshot

def extract_source_timestamps(c: dict) -> dict:
    ts: dict = {}
    for key in ("pulled_at", "as_of", "event_date", "slate_date"):
        v = c.get(key)
        if v:
            ts[key] = str(v)
    return ts

def extract_source_provenance(c: dict) -> dict:
    acq = (c.get("gates") or {}).get("tennis_evidence_acquisition") or {}
    return {
        "sources_used":  list(acq.get("sources_used") or []),
        "packet_status": acq.get("packet_status") or _UNKNOWN,
        "model_used":    str(c.get("model_used") or _UNKNOWN),
    }

def extract_deterministic_model_inputs(c: dict) -> dict:
    stat_key = extract_stat_key(c)
    simplex  = extract_simplex_probabilities(c)

    inputs: dict = {
        "stat_key":                stat_key,
        "hit_probability":         c.get("hit_probability"),
        "calibrated_probability":  c.get("calibrated_probability"),
        "model_used":              c.get("model_used"),
        "is_stale":                bool(c.get("data_stale") or c.get("is_stale")),
        "surface":                 extract_surface(c),
        "best_of":                 c.get("best_of"),
        "set_number":              c.get("set_number"),
        "player_1_rank":           c.get("player_1_rank"),
        "player_2_rank":           c.get("player_2_rank"),
        "serve_hold_rate":         c.get("serve_hold_rate"),
        "break_point_rate":        c.get("break_point_rate"),
        "h2h_surface_wins":        c.get("h2h_surface_wins"),
        "h2h_surface_losses":      c.get("h2h_surface_losses"),
        "l5_ledger":               c.get("l5_ledger"),
        "l10_ledger":              c.get("l10_ledger"),
        "markov_chain_required":   is_markov_chain_required(c),
        "is_first_set_market":     is_first_set_market(c),
    }
    if simplex:
        inputs["simplex"] = simplex
    return inputs

def extract_source_failures(c: dict) -> dict:
    acq = (c.get("gates") or {}).get("tennis_evidence_acquisition") or {}
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
    "surface",
)

def build_data_gaps(c: dict) -> list[str]:
    gaps: list[str] = []
    for field in _REQUIRED_EVIDENCE_FIELDS:
        val = c.get(field)
        if field == "surface":
            # surface is a gap only when genuinely absent (not just "UNKNOWN")
            if not val or str(val).strip().lower() in ("", "unknown"):
                gaps.append(f"MISSING:{field}")
        elif not val:
            gaps.append(f"MISSING:{field}")
    return gaps
