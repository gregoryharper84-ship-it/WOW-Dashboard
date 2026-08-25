"""
gate_engine/universal_agent/lanes/generic_moneyline/role_inputs.py
WOW-PATCH-2026-08-16-UNIVERSAL-AGENT-CORE-V1-B7

B1 advisory role input builders for the Generic Moneyline Lane.

Design principle
────────────────
This lane does NOT implement sport-specific model logic. The sport_specialist
role payload includes all available probability fields and references the LLP
probability specialist by name — it does NOT call it directly.

No probability fabrication: when calibrated_probability is absent, the payload
records probability_status="PROBABILITY_UNAVAILABLE" and the advisory agent must
surface that gap rather than fabricating a value.

can_execute = False
"""
from __future__ import annotations

from gate_engine.universal_agent.lanes.generic_moneyline.field_map import (
    extract_sport,
    _role_status,
    _market_comparison,
    _news_contradiction,
    _MISSING,
    _UNKNOWN,
)

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

# The LLP probability specialist is referenced by name — not called.
# Advisory agents must route through this specialist for probability decisions.
LLP_PROBABILITY_SPECIALIST_REF = "wow.llp-moneyline-probability-expert"


class RoleInputBuildError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code    = code
        self.message = message


# ── Data Slate Integrity ──────────────────────────────────────────────────────

def build_data_slate_integrity_input(c: dict) -> dict:
    is_stale      = bool(c.get("data_stale") or c.get("is_stale"))
    acq_gate      = (c.get("gates") or {}).get("evidence_acquisition") or {}
    packet_status = acq_gate.get("packet_status") or _UNKNOWN
    fields_unresolved = list(acq_gate.get("fields_unresolved") or [])
    event_status  = str(c.get("event_status") or _UNKNOWN)
    sport         = extract_sport(c)

    return {
        "advisory_only":  True,
        "snapshot_id":    c.get("as_of") or c.get("pulled_at") or _MISSING,
        "agent_id":       "dsi-generic-moneyline-v1",
        "input_tokens":   0,
        "output_tokens":  0,
        "advisory_findings": {
            "data_freshness": {
                "is_stale": is_stale,
            },
            "acquisition_packet": {
                "packet_status":    packet_status,
                "fields_unresolved": fields_unresolved,
            },
            "event_context": {
                "event_status": event_status,
                "sport":        sport,
            },
        },
    }


# ── News / Team Status ────────────────────────────────────────────────────────

def build_news_status_input(c: dict) -> dict:
    rs           = _role_status(c)
    active_status = str(rs.get("active_status") or _UNKNOWN)
    dnp_risk     = bool(rs.get("dnp_risk"))
    injury_flag  = active_status.upper() in {"OUT", "INJURED", "DOUBTFUL"}
    nc           = _news_contradiction(c)

    return {
        "advisory_only":  True,
        "snapshot_id":    c.get("as_of") or c.get("pulled_at") or _MISSING,
        "agent_id":       "ns-generic-moneyline-v1",
        "input_tokens":   0,
        "output_tokens":  0,
        "advisory_findings": {
            "team_status": {
                "active_status": active_status,
                "dnp_risk":      dnp_risk,
                "injury_flag":   injury_flag,
            },
            "news_contradiction": {
                "has_contradiction":  bool(nc.get("contradiction_detected")),
                "contradiction_type": nc.get("contradiction_type") or _UNKNOWN,
                "severity":           nc.get("severity") or _UNKNOWN,
            },
        },
    }


# ── Market Exact Line ─────────────────────────────────────────────────────────

def build_market_exact_line_input(c: dict) -> dict:
    raw_line = c.get("line")
    try:
        line_val = float(raw_line) if raw_line is not None else None
    except (TypeError, ValueError):
        line_val = None

    mc          = _market_comparison(c)
    line_status = "CONFIRMED" if line_val is not None else "UNCONFIRMED"
    is_stale    = bool(c.get("data_stale") or c.get("is_stale"))
    if is_stale and line_status == "CONFIRMED":
        line_status = "STALE"

    return {
        "advisory_only":  True,
        "snapshot_id":    c.get("as_of") or c.get("pulled_at") or _MISSING,
        "agent_id":       "mel-generic-moneyline-v1",
        "input_tokens":   0,
        "output_tokens":  0,
        "advisory_findings": {
            "line": {
                "value":     line_val,
                "status":    line_status,
                "direction": str(c.get("direction") or _MISSING),
                "side":      str(c.get("side") or _MISSING),
            },
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
    SS role: sport-specific inputs for the advisory specialist.

    This role does NOT call the LLP probability specialist directly. It
    packages available probability evidence and records the specialist
    reference (llp_probability_specialist_ref) so downstream advisory agents
    know which specialist to consult.

    probability_status:
      "AVAILABLE"            — calibrated_probability is present
      "RAW_ONLY"             — hit_probability present but no calibrated value
      "IMPLIED_ONLY"         — only implied_probability (from odds) present
      "PROBABILITY_UNAVAILABLE" — no probability evidence at all

    No probability fabrication occurs here regardless of status.
    advisory_only = True.
    """
    sport = extract_sport(c)
    cal_prob  = c.get("calibrated_probability")
    hit_prob  = c.get("hit_probability")
    impl_prob = c.get("implied_probability")
    vig_prob  = c.get("vig_adjusted_probability")

    if cal_prob is not None:
        prob_status = "AVAILABLE"
    elif hit_prob is not None:
        prob_status = "RAW_ONLY"
    elif impl_prob is not None:
        prob_status = "IMPLIED_ONLY"
    else:
        prob_status = "PROBABILITY_UNAVAILABLE"

    return {
        "advisory_only":  True,
        "snapshot_id":    c.get("as_of") or c.get("pulled_at") or _MISSING,
        "agent_id":       "ss-generic-moneyline-v1",
        "input_tokens":   0,
        "output_tokens":  0,
        "advisory_findings": {
            "sport":   sport.upper(),
            "market":  str(c.get("market") or _MISSING),
            "llp_probability_specialist_ref": LLP_PROBABILITY_SPECIALIST_REF,
            "model_routing": {
                "probability_status":          prob_status,
                "probability_fabrication_flag": False,  # never fabricated
                "generic_fallback_blocked":    True,    # no unsupported fallback
            },
            "probability": {
                "calibrated_probability":   cal_prob,
                "hit_probability":          hit_prob,
                "implied_probability":      impl_prob,
                "vig_adjusted_probability": vig_prob,
                "model_used":               c.get("model_used"),
                "llp_decision":             c.get("llp_decision"),
                "llp_label":                c.get("llp_label"),
                "edge":                     c.get("edge"),
            },
            "historical": {
                "l5_ledger":  c.get("l5_ledger"),
                "l10_ledger": c.get("l10_ledger"),
            },
        },
    }


# ── Failure / Contradiction ───────────────────────────────────────────────────

def build_failure_contradiction_input(c: dict) -> dict:
    acq_gate = (c.get("gates") or {}).get("evidence_acquisition") or {}
    fields_unresolved = list(acq_gate.get("fields_unresolved") or [])
    conflicts_raw = c.get("source_conflicts")
    if isinstance(conflicts_raw, list):
        conflicts = conflicts_raw
    elif isinstance(conflicts_raw, dict):
        conflicts = list(conflicts_raw.values())
    else:
        conflicts = []
    nc = _news_contradiction(c)

    return {
        "advisory_only":  True,
        "snapshot_id":    c.get("as_of") or c.get("pulled_at") or _MISSING,
        "agent_id":       "fc-generic-moneyline-v1",
        "input_tokens":   0,
        "output_tokens":  0,
        "advisory_findings": {
            "source_failures": {
                "has_failures":      bool(fields_unresolved),
                "fields_unresolved": fields_unresolved,
            },
            "contradictions": {
                "has_contradictions":    bool(conflicts),
                "source_conflict_count": len(conflicts),
                "news_contradiction":    bool(nc.get("contradiction_detected")),
                "severity":             nc.get("severity") or _UNKNOWN,
            },
        },
    }


# ── Final Refresh ─────────────────────────────────────────────────────────────

def build_final_refresh_input(c: dict) -> dict:
    pp_gate         = (c.get("gates") or {}).get("pp_final_refresh") or {}
    refresh_verdict = str(pp_gate.get("verdict") or _UNKNOWN)

    return {
        "advisory_only":  True,
        "snapshot_id":    c.get("as_of") or c.get("pulled_at") or _MISSING,
        "agent_id":       "fr-generic-moneyline-v1",
        "input_tokens":   0,
        "output_tokens":  0,
        "advisory_findings": {
            "refresh_state": {
                "verdict":    refresh_verdict,
                "line_delta": pp_gate.get("line_delta"),
                "stale_at":   pp_gate.get("stale_at"),
            },
            "pregame_snapshot": {
                "has_pp_gate_data": bool(pp_gate),
            },
        },
    }
