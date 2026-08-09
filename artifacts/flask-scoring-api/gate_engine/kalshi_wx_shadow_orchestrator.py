"""
gate_engine/kalshi_wx_shadow_orchestrator.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — Stage 3 (orchestrator)

Shadow orchestrator: runs the five read-only Kalshi Weather research subagents
in sequence, assembles their outputs into a schema-valid payload, validates
through the Step 9 closed schema validator, records to the shadow ledger, and
returns a ShadowValidationResult.

EXECUTION SEQUENCE
  1. forecast_context          (interprets forecast context)
  2. source_reconciliation     (reconciles data sources)
  3. contradiction_detection   (detects contradictions; may revise ceiling;
                                receives forecast_context + source_reconciliation)
  4. unusual_regime            (identifies unusual meteorological regimes)
  5. uncertainty_explanation   (synthesizes final uncertainty assessment;
                                receives forecast_context + contradiction +
                                unusual_regime)

FAILURE HANDLING
  If any subagent returns success=False, the orchestrator immediately builds a
  BLOCKED-status payload, validates it through the schema (BLOCKED is a valid
  status), records to the shadow ledger, and returns.  Remaining subagents are
  skipped.

PAYLOAD ASSEMBLY
  _assemble_payload() maps subagent tool_input dicts to the schema's root
  structure.  Fields not provided by a subagent fall back to safe defaults.
  The ceiling from contradiction_detection takes precedence over the ceiling
  from forecast_context.  If either ceiling is not in CEILING_CAPABLE_LABELS,
  it falls back to KALSHI_WATCH.

INVARIANTS
  - CAN_EXECUTE = False on the caller (KalshiWxShadowResearchClient)
  - advisory_only = True in every assembled payload
  - Forbidden governance keys are blocked by CapabilityBoundary hooks
  - All production weather behavior is unchanged — no import of app.py or
    any scoring/market/settlement/ceiling/execution module

OUT OF SCOPE
  No Flask routes, no DB writes from this module, no scoring/market/settlement
  logic, no changes to any existing ceiling resolver.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from gate_engine.kalshi_wx_shadow_capability_boundary import CapabilityBoundary
from gate_engine.kalshi_wx_shadow_snapshot import WeatherResearchSnapshot
from gate_engine.kalshi_wx_shadow_ledger import ShadowLedger
from gate_engine.kalshi_wx_shadow_schema import (
    SHADOW_PASS,
    ShadowValidationResult,
    validate_shadow_output,
)
from gate_engine.kalshi_wx_shadow_subagents import (
    SubagentResult,
    run_contradiction_detection_subagent,
    run_forecast_context_subagent,
    run_source_reconciliation_subagent,
    run_uncertainty_explanation_subagent,
    run_unusual_regime_subagent,
)

# ── Constants ─────────────────────────────────────────────────────────────────
_AGENT_ID: str = "kalshi-wx-shadow-research-agent-v1"

# CEILING_CAPABLE_LABELS — 6 approved terminal projection values
_CEILING_CAPABLE_LABELS: frozenset[str] = frozenset({
    "KALSHI_WATCH",
    "KALSHI_PLAYABLE_LIMIT_ONLY",
    "KALSHI_REJECT_NO_EDGE",
    "KALSHI_REJECT_UNCALIBRATED",
    "KALSHI_REJECT_BAD_RULES",
    "KALSHI_DATA_UNOBTAINABLE",
})
_DEFAULT_CEILING: str = "KALSHI_WATCH"


# ── SDK client builder ────────────────────────────────────────────────────────

def _build_sdk_client() -> Optional[Any]:
    """
    Build an Anthropic SDK client from environment variables.
    Returns None if the SDK is not installed or no API key is available.

    Resolution order:
      1. AI_INTEGRATIONS_ANTHROPIC_API_KEY + AI_INTEGRATIONS_ANTHROPIC_BASE_URL
         (Replit AI integrations proxy — preferred)
      2. ANTHROPIC_API_KEY (direct key)
    """
    try:
        import anthropic as _sdk
    except ImportError:
        return None

    api_key = (
        os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
    )
    if not api_key:
        return None

    base_url = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL")
    if base_url:
        return _sdk.Anthropic(api_key=api_key, base_url=base_url)
    return _sdk.Anthropic(api_key=api_key)


# ── Payload assembly ──────────────────────────────────────────────────────────

def _safe_ceiling(value: Any) -> str:
    """Return value if it's a valid ceiling label, else _DEFAULT_CEILING."""
    if isinstance(value, str) and value in _CEILING_CAPABLE_LABELS:
        return value
    return _DEFAULT_CEILING


def _assemble_payload(
    city: str,
    date: str,
    run_id: str,
    results: dict[str, SubagentResult],
) -> dict:
    """
    Assemble the final schema-valid payload from all 5 subagent results.

    Subagent tool_input dicts are the model's structured analysis outputs.
    Fields not provided default to safe values that pass validate_shadow_output.

    The ceiling precedence:
      contradiction_detection.revised_ceiling (if set and valid)
      → forecast_context.recommended_ceiling (if valid)
      → KALSHI_WATCH (fallback)
    """
    def _out(subagent_id: str) -> dict:
        r = results.get(subagent_id)
        return r.tool_input if (r and r.success and isinstance(r.tool_input, dict)) else {}

    fc_out = _out("forecast_context")
    sr_out = _out("source_reconciliation")
    cd_out = _out("contradiction_detection")
    ur_out = _out("unusual_regime")
    ue_out = _out("uncertainty_explanation")

    # ── Ceiling ───────────────────────────────────────────────────────────────
    ceiling = _safe_ceiling(cd_out.get("revised_ceiling")) \
              if cd_out.get("ceiling_impacted") and cd_out.get("revised_ceiling") \
              else _safe_ceiling(fc_out.get("recommended_ceiling"))

    # ── Blockers ──────────────────────────────────────────────────────────────
    blockers: list[str] = [str(b) for b in fc_out.get("blockers", [])]
    if ur_out.get("regime_unusual"):
        for factor in ur_out.get("regime_factors", []):
            s = str(factor)
            if s not in blockers:
                blockers.append(s)

    # ── Source conflicts ──────────────────────────────────────────────────────
    seen: set[str] = set()
    conflicts: list[str] = []
    for c in (
        list(sr_out.get("conflicts", []))
        + list(cd_out.get("contradictions_found", []))
    ):
        s = str(c)
        if s not in seen:
            seen.add(s)
            conflicts.append(s)

    # ── Uncertainty ───────────────────────────────────────────────────────────
    uncertainty_tier = (
        ue_out.get("uncertainty_tier")
        or fc_out.get("uncertainty_tier")
        or "HIGH"
    )
    uncertainty_obj: dict = {"uncertainty_tier": str(uncertainty_tier)}

    # Add numeric fields only if present and valid numbers
    for src_key, dst_key in (
        ("sigma_f_estimate",    "sigma_f"),
        ("horizon_hours_estimate", "horizon_hours"),
    ):
        v = ue_out.get(src_key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            uncertainty_obj[dst_key] = float(v)

    # ── Probabilities ─────────────────────────────────────────────────────────
    probabilities_obj: dict = {
        "calibration_status": str(fc_out.get("calibration_status", "UNAVAILABLE")),
    }

    # ── Facts ─────────────────────────────────────────────────────────────────
    facts_obj: dict = {
        "city": city,
        "date": date,
        "scoring_mode": str(fc_out.get("scoring_mode", "gaussian_forecast")),
    }

    return {
        "agent_id": _AGENT_ID,
        "run_id":   run_id,
        "lane":     "KALSHI_WEATHER",
        "status":   "COMPLETE",
        "facts":    facts_obj,
        "probabilities":        probabilities_obj,
        "uncertainty":          uncertainty_obj,
        "agent_observed_blockers": blockers,
        "source_conflicts":        conflicts,
        "recommended_ceiling":     ceiling,
        "advisory_only":           True,
    }


def _build_blocked_payload(
    city: str,
    date: str,
    run_id: str,
    failed_subagent_id: str,
) -> dict:
    """
    Build a schema-valid BLOCKED payload when a subagent fails.
    All required fields are present; status is "BLOCKED" (a valid schema status).
    """
    return {
        "agent_id": _AGENT_ID,
        "run_id":   run_id,
        "lane":     "KALSHI_WEATHER",
        "status":   "BLOCKED",
        "facts": {
            "city":         city,
            "date":         date,
            "scoring_mode": "gaussian_forecast",
        },
        "probabilities": {
            "calibration_status": "UNAVAILABLE",
        },
        "uncertainty": {
            "uncertainty_tier": "HIGH",
        },
        "agent_observed_blockers": [
            f"SUBAGENT_BLOCKED:{failed_subagent_id}",
        ],
        "source_conflicts":    [],
        "recommended_ceiling": _DEFAULT_CEILING,
        "advisory_only":       True,
    }


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_shadow_orchestrator(
    city: str,
    date: str,
    run_id: str,
    sdk_client: Any,
    capability_boundary: CapabilityBoundary,
    ledger: ShadowLedger,
    snapshot: Optional[WeatherResearchSnapshot] = None,
) -> ShadowValidationResult:
    """
    Run the full Kalshi Weather shadow research pipeline.

    Calls all 5 subagents in sequence, assembles the final payload, validates
    it through validate_shadow_output, records to the shadow ledger, and
    returns a ShadowValidationResult.

    Parameters
    ----------
    city               : City name for the weather market.
    date               : ISO-8601 date string (YYYY-MM-DD).
    run_id             : Caller-supplied run identifier.
    sdk_client         : Pre-built Anthropic client (never None — caller handles
                         the None case before calling this function).
    capability_boundary: CapabilityBoundary for hook enforcement.
    ledger             : ShadowLedger to record the run outcome.
    snapshot           : Immutable evidence snapshot for this run.  The SAME
                         instance is passed unchanged to all five subagents —
                         "one snapshot, same immutable evidence to every agent."
                         When None (default), subagents fall back to city/date
                         context-only messages for backward compatibility.

    Returns
    -------
    ShadowValidationResult — SHADOW_PASS if the assembled payload is schema-valid,
    or a shadow-failure-only result if a subagent fails or the payload is invalid.
    The result MUST NOT reach any production route or production log.
    """
    context = {"city": city, "date": date, "run_id": run_id}
    results: dict[str, SubagentResult] = {}
    all_violations: list[dict] = []

    def _run_and_check(
        subagent_id: str,
        run_fn,
        **kwargs,
    ) -> Optional[ShadowValidationResult]:
        """
        Run one subagent, accumulate results, and return a ShadowValidationResult
        if the subagent failed (caller should return it).  Returns None on success.
        """
        result = run_fn(
            client=sdk_client,
            context=context,
            capability_boundary=capability_boundary,
            snapshot=snapshot,  # same instance to every subagent — closure variable
            **kwargs,
        )
        results[subagent_id] = result
        all_violations.extend(result.hook_violations)

        if not result.success:
            payload = _build_blocked_payload(city, date, run_id, subagent_id)
            validation = validate_shadow_output(payload)
            ledger.record(run_id, city, date, "BLOCKED", results, all_violations)
            return validation

        return None  # success — continue

    # ── 1. Forecast context ───────────────────────────────────────────────────
    early = _run_and_check("forecast_context", run_forecast_context_subagent)
    if early is not None:
        return early

    # ── 2. Source reconciliation ──────────────────────────────────────────────
    early = _run_and_check("source_reconciliation", run_source_reconciliation_subagent)
    if early is not None:
        return early

    # ── 3. Contradiction detection (receives upstream results) ─────────────────
    early = _run_and_check(
        "contradiction_detection",
        run_contradiction_detection_subagent,
        forecast_context=results.get("forecast_context"),
        source_reconciliation=results.get("source_reconciliation"),
    )
    if early is not None:
        return early

    # ── 4. Unusual regime ─────────────────────────────────────────────────────
    early = _run_and_check("unusual_regime", run_unusual_regime_subagent)
    if early is not None:
        return early

    # ── 5. Uncertainty explanation (receives upstream results) ─────────────────
    early = _run_and_check(
        "uncertainty_explanation",
        run_uncertainty_explanation_subagent,
        forecast_context=results.get("forecast_context"),
        contradiction_detection=results.get("contradiction_detection"),
        unusual_regime=results.get("unusual_regime"),
    )
    if early is not None:
        return early

    # ── Assemble + validate ───────────────────────────────────────────────────
    payload = _assemble_payload(city, date, run_id, results)
    validation = validate_shadow_output(payload)
    status = "COMPLETE" if validation.passed else "SCHEMA_FAIL"
    ledger.record(run_id, city, date, status, results, all_violations)

    return validation
