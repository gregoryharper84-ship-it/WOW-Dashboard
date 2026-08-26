"""
gate_engine/universal_agent/lanes/wnba_props/field_map.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4

Pure deterministic field extraction for WNBA/NBA props evidence rows.

All functions accept the combined evidence dict ({**enrichment, **row},
row takes precedence) as read-only and return structured dicts / scalars.
Missing fields → "MISSING" / "UNKNOWN" sentinels. Never fabricates.

can_execute = False
"""
from __future__ import annotations

from typing import Any

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

_AVAILABLE = "available"
_MISSING   = "missing"

_AVAILABLE_STATUSES: frozenset[str] = frozenset({
    "PRIMARY_RETRIEVED", "FALLBACK_RETRIEVED", "RECONSTRUCTED",
})

SOURCE_ROW_FIELDS_USED: tuple[str, ...] = (
    "event_id", "sport", "market", "prop_type",
    "player", "team", "opponent", "game",
    "line", "direction", "side",
    "slate_date", "event_date",
    "role_status",
    "hit_probability", "calibrated_probability", "model_used",
    "pulled_at", "as_of", "data_stale", "is_stale",
    "source_conflicts", "gates",
    # enrichment fields (merged into combined dict)
    "event_status", "game_log", "box_score_log",
    "l5_ledger", "l10_ledger",
    "market_comparison", "matchup",
    "news_contradiction_check", "acquisition_audit",
)


# ── Sub-dict accessors ────────────────────────────────────────────────────────

def _acq_gate(c: dict) -> dict:
    return (c.get("gates") or {}).get("wnba_evidence_acquisition") or {}

def _packet_status(c: dict) -> str | None:
    return _acq_gate(c).get("packet_status")

def _fields_unresolved(c: dict) -> list:
    return list(_acq_gate(c).get("fields_unresolved") or [])

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
    return str(c["event_id"]).strip()

def extract_event_name(c: dict) -> str | None:
    game = (c.get("game") or "").strip()
    if game:
        return game
    team = (c.get("team") or "").strip()
    opp  = (c.get("opponent") or "").strip()
    if team and opp:
        return f"{team} vs {opp}"
    return team or opp or None

def extract_event_date(c: dict) -> str | None:
    d = c.get("slate_date") or c.get("event_date")
    return str(d).strip() if d else None

def extract_player_identity(c: dict) -> dict[str, str | None]:
    return {
        "player_name":        c.get("player"),
        "team_name":          c.get("team"),
        "opponent_team_name": c.get("opponent"),
    }

def extract_team_identity(c: dict) -> dict[str, str | None]:
    return {
        "team_id": None, "team_name": c.get("team"),
        "opponent_team_id": None, "opponent_team_name": c.get("opponent"),
    }


# ── Source metadata ───────────────────────────────────────────────────────────

def extract_source_timestamps(c: dict) -> dict[str, str]:
    ts: dict[str, str] = {}
    for key in ("pulled_at", "as_of"):
        if c.get(key) is not None:
            ts[key] = str(c[key])
    rs_ts = _role_status(c).get("role_timestamp")
    if rs_ts is not None:
        ts["role_timestamp"] = str(rs_ts)
    return ts

def extract_source_provenance(c: dict) -> dict[str, str]:
    prov: dict[str, str] = {}
    rs = _role_status(c)
    sources = rs.get("sources")
    if isinstance(sources, list):
        for i, s in enumerate(sources):
            prov[f"role_status_source_{i}"] = str(s)
    elif isinstance(sources, str) and sources:
        prov["role_status_source"] = sources
    if c.get("model_used"):
        prov["model_used"] = str(c["model_used"])
    return prov


# ── Market / model snapshots ──────────────────────────────────────────────────

def extract_market_snapshot(c: dict) -> dict[str, Any]:
    mc = _market_comparison(c)
    return {
        "market_type":            c.get("market") or c.get("prop_type"),
        "line":                   c.get("line"),
        "direction":              c.get("direction") or c.get("side"),
        "over_odds":              mc.get("over_odds"),
        "under_odds":             mc.get("under_odds"),
        "hit_probability":        c.get("hit_probability"),
        "calibrated_probability": c.get("calibrated_probability"),
        "model_used":             c.get("model_used"),
        "event_status":           c.get("event_status"),
        "packet_status":          _packet_status(c),
    }

def extract_deterministic_model_inputs(c: dict) -> dict[str, Any]:
    rs = _role_status(c)
    return {
        "hit_probability":        c.get("hit_probability"),
        "calibrated_probability": c.get("calibrated_probability"),
        "model_used":             c.get("model_used"),
        "active_status":          rs.get("active_status"),
        "projected_minutes":      rs.get("projected_minutes"),
        "usage_role":             rs.get("usage_role"),
        "packet_status":          _packet_status(c),
        "event_status":           c.get("event_status"),
    }


# ── Failure / conflict ────────────────────────────────────────────────────────

def extract_source_failures(c: dict) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for f in _fields_unresolved(c):
        failures.append({"source": "wnba_evidence_acquisition",
                         "reason": f"field_unresolved:{f}", "severity": "LOW"})
    if _packet_status(c) == "PACKET_INCOMPLETE_REJECTED":
        failures.append({"source": "wnba_evidence_acquisition",
                         "reason": "PACKET_INCOMPLETE_REJECTED", "severity": "HIGH"})
    return failures

def extract_source_conflicts(c: dict) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for item in (c.get("source_conflicts") or []):
        if isinstance(item, dict):
            conflicts.append(item)
    nc = _news_contradiction(c)
    if nc.get("conflict_status") == "CONFLICT":
        conflicts.append({"source": "news_contradiction_check",
                          "reason": nc.get("conflict_detail") or "CONFLICT",
                          "severity": "HIGH"})
    return conflicts


# ── DATA_SLATE_INTEGRITY helpers ──────────────────────────────────────────────

def build_source_coverage(c: dict) -> dict[str, str]:
    rs = _role_status(c)
    checks = {
        "active_status":          rs.get("active_status"),
        "projected_minutes":      rs.get("projected_minutes"),
        "hit_probability":        c.get("hit_probability"),
        "calibrated_probability": c.get("calibrated_probability"),
        "packet_status":          _packet_status(c),
        "event_status":           c.get("event_status"),
        "game_log":               c.get("game_log") or c.get("box_score_log"),
        "market_comparison":      c.get("market_comparison"),
    }
    return {k: (_AVAILABLE if v is not None else _MISSING) for k, v in checks.items()}

def build_data_gaps(c: dict) -> list[str]:
    cov = build_source_coverage(c)
    return sorted(f"MISSING:{k}" for k, v in cov.items() if v == _MISSING)

def derive_data_freshness(c: dict) -> str:
    if c.get("hit_probability") is None and c.get("calibrated_probability") is None:
        return "MISSING"
    if c.get("data_stale") or c.get("is_stale"):
        return "STALE"
    if c.get("pulled_at") or c.get("as_of"):
        return "FRESH"
    return "UNKNOWN"

def derive_slate_consistency(c: dict) -> str:
    pkt = _packet_status(c)
    if pkt in ("PACKET_COMPLETE", "PACKET_PARTIAL_DEGRADED"):
        return "CONSISTENT"
    if pkt == "PACKET_INCOMPLETE_REJECTED":
        return "INCONSISTENT"
    return "UNKNOWN"


# ── NEWS_STATUS helpers ───────────────────────────────────────────────────────

_ACTIVE_TO_PLAYER_STATUS: dict[str, str] = {
    "ACTIVE": "ACTIVE", "AVAILABLE": "ACTIVE",
    "PROBABLE": "QUESTIONABLE", "QUESTIONABLE": "QUESTIONABLE",
    "GTD": "QUESTIONABLE", "GAME_TIME_DECISION": "QUESTIONABLE",
    "DOUBTFUL": "DOUBTFUL",
    "OUT": "OUT", "DNP": "OUT", "INACTIVE": "OUT",
}

def map_active_status_to_player_status(active_status: str | None) -> str:
    if active_status is None:
        return "UNKNOWN"
    return _ACTIVE_TO_PLAYER_STATUS.get(active_status.strip().upper(), "UNKNOWN")


# ── MARKET_EXACT_LINE helpers ─────────────────────────────────────────────────

def derive_market_status(c: dict) -> str:
    event = (c.get("event_status") or "").strip().upper()
    if event in ("POSTPONED", "CANCELLED", "FINAL", "COMPLETE"):
        return "CLOSED"
    if event in ("SUSPENDED", "DELAYED"):
        return "SUSPENDED"
    if event in ("SCHEDULED", "PREGAME", "PRE_GAME", "ACTIVE_PREGAME_VALID", "OPEN"):
        return "OPEN"
    return "UNKNOWN"


# ── SPORT_SPECIALIST helpers ──────────────────────────────────────────────────

def derive_assessment_confidence(c: dict) -> str:
    rs = _role_status(c)
    hp_ok  = c.get("hit_probability") is not None
    cal_ok = c.get("calibrated_probability") is not None
    min_ok = rs.get("projected_minutes") is not None
    pkt    = _packet_status(c)
    n = sum([hp_ok, cal_ok, min_ok])
    if n == 3 and pkt == "PACKET_COMPLETE":
        return "HIGH"
    if n >= 2 and pkt != "PACKET_INCOMPLETE_REJECTED":
        return "MEDIUM"
    if n >= 1:
        return "LOW"
    return "UNKNOWN"


# ── FAILURE_CONTRADICTION helpers ─────────────────────────────────────────────

def derive_contradiction_severity(c: dict) -> str:
    pkt = _packet_status(c)
    nc  = _news_contradiction(c)
    if pkt == "PACKET_INCOMPLETE_REJECTED" or nc.get("conflict_status") == "CONFLICT":
        return "HIGH"
    if _fields_unresolved(c):
        return "LOW"
    if pkt == "PACKET_COMPLETE":
        return "NONE"
    return "UNKNOWN"

def derive_resolution_recommendation(c: dict) -> str:
    pkt = _packet_status(c)
    nc  = _news_contradiction(c)
    if pkt == "PACKET_INCOMPLETE_REJECTED" or nc.get("conflict_status") == "CONFLICT":
        return "ABORT"
    if pkt == "PACKET_COMPLETE" and not _fields_unresolved(c):
        return "PROCEED"
    if pkt == "PACKET_PARTIAL_DEGRADED" or _fields_unresolved(c):
        return "HOLD"
    return "UNKNOWN"

def derive_failure_detected(c: dict) -> bool:
    if _packet_status(c) == "PACKET_INCOMPLETE_REJECTED":
        return True
    if _fields_unresolved(c):
        return True
    return c.get("hit_probability") is None and c.get("calibrated_probability") is None

def derive_contradiction_detected(c: dict) -> bool:
    return _news_contradiction(c).get("conflict_status") == "CONFLICT"


# ── FINAL_REFRESH helpers ─────────────────────────────────────────────────────

def derive_refresh_status(data_gaps: list[str]) -> str:
    return "COMPLETE" if not data_gaps else "PARTIAL"

def derive_evidence_snapshot_valid(c: dict) -> bool:
    return _packet_status(c) != "PACKET_INCOMPLETE_REJECTED"
