"""
gate_engine/universal_agent/lanes/mlb_props/role_inputs.py
WOW-PATCH-2026-08-16-UNIVERSAL-AGENT-CORE-V1-B5

B1 advisory role input builders for the MLB Props Lane.

Each builder accepts the combined evidence dict and returns a validated
advisory-only role payload dict. These payloads go directly into the
Universal Agent Core orchestrator as B1 role inputs.

MLB-specific invariants enforced here
──────────────────────────────────────
1. pitcher_strikeouts — failure_path_probability_required=True in the
   sport_specialist payload; advisory agents must not omit a failure-path
   probability estimate for this stat.

2. pitcher_outs — outs_equivalent (whole outs from innings notation) is
   included in the sport_specialist payload; advisory agents must use this
   value, not the raw innings float, when comparing against the line.

3. pitcher_1ip_pitches — requires_event_tree=True and
   event_tree_id="MLB_1IP_PITCHES_EVENT_TREE_V1" in the sport_specialist
   payload; generic models are blocked (generic_model_blocked=True).

can_execute = False
"""
from __future__ import annotations

from typing import Any

from gate_engine.universal_agent.lanes.mlb_props.field_map import (
    extract_stat_key,
    extract_pitcher_metadata,
    extract_batter_metadata,
    ip_to_outs,
    _role_status,
    _market_comparison,
    _news_contradiction,
    _MISSING,
    _UNKNOWN,
)
from gate_engine.universal_agent.lanes.mlb_props.event_tree.one_ip_gate import (
    OneIpGate,
    ONE_IP_EVENT_TREE_ID,
)

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

# Stat keys requiring unconditional failure-path probability.
_FAILURE_PATH_PROB_REQUIRED: frozenset[str] = frozenset({
    "pitcher_strikeouts",
})

# Stat keys requiring innings-notation outs conversion in the payload.
_OUTS_CONVERSION_REQUIRED: frozenset[str] = frozenset({
    "pitcher_outs",
})

_one_ip_gate = OneIpGate()


class RoleInputBuildError(Exception):
    """Raised when field_map returns an invalid value that fails B1 validation."""
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code    = code
        self.message = message


# ── Data Slate Integrity ──────────────────────────────────────────────────────

def build_data_slate_integrity_input(c: dict) -> dict:
    """
    DSI role: completeness / freshness / stale flags for this MLB props row.
    advisory_only = True (unconditional).
    """
    is_stale = bool(c.get("data_stale") or c.get("is_stale"))
    acq_gate = (c.get("gates") or {}).get("mlb_evidence_acquisition") or {}
    packet_status = acq_gate.get("packet_status") or _UNKNOWN
    fields_unresolved = list(acq_gate.get("fields_unresolved") or [])
    event_status = str(c.get("event_status") or _UNKNOWN)
    stat_key = extract_stat_key(c)

    return {
        "advisory_only":       True,
        "snapshot_id":         c.get("as_of") or c.get("pulled_at") or _MISSING,
        "agent_id":            "dsi-mlb-props-v1",
        "input_tokens":        0,
        "output_tokens":       0,
        "advisory_findings": {
            "data_freshness": {
                "is_stale":         is_stale,
                "stale_flag_source": "row.data_stale" if c.get("data_stale") else "row.is_stale",
            },
            "acquisition_packet": {
                "packet_status":    packet_status,
                "fields_unresolved": fields_unresolved,
            },
            "event_context": {
                "event_status": event_status,
                "stat_key":     stat_key,
            },
        },
    }


# ── News / Injury Status ──────────────────────────────────────────────────────

def build_news_status_input(c: dict) -> dict:
    """
    NS role: player availability, injury, and lineup status for MLB props.
    advisory_only = True.
    """
    rs = _role_status(c)
    active_status = str(rs.get("active_status") or _UNKNOWN)
    dnp_risk      = bool(rs.get("dnp_risk") or rs.get("expected_dnp"))
    injury_flag   = active_status.upper() in {"OUT", "DOUBTFUL", "INJURED", "IL"}
    nc = _news_contradiction(c)

    return {
        "advisory_only":    True,
        "snapshot_id":      c.get("as_of") or c.get("pulled_at") or _MISSING,
        "agent_id":         "ns-mlb-props-v1",
        "input_tokens":     0,
        "output_tokens":    0,
        "advisory_findings": {
            "player_status": {
                "active_status":   active_status,
                "dnp_risk":        dnp_risk,
                "injury_flag":     injury_flag,
                "lineup_confirmed": bool(c.get("lineup_confirmed")),
                "batting_order":   c.get("batting_order"),
                "starter_flag":    c.get("starter_flag"),
            },
            "news_contradiction": {
                "has_contradiction": bool(nc.get("contradiction_detected")),
                "contradiction_type": nc.get("contradiction_type") or _UNKNOWN,
                "severity":          nc.get("severity") or _UNKNOWN,
            },
        },
    }


# ── Market Exact Line ─────────────────────────────────────────────────────────

def build_market_exact_line_input(c: dict) -> dict:
    """
    MEL role: line confirmation, staleness check, market comparison.
    advisory_only = True.
    """
    raw_line = c.get("line")
    try:
        line_val = float(raw_line) if raw_line is not None else None
    except (TypeError, ValueError):
        line_val = None

    stat_key    = extract_stat_key(c)
    mc          = _market_comparison(c)
    line_status = "CONFIRMED" if line_val is not None else "UNCONFIRMED"
    is_stale    = bool(c.get("data_stale") or c.get("is_stale"))

    if is_stale and line_status == "CONFIRMED":
        line_status = "STALE"

    # For pitcher_outs: convert line to outs equivalent (if line is in IP)
    outs_line = None
    if stat_key in _OUTS_CONVERSION_REQUIRED and line_val is not None:
        outs_line = ip_to_outs(line_val)

    line_dict: dict = {
        "value":    line_val,
        "status":   line_status,
        "direction": str(c.get("direction") or _MISSING),
        "side":     str(c.get("side") or _MISSING),
    }
    if outs_line is not None:
        line_dict["outs_equivalent"] = outs_line

    return {
        "advisory_only":    True,
        "snapshot_id":      c.get("as_of") or c.get("pulled_at") or _MISSING,
        "agent_id":         "mel-mlb-props-v1",
        "input_tokens":     0,
        "output_tokens":    0,
        "advisory_findings": {
            "line": line_dict,
            "market_comparison": {
                "books_checked":    mc.get("books_checked") or [],
                "consensus_line":   mc.get("consensus_line"),
                "line_discrepancy": mc.get("line_discrepancy"),
            },
        },
    }


# ── Sport Specialist ──────────────────────────────────────────────────────────

def build_sport_specialist_input(c: dict) -> dict:
    """
    SS role: MLB-specific inputs for the advisory sport specialist.

    Enforces three MLB props lane invariants:
      1. pitcher_strikeouts  → failure_path_probability_required=True
      2. pitcher_outs        → outs_equivalent in payload (innings conversion)
      3. pitcher_1ip_pitches → requires_event_tree=True, generic_model_blocked=True

    advisory_only = True.
    """
    stat_key = extract_stat_key(c)
    pitcher  = extract_pitcher_metadata(c)
    batter   = extract_batter_metadata(c)

    # 1IP event-tree enforcement
    one_ip_result = _one_ip_gate.evaluate(c)
    requires_event_tree   = one_ip_result.routing_required
    event_tree_id         = one_ip_result.event_tree_id
    generic_model_blocked = one_ip_result.generic_model_blocked

    # Unconditional failure-path probability requirement
    failure_path_prob_required = stat_key in _FAILURE_PATH_PROB_REQUIRED

    # Outs conversion flag
    outs_conversion_required = stat_key in _OUTS_CONVERSION_REQUIRED

    return {
        "advisory_only":    True,
        "snapshot_id":      c.get("as_of") or c.get("pulled_at") or _MISSING,
        "agent_id":         "ss-mlb-props-v1",
        "input_tokens":     0,
        "output_tokens":    0,
        "advisory_findings": {
            "sport":   "MLB",
            "stat_key": stat_key,
            "model_routing": {
                "requires_event_tree":         requires_event_tree,
                "event_tree_id":               event_tree_id,
                "generic_model_blocked":       generic_model_blocked,
                "failure_path_prob_required":  failure_path_prob_required,
                "outs_conversion_required":    outs_conversion_required,
            },
            "pitcher_metadata": pitcher,
            "batter_metadata":  batter,
            "probability": {
                "hit_probability":       c.get("hit_probability"),
                "calibrated_probability": c.get("calibrated_probability"),
                "model_used":            c.get("model_used"),
            },
            "historical": {
                "l5_ledger":  c.get("l5_ledger"),
                "l10_ledger": c.get("l10_ledger"),
            },
        },
    }


# ── Failure / Contradiction ───────────────────────────────────────────────────

def build_failure_contradiction_input(c: dict) -> dict:
    """
    FC role: source failures, acquisition errors, and data contradictions.
    advisory_only = True.
    """
    acq_gate = (c.get("gates") or {}).get("mlb_evidence_acquisition") or {}
    fields_unresolved = list(acq_gate.get("fields_unresolved") or [])
    conflicts_raw = c.get("source_conflicts")
    if isinstance(conflicts_raw, list):
        conflicts = conflicts_raw
    elif isinstance(conflicts_raw, dict):
        conflicts = list(conflicts_raw.values())
    else:
        conflicts = []

    has_failures     = bool(fields_unresolved)
    has_contradictions = bool(conflicts)
    nc = _news_contradiction(c)

    return {
        "advisory_only":    True,
        "snapshot_id":      c.get("as_of") or c.get("pulled_at") or _MISSING,
        "agent_id":         "fc-mlb-props-v1",
        "input_tokens":     0,
        "output_tokens":    0,
        "advisory_findings": {
            "source_failures": {
                "has_failures":      has_failures,
                "fields_unresolved": fields_unresolved,
            },
            "contradictions": {
                "has_contradictions":    has_contradictions,
                "source_conflict_count": len(conflicts),
                "news_contradiction":    bool(nc.get("contradiction_detected")),
                "severity":             nc.get("severity") or _UNKNOWN,
            },
        },
    }


# ── Final Refresh ─────────────────────────────────────────────────────────────

def build_final_refresh_input(c: dict) -> dict:
    """
    FR role: pre-game snapshot vs live refresh comparison.
    advisory_only = True.
    """
    pp_gate = (c.get("gates") or {}).get("pp_final_refresh") or {}
    refresh_verdict = str(pp_gate.get("verdict") or _UNKNOWN)
    refresh_delta   = pp_gate.get("line_delta")
    stale_at        = pp_gate.get("stale_at")

    return {
        "advisory_only":    True,
        "snapshot_id":      c.get("as_of") or c.get("pulled_at") or _MISSING,
        "agent_id":         "fr-mlb-props-v1",
        "input_tokens":     0,
        "output_tokens":    0,
        "advisory_findings": {
            "refresh_state": {
                "verdict":    refresh_verdict,
                "line_delta": refresh_delta,
                "stale_at":   stale_at,
            },
            "pregame_snapshot": {
                "has_pp_gate_data": bool(pp_gate),
            },
        },
    }
