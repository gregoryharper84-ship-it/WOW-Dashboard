"""
gate_engine/universal_agent/lanes/mlb_moneyline/role_inputs.py
WOW-PATCH-2026-08-10-UNIVERSAL-AGENT-CORE-V1-B3A

Build and validate all six B1 advisory role payloads for an MLB moneyline row.

Each build_*_input() function:
  - Reads from the evidence row dict (read-only).
  - Applies deterministic field mapping via field_map.py.
  - Returns a validated payload dict (structure: {"advisory_findings": {...}}).
  - Raises RoleInputBuildError if the constructed payload fails B1 validation.
  - Never fabricates probability values or status strings.
  - Missing fields → explicit UNKNOWN / MISSING enum values.

The six role functions must be called in this order by the adapter:
  1. build_data_slate_integrity_input  (DATA_SLATE_INTEGRITY)
  2. build_news_status_input           (NEWS_STATUS)
  3. build_market_exact_line_input     (MARKET_EXACT_LINE)
  4. build_sport_specialist_input      (SPORT_SPECIALIST)
  5. build_failure_contradiction_input (FAILURE_CONTRADICTION)
  6. build_final_refresh_input         (FINAL_REFRESH — synthesises above)

can_execute = False
"""
from __future__ import annotations

from typing import Any

from gate_engine.universal_agent.output_contract import OUTPUT_VALID, valid_output_payload
from gate_engine.universal_agent.roles.role_base import SCHEMA_VERSION
from gate_engine.universal_agent.roles.data_slate_integrity import (
    ROLE_ID as DSI_ROLE_ID,
    validate_data_slate_integrity_output,
)
from gate_engine.universal_agent.roles.news_status import (
    ROLE_ID as NS_ROLE_ID,
    validate_news_status_output,
)
from gate_engine.universal_agent.roles.market_exact_line import (
    ROLE_ID as MEL_ROLE_ID,
    validate_market_exact_line_output,
)
from gate_engine.universal_agent.roles.sport_specialist import (
    ROLE_ID as SS_ROLE_ID,
    validate_sport_specialist_output,
)
from gate_engine.universal_agent.roles.failure_contradiction import (
    ROLE_ID as FC_ROLE_ID,
    validate_failure_contradiction_output,
)
from gate_engine.universal_agent.roles.final_refresh import (
    ROLE_ID as FR_ROLE_ID,
    validate_final_refresh_output,
)

from gate_engine.universal_agent.lanes.mlb_moneyline.field_map import (
    build_source_coverage,
    build_data_gaps,
    derive_data_freshness,
    derive_slate_consistency,
    map_starter_to_player_status,
    derive_market_status,
    derive_assessment_confidence,
    derive_contradiction_severity,
    derive_resolution_recommendation,
    derive_failure_detected,
    derive_contradiction_detected,
    derive_refresh_status,
    derive_evidence_snapshot_valid,
)

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

# Canonical preceding-role order for FINAL_REFRESH synthesis
_PRECEDING_ROLE_IDS: tuple[str, ...] = (
    DSI_ROLE_ID,
    NS_ROLE_ID,
    MEL_ROLE_ID,
    SS_ROLE_ID,
    FC_ROLE_ID,
)


class RoleInputBuildError(RuntimeError):
    """
    Raised when a constructed role payload fails B1 validation.

    This means the field_map derivation produced an invalid enum value or
    structural problem. Should not occur for well-formed rows, but is fail-closed:
    an invalid payload is rejected rather than passed to the orchestrator.

    Attributes
    ----------
    role_id  : str   — which role failed
    code     : str   — B1 violation code from OutputContractViolation
    message  : str   — human-readable explanation
    """
    def __init__(self, role_id: str, code: str, message: str) -> None:
        super().__init__(f"[{role_id}] {code}: {message}")
        self.role_id = role_id
        self.code    = code
        self.message = message


def _checked(payload: dict, role_id: str, validator: Any) -> dict:
    """
    Run B1 validator on payload. Raise RoleInputBuildError on failure.
    Returns payload unchanged on success.
    """
    result = validator(payload)
    if result is not OUTPUT_VALID:
        raise RoleInputBuildError(
            role_id=role_id,
            code=str(getattr(result, "code", "VALIDATION_FAILED")),
            message=str(getattr(result, "message", repr(result))),
        )
    return payload


# ── Role 1: DATA_SLATE_INTEGRITY ──────────────────────────────────────────────

def build_data_slate_integrity_input(row: dict) -> dict:
    """
    Build DATA_SLATE_INTEGRITY advisory payload from an MLB moneyline row.

    Captures data freshness status, slate consistency, per-source coverage,
    and an explicit list of any data gaps (missing evidence fields).
    """
    coverage = build_source_coverage(row)
    gaps     = build_data_gaps(row)
    stale    = [k for k, v in coverage.items() if v == "missing"]

    findings: dict[str, Any] = {
        "role_id":                 DSI_ROLE_ID,
        "schema_version":          SCHEMA_VERSION,
        "data_freshness_status":   derive_data_freshness(row),
        "slate_consistency_check": derive_slate_consistency(row),
        "source_coverage":         coverage,
        "data_gaps_identified":    gaps,
        "stale_sources":           stale,
    }
    return _checked(
        valid_output_payload(advisory_findings=findings),
        DSI_ROLE_ID,
        validate_data_slate_integrity_output,
    )


# ── Role 2: NEWS_STATUS ───────────────────────────────────────────────────────

def build_news_status_input(row: dict) -> dict:
    """
    Build NEWS_STATUS advisory payload from an MLB moneyline row.

    For a team-level moneyline market the most relevant "player" status is
    the starting pitcher's status — the single largest pre-game uncertainty.

    Mapping
    -------
    starter_status CONFIRMED / PROBABLE_STRONG → player_status = ACTIVE
    starter_status PROBABLE_ONLY               → player_status = QUESTIONABLE
    starter_status DOUBTFUL                    → player_status = DOUBTFUL
    starter_status SCRATCHED / OUT             → player_status = OUT
    starter_status None / unrecognised         → player_status = UNKNOWN

    injury_flag is True only when player_status = OUT (hard scratch).
    """
    starter       = row.get("starter_status")
    player_status = map_starter_to_player_status(starter)
    source        = str(row.get("starter_source") or "UNKNOWN")
    as_of         = str(
        row.get("starter_as_of") or row.get("pulled_at") or row.get("as_of") or "UNKNOWN"
    )
    injury_flag   = (player_status == "OUT")

    findings: dict[str, Any] = {
        "role_id":        NS_ROLE_ID,
        "schema_version": SCHEMA_VERSION,
        "player_status":  player_status,
        "status_source":  source,
        "status_as_of":   as_of,
        "injury_flag":    injury_flag,
    }
    return _checked(
        valid_output_payload(advisory_findings=findings),
        NS_ROLE_ID,
        validate_news_status_output,
    )


# ── Role 3: MARKET_EXACT_LINE ─────────────────────────────────────────────────

def build_market_exact_line_input(row: dict) -> dict:
    """
    Build MARKET_EXACT_LINE advisory payload from an MLB moneyline row.

    For a moneyline market (no spread):
      confirmed_line = sportsbook_no_vig_probability (implied win probability)
      over_odds      = candidate team's raw moneyline odds
      under_odds     = opponent team's raw moneyline odds
      line_confirmed = True iff sportsbook_no_vig_probability is present
    """
    no_vig         = row.get("sportsbook_no_vig_probability")
    line_confirmed = no_vig is not None
    source         = str(
        row.get("odds_source") or row.get("starter_source") or "UNKNOWN"
    )

    findings: dict[str, Any] = {
        "role_id":        MEL_ROLE_ID,
        "schema_version": SCHEMA_VERSION,
        "line_confirmed": line_confirmed,
        "line_source":    source,
        "market_status":  derive_market_status(row),
        "confirmed_line": float(no_vig) if no_vig is not None else None,
        "over_odds":      row.get("candidate_odds") or row.get("team_odds"),
        "under_odds":     row.get("opponent_odds"),
    }
    return _checked(
        valid_output_payload(advisory_findings=findings),
        MEL_ROLE_ID,
        validate_market_exact_line_output,
    )


# ── Role 4: SPORT_SPECIALIST ──────────────────────────────────────────────────

def build_sport_specialist_input(row: dict) -> dict:
    """
    Build SPORT_SPECIALIST advisory payload from an MLB moneyline row.

    statistical_assessment captures all available model/preflight metrics.
    Absent fields use the explicit sentinel string "MISSING" rather than
    being fabricated or omitted.

    key_metrics    — field names that ARE present (non-MISSING).
    missing_metrics — field names that could NOT be obtained (value="MISSING").
    """
    _s = lambda v: v if v is not None else "MISSING"  # noqa: E731

    assessment: dict[str, Any] = {
        "model_probability":                  _s(row.get("model_probability")),
        "calibrated_probability_lower_bound": _s(row.get("calibrated_probability_lower_bound")),
        "sportsbook_no_vig_probability":      _s(row.get("sportsbook_no_vig_probability")),
        "kalshi_breakeven_probability":       _s(row.get("kalshi_breakeven_probability")),
        "breakeven_gap":                      _s(row.get("breakeven_gap")),
        "starter_status":                     _s(row.get("starter_status")),
        "lineup_status":                      _s(row.get("lineup_status")),
        "event_status":                       _s(row.get("event_status")),
        "weather_status":                     _s(row.get("weather_status")),
        "preflight_status":                   _s(row.get("preflight_status")),
        "upgrade_allowed":                    _s(row.get("upgrade_allowed")),
    }

    key_metrics     = [k for k, v in assessment.items() if v != "MISSING"]
    missing_metrics = [k for k, v in assessment.items() if v == "MISSING"]

    findings: dict[str, Any] = {
        "role_id":                SS_ROLE_ID,
        "schema_version":         SCHEMA_VERSION,
        "sport":                  "MLB",
        "statistical_assessment": assessment,
        "key_metrics":            key_metrics,
        "missing_metrics":        missing_metrics,
        "assessment_confidence":  derive_assessment_confidence(row),
    }
    return _checked(
        valid_output_payload(advisory_findings=findings),
        SS_ROLE_ID,
        validate_sport_specialist_output,
    )


# ── Role 5: FAILURE_CONTRADICTION ─────────────────────────────────────────────

def build_failure_contradiction_input(row: dict) -> dict:
    """
    Build FAILURE_CONTRADICTION advisory payload from an MLB moneyline row.

    contradiction_detected — True when hard preflight blockers are present
                             (these represent evidence-level conflicts, e.g.
                             model probability below Kalshi breakeven, or
                             no-vig probability below breakeven).
    failure_detected       — True when any preflight blocker (hard or watch)
                             is present, or critical model fields are absent.
    failures               — one entry per preflight blocker, with severity.
    """
    blockers = list(row.get("preflight_blockers") or [])
    gate     = (row.get("gates") or {}).get("mlb_winner_preflight") or {}
    hard_set = set(gate.get("hard_blockers") or [])

    failures = [
        {
            "source":   "mlb_winner_preflight",
            "reason":   str(b),
            "severity": "HIGH" if b in hard_set else "LOW",
        }
        for b in blockers
    ]

    findings: dict[str, Any] = {
        "role_id":                   FC_ROLE_ID,
        "schema_version":            SCHEMA_VERSION,
        "contradiction_detected":    derive_contradiction_detected(row),
        "failure_detected":          derive_failure_detected(row),
        "resolution_recommendation": derive_resolution_recommendation(row),
        "contradiction_severity":    derive_contradiction_severity(row),
        "failures":                  failures,
        "contradictions":            [],
    }
    return _checked(
        valid_output_payload(advisory_findings=findings),
        FC_ROLE_ID,
        validate_failure_contradiction_output,
    )


# ── Role 6: FINAL_REFRESH ─────────────────────────────────────────────────────

def build_final_refresh_input(row: dict) -> dict:
    """
    Build FINAL_REFRESH advisory payload, synthesising the adapter's output.

    Since the MLB moneyline adapter always builds all five preceding role payloads
    deterministically, FINAL_REFRESH can confirm all_roles_completed=True and
    list all five as roles_completed. roles_missing is always [].

    refresh_status — COMPLETE if no evidence gaps; PARTIAL if gaps found.
    evidence_snapshot_valid — False only when preflight explicitly failed
                              (FAIL or FAIL_POSTPONEMENT).
    """
    data_gaps = build_data_gaps(row)

    findings: dict[str, Any] = {
        "role_id":                 FR_ROLE_ID,
        "schema_version":          SCHEMA_VERSION,
        "all_roles_completed":     True,
        "roles_completed":         list(_PRECEDING_ROLE_IDS),
        "roles_missing":           [],
        "refresh_status":          derive_refresh_status(data_gaps),
        "evidence_snapshot_valid": derive_evidence_snapshot_valid(row),
        "synthesis_note": (
            f"MLB moneyline adapter: {len(data_gaps)} evidence gap(s); "
            f"preflight_status={row.get('preflight_status')!r}"
        ),
    }
    return _checked(
        valid_output_payload(advisory_findings=findings),
        FR_ROLE_ID,
        validate_final_refresh_output,
    )
