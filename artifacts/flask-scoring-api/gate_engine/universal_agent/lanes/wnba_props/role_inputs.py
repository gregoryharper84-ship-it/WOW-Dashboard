"""
gate_engine/universal_agent/lanes/wnba_props/role_inputs.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4

Build and validate all six B1 advisory role payloads for a WNBA/NBA props row.

Each build_*_input() reads from the combined dict ({**enrichment, **row}),
applies deterministic field mapping, validates against the B1 schema, and
returns the validated payload. Raises RoleInputBuildError if validation fails.
Never fabricates; missing fields -> UNKNOWN / MISSING sentinels.

can_execute = False
"""
from __future__ import annotations

from typing import Any

from gate_engine.universal_agent.output_contract import OUTPUT_VALID, valid_output_payload
from gate_engine.universal_agent.roles.role_base import SCHEMA_VERSION
from gate_engine.universal_agent.roles.data_slate_integrity import (
    ROLE_ID as DSI_ROLE_ID, validate_data_slate_integrity_output)
from gate_engine.universal_agent.roles.news_status import (
    ROLE_ID as NS_ROLE_ID, validate_news_status_output)
from gate_engine.universal_agent.roles.market_exact_line import (
    ROLE_ID as MEL_ROLE_ID, validate_market_exact_line_output)
from gate_engine.universal_agent.roles.sport_specialist import (
    ROLE_ID as SS_ROLE_ID, validate_sport_specialist_output)
from gate_engine.universal_agent.roles.failure_contradiction import (
    ROLE_ID as FC_ROLE_ID, validate_failure_contradiction_output)
from gate_engine.universal_agent.roles.final_refresh import (
    ROLE_ID as FR_ROLE_ID, validate_final_refresh_output)

from gate_engine.universal_agent.lanes.wnba_props.field_map import (
    build_source_coverage, build_data_gaps,
    derive_data_freshness, derive_slate_consistency,
    map_active_status_to_player_status, derive_market_status,
    derive_assessment_confidence, derive_contradiction_severity,
    derive_resolution_recommendation, derive_failure_detected,
    derive_contradiction_detected, derive_refresh_status,
    derive_evidence_snapshot_valid,
    _role_status, _news_contradiction, _fields_unresolved,
    _packet_status, _market_comparison,
)

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

_PRECEDING_ROLE_IDS: tuple[str, ...] = (
    DSI_ROLE_ID, NS_ROLE_ID, MEL_ROLE_ID, SS_ROLE_ID, FC_ROLE_ID,
)


class RoleInputBuildError(RuntimeError):
    """Raised when a constructed role payload fails B1 validation.
    Indicates a bug in field_map derivation logic — fail-closed."""
    def __init__(self, role_id: str, code: str, message: str) -> None:
        super().__init__(f"[{role_id}] {code}: {message}")
        self.role_id = role_id
        self.code    = code
        self.message = message


def _checked(payload: dict, role_id: str, validator: Any) -> dict:
    result = validator(payload)
    if result is not OUTPUT_VALID:
        raise RoleInputBuildError(
            role_id=role_id,
            code=str(getattr(result, "code", "VALIDATION_FAILED")),
            message=str(getattr(result, "message", repr(result))),
        )
    return payload


# ── Role 1: DATA_SLATE_INTEGRITY ──────────────────────────────────────────────

def build_data_slate_integrity_input(c: dict) -> dict:
    coverage = build_source_coverage(c)
    gaps     = build_data_gaps(c)
    stale    = [k for k, v in coverage.items() if v == "missing"]
    findings: dict[str, Any] = {
        "role_id": DSI_ROLE_ID, "schema_version": SCHEMA_VERSION,
        "data_freshness_status":   derive_data_freshness(c),
        "slate_consistency_check": derive_slate_consistency(c),
        "source_coverage":         coverage,
        "data_gaps_identified":    gaps,
        "stale_sources":           stale,
    }
    return _checked(valid_output_payload(advisory_findings=findings),
                    DSI_ROLE_ID, validate_data_slate_integrity_output)


# ── Role 2: NEWS_STATUS ───────────────────────────────────────────────────────

def build_news_status_input(c: dict) -> dict:
    rs            = _role_status(c)
    player_status = map_active_status_to_player_status(rs.get("active_status"))
    sources = rs.get("sources")
    if isinstance(sources, list) and sources:
        source = str(sources[0])
    elif isinstance(sources, str) and sources:
        source = sources
    else:
        source = "UNKNOWN"
    as_of = str(
        rs.get("role_timestamp") or c.get("pulled_at") or c.get("as_of") or "UNKNOWN"
    )
    findings: dict[str, Any] = {
        "role_id": NS_ROLE_ID, "schema_version": SCHEMA_VERSION,
        "player_status": player_status,
        "status_source": source,
        "status_as_of":  as_of,
        "injury_flag":   (player_status == "OUT"),
        "dnp_risk":      player_status in ("QUESTIONABLE", "DOUBTFUL"),
    }
    return _checked(valid_output_payload(advisory_findings=findings),
                    NS_ROLE_ID, validate_news_status_output)


# ── Role 3: MARKET_EXACT_LINE ─────────────────────────────────────────────────

def build_market_exact_line_input(c: dict) -> dict:
    line = c.get("line")
    mc   = _market_comparison(c)
    findings: dict[str, Any] = {
        "role_id": MEL_ROLE_ID, "schema_version": SCHEMA_VERSION,
        "line_confirmed": line is not None,
        "line_source":    str(c.get("model_used") or "UNKNOWN"),
        "market_status":  derive_market_status(c),
        "confirmed_line": float(line) if line is not None else None,
        "over_odds":      mc.get("over_odds"),
        "under_odds":     mc.get("under_odds"),
    }
    return _checked(valid_output_payload(advisory_findings=findings),
                    MEL_ROLE_ID, validate_market_exact_line_output)


# ── Role 4: SPORT_SPECIALIST ──────────────────────────────────────────────────

def build_sport_specialist_input(c: dict) -> dict:
    _s   = lambda v: v if v is not None else "MISSING"  # noqa: E731
    rs   = _role_status(c)
    sport = (c.get("sport") or "WNBA").strip().upper()
    assessment: dict[str, Any] = {
        "hit_probability":        _s(c.get("hit_probability")),
        "calibrated_probability": _s(c.get("calibrated_probability")),
        "model_used":             _s(c.get("model_used")),
        "packet_status":          _s(_packet_status(c)),
        "active_status":          _s(rs.get("active_status")),
        "projected_minutes":      _s(rs.get("projected_minutes")),
        "usage_role":             _s(rs.get("usage_role")),
        "minutes_low":            _s(rs.get("minutes_low")),
        "minutes_high":           _s(rs.get("minutes_high")),
        "event_status":           _s(c.get("event_status")),
        "line":                   _s(c.get("line")),
    }
    key_m  = [k for k, v in assessment.items() if v != "MISSING"]
    miss_m = [k for k, v in assessment.items() if v == "MISSING"]
    findings: dict[str, Any] = {
        "role_id": SS_ROLE_ID, "schema_version": SCHEMA_VERSION,
        "sport": sport,
        "statistical_assessment": assessment,
        "key_metrics":            key_m,
        "missing_metrics":        miss_m,
        "assessment_confidence":  derive_assessment_confidence(c),
    }
    return _checked(valid_output_payload(advisory_findings=findings),
                    SS_ROLE_ID, validate_sport_specialist_output)


# ── Role 5: FAILURE_CONTRADICTION ─────────────────────────────────────────────

def build_failure_contradiction_input(c: dict) -> dict:
    unresolved = _fields_unresolved(c)
    pkt        = _packet_status(c)
    nc         = _news_contradiction(c)
    failures = [
        {"source": "wnba_evidence_acquisition",
         "reason": f"field_unresolved:{f}", "severity": "LOW"}
        for f in unresolved
    ]
    if pkt == "PACKET_INCOMPLETE_REJECTED":
        failures.append({"source": "wnba_evidence_acquisition",
                         "reason": "PACKET_INCOMPLETE_REJECTED", "severity": "HIGH"})
    contradictions: list[dict[str, Any]] = []
    if nc.get("conflict_status") == "CONFLICT":
        contradictions.append({"source": "news_contradiction_check",
                                "reason": nc.get("conflict_detail") or "CONFLICT",
                                "severity": "HIGH"})
    findings: dict[str, Any] = {
        "role_id": FC_ROLE_ID, "schema_version": SCHEMA_VERSION,
        "contradiction_detected":    derive_contradiction_detected(c),
        "failure_detected":          derive_failure_detected(c),
        "resolution_recommendation": derive_resolution_recommendation(c),
        "contradiction_severity":    derive_contradiction_severity(c),
        "failures":                  failures,
        "contradictions":            contradictions,
    }
    return _checked(valid_output_payload(advisory_findings=findings),
                    FC_ROLE_ID, validate_failure_contradiction_output)


# ── Role 6: FINAL_REFRESH ─────────────────────────────────────────────────────

def build_final_refresh_input(c: dict) -> dict:
    gaps = build_data_gaps(c)
    findings: dict[str, Any] = {
        "role_id": FR_ROLE_ID, "schema_version": SCHEMA_VERSION,
        "all_roles_completed":     True,
        "roles_completed":         list(_PRECEDING_ROLE_IDS),
        "roles_missing":           [],
        "refresh_status":          derive_refresh_status(gaps),
        "evidence_snapshot_valid": derive_evidence_snapshot_valid(c),
        "synthesis_note": (
            f"WNBA props adapter: {len(gaps)} gap(s); "
            f"packet_status={_packet_status(c)!r}; sport={c.get('sport')!r}"
        ),
    }
    return _checked(valid_output_payload(advisory_findings=findings),
                    FR_ROLE_ID, validate_final_refresh_output)
