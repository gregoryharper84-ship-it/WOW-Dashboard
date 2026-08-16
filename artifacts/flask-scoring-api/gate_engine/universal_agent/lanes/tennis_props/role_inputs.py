"""
gate_engine/universal_agent/lanes/tennis_props/role_inputs.py
WOW-PATCH-2026-08-16-UNIVERSAL-AGENT-CORE-V1-B6

B1 advisory role input builders for the Tennis Props Lane.

Tennis-specific invariants enforced here
─────────────────────────────────────────
1. total_games / set_games / first_set_games — requires_markov_chain=True
   in the sport_specialist payload; advisory agents must not use Monte Carlo
   or generic binomial fallback for these stat types.

2. first_set_winner / first_set_games — is_first_set_market=True in the
   sport_specialist payload; agents must not cross-apply full-match models
   to first-set markets.

3. simplex probabilities — stored as raw full-precision floats in the
   sport_specialist payload; agents must not round to 6dp before comparisons
   (causes FP drift in Markov chain simplex constraint checks).

4. Surface type — included in all payloads; advisory agents must flag when
   surface is absent (UNKNOWN) since it is a primary model discriminator.

can_execute = False
"""
from __future__ import annotations

from gate_engine.universal_agent.lanes.tennis_props.field_map import (
    extract_stat_key,
    extract_surface,
    extract_simplex_probabilities,
    is_markov_chain_required,
    is_first_set_market,
    _role_status,
    _market_comparison,
    _news_contradiction,
    _MISSING,
    _UNKNOWN,
)

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"


class RoleInputBuildError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code    = code
        self.message = message


# ── Data Slate Integrity ──────────────────────────────────────────────────────

def build_data_slate_integrity_input(c: dict) -> dict:
    is_stale      = bool(c.get("data_stale") or c.get("is_stale"))
    acq_gate      = (c.get("gates") or {}).get("tennis_evidence_acquisition") or {}
    packet_status = acq_gate.get("packet_status") or _UNKNOWN
    fields_unresolved = list(acq_gate.get("fields_unresolved") or [])
    event_status  = str(c.get("event_status") or _UNKNOWN)
    stat_key      = extract_stat_key(c)
    surface       = extract_surface(c)

    return {
        "advisory_only":  True,
        "snapshot_id":    c.get("as_of") or c.get("pulled_at") or _MISSING,
        "agent_id":       "dsi-tennis-props-v1",
        "input_tokens":   0,
        "output_tokens":  0,
        "advisory_findings": {
            "data_freshness": {
                "is_stale": is_stale,
                "stale_flag_source": "row.data_stale" if c.get("data_stale") else "row.is_stale",
            },
            "acquisition_packet": {
                "packet_status":    packet_status,
                "fields_unresolved": fields_unresolved,
            },
            "event_context": {
                "event_status": event_status,
                "stat_key":     stat_key,
                "surface":      surface,
                "surface_missing": surface == _UNKNOWN,
            },
        },
    }


# ── News / Player Status ──────────────────────────────────────────────────────

def build_news_status_input(c: dict) -> dict:
    rs           = _role_status(c)
    active_status = str(rs.get("active_status") or _UNKNOWN)
    dnp_risk     = bool(rs.get("dnp_risk") or rs.get("expected_dnp"))
    injury_flag  = active_status.upper() in {"OUT", "WITHDRAWN", "RETIRED", "INJURED", "WD"}
    nc           = _news_contradiction(c)

    return {
        "advisory_only":  True,
        "snapshot_id":    c.get("as_of") or c.get("pulled_at") or _MISSING,
        "agent_id":       "ns-tennis-props-v1",
        "input_tokens":   0,
        "output_tokens":  0,
        "advisory_findings": {
            "player_status": {
                "active_status": active_status,
                "dnp_risk":      dnp_risk,
                "injury_flag":   injury_flag,
                "withdrawal_risk": bool(rs.get("withdrawal_risk")),
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

    stat_key    = extract_stat_key(c)
    mc          = _market_comparison(c)
    line_status = "CONFIRMED" if line_val is not None else "UNCONFIRMED"
    is_stale    = bool(c.get("data_stale") or c.get("is_stale"))
    if is_stale and line_status == "CONFIRMED":
        line_status = "STALE"

    return {
        "advisory_only":  True,
        "snapshot_id":    c.get("as_of") or c.get("pulled_at") or _MISSING,
        "agent_id":       "mel-tennis-props-v1",
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
    SS role: tennis-specific model routing inputs for the advisory agent.

    Enforces three Tennis Props lane invariants:
      1. total_games/set_games/first_set_games → requires_markov_chain=True
      2. first_set_* markets → is_first_set_market=True (no cross-model)
      3. simplex probabilities stored as raw full-precision floats

    advisory_only = True.
    """
    stat_key = extract_stat_key(c)
    surface  = extract_surface(c)
    simplex  = extract_simplex_probabilities(c)

    requires_mc    = is_markov_chain_required(c)
    first_set_mkt  = is_first_set_market(c)

    findings: dict = {
        "sport":    "TENNIS",
        "stat_key": stat_key,
        "surface":  surface,
        "surface_missing": surface == _UNKNOWN,
        "model_routing": {
            "requires_markov_chain":    requires_mc,
            "monte_carlo_blocked":      requires_mc,  # blocked when Markov required
            "is_first_set_market":      first_set_mkt,
            "generic_model_blocked":    requires_mc or first_set_mkt,
        },
        "match_context": {
            "best_of":         c.get("best_of"),
            "set_number":      c.get("set_number"),
            "tournament":      c.get("tournament"),
            "round":           c.get("round"),
        },
        "player_context": {
            "player_1":        c.get("player_1") or c.get("player"),
            "player_2":        c.get("player_2") or c.get("opponent"),
            "player_1_rank":   c.get("player_1_rank"),
            "player_2_rank":   c.get("player_2_rank"),
            "serve_hold_rate": c.get("serve_hold_rate"),
            "break_point_rate": c.get("break_point_rate"),
            "h2h_surface_wins":   c.get("h2h_surface_wins"),
            "h2h_surface_losses": c.get("h2h_surface_losses"),
        },
        "probability": {
            "hit_probability":        c.get("hit_probability"),
            "calibrated_probability": c.get("calibrated_probability"),
            "model_used":             c.get("model_used"),
        },
        "historical": {
            "l5_ledger":  c.get("l5_ledger"),
            "l10_ledger": c.get("l10_ledger"),
        },
    }
    # simplex stored as raw full-precision floats (not rounded)
    if simplex:
        findings["simplex"] = simplex

    return {
        "advisory_only":     True,
        "snapshot_id":       c.get("as_of") or c.get("pulled_at") or _MISSING,
        "agent_id":          "ss-tennis-props-v1",
        "input_tokens":      0,
        "output_tokens":     0,
        "advisory_findings": findings,
    }


# ── Failure / Contradiction ───────────────────────────────────────────────────

def build_failure_contradiction_input(c: dict) -> dict:
    acq_gate = (c.get("gates") or {}).get("tennis_evidence_acquisition") or {}
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
        "agent_id":       "fc-tennis-props-v1",
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
    refresh_delta   = pp_gate.get("line_delta")
    stale_at        = pp_gate.get("stale_at")

    return {
        "advisory_only":  True,
        "snapshot_id":    c.get("as_of") or c.get("pulled_at") or _MISSING,
        "agent_id":       "fr-tennis-props-v1",
        "input_tokens":   0,
        "output_tokens":  0,
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
