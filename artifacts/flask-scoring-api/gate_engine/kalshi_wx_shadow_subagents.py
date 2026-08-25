"""
gate_engine/kalshi_wx_shadow_subagents.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — Stage 2

Five read-only Kalshi Weather shadow research subagents, each implemented as a
single-turn tool-use call to the Anthropic Messages API.

Each subagent:
  - Has ONE designated tool (structured output channel).
  - Has a read-only system prompt (no execution, no writes, no orders).
  - Runs pre-tool-use and post-tool-use hooks from CapabilityBoundary.
  - Returns a SubagentResult — either success (tool input captured) or failure
    (SDK error, hook denial, or no tool call produced).

TOOL-USE LOOP
  Uses tool_choice={"type":"tool","name":<tool_name>} which forces the model
  to call exactly that tool on the first turn.  The loop includes a max_turns
  guard for robustness.  "Execution" of each tool is simply recording the
  model's structured input — no external API call, no write, no side effect.

INVARIANTS
  - No tool in this module makes external API calls.
  - No tool writes to any table or file.
  - All tool inputs are passed through the CapabilityBoundary pre/post hooks.
  - SubagentResult.tool_input is always a plain dict (never a MagicMock,
    never a raw SDK object).

OUT OF SCOPE
  No Flask routes, no DB imports, no scoring/market/settlement/label/execution
  logic, no changes to production weather behavior.
"""
from __future__ import annotations

import dataclasses
import os
from typing import Any, Optional

from gate_engine.kalshi_wx_shadow_capability_boundary import (
    CapabilityBoundary,
)
from gate_engine.kalshi_wx_shadow_snapshot import WeatherResearchSnapshot
from gate_engine.kalshi_wx_terminal_labels import KALSHI_WX_TERMINAL_LABEL_REGISTRY

# ── Constants ─────────────────────────────────────────────────────────────────
_MODEL: str = "claude-haiku-4-5-20251001"
_MAX_TOKENS: int = 1024
_MAX_TURNS: int = 4   # guard; with forced tool_choice, normally 1 turn suffices

# Derived from the canonical registry (single source of truth).
# Do NOT add labels here — update gate_engine/kalshi_wx_terminal_labels.py.
# KALSHI_REJECT_UNCALIBRATED was removed 2026-08-09: dead code in the weather engine.
_VALID_CEILINGS: tuple[str, ...] = tuple(sorted(KALSHI_WX_TERMINAL_LABEL_REGISTRY))

_FORBIDDEN_KEYS_REMINDER = (
    "FORBIDDEN KEY NAMES — must NEVER appear in your tool input at any depth: "
    "terminal_label, final_label, label, can_execute, execute, capital_allocation, "
    "execution_permission, trade_authorization, governance_state, authorized, "
    "approved_for_execution."
)

_ADVISORY_REMINDER = (
    "You are ADVISORY ONLY. You cannot place orders, modify positions, write to any "
    "system, or call any execution endpoint. Output only via your designated tool."
)

# ── Research-call authorization gate (Gate B) ─────────────────────────────────
# Cached at module load time; patchable by tests via
#   patch("gate_engine.kalshi_wx_shadow_subagents._RESEARCH_API_ENABLED", True/False)
# This flag is INDEPENDENT of KALSHI_WX_SHADOW_AGENT_ENABLED (which gates
# snapshot capture) and completely independent of the CAN_EXECUTE /
# PRODUCTION_AUTHORITY / USER_OUTPUT_AUTHORITY constants in
# KalshiWxShadowResearchClient (those govern trading authority, not
# research-call authorization).
_RESEARCH_API_ENABLED: bool = (
    os.environ.get("SHADOW_RESEARCH_API_ENABLED", "false").strip().lower() == "true"
)


# ── Snapshot evidence formatter ───────────────────────────────────────────────

def _format_snapshot_evidence(snap: WeatherResearchSnapshot) -> str:
    """
    Render a WeatherResearchSnapshot as a structured evidence block for
    inclusion in a subagent user message.  All fields are evidence only.

    When snapshot is None (legacy path), each subagent falls back to the
    city/date/run_id-only message format to preserve backward compatibility
    with existing tests.
    """
    def _fmt_dict(d: Optional[dict], label: str) -> str:
        if d is None:
            return f"{label}: unavailable"
        if not d:
            return f"{label}: {{}}"
        lines = [f"{label}:"]
        for k, v in d.items():
            lines.append(f"  {k}={v!r}")
        return "\n".join(lines)

    failures_str = (
        "\n".join(f"  - {f}" for f in snap.source_failures)
        if snap.source_failures else "  (none)"
    )
    disagreements_str = (
        "\n".join(f"  - {d}" for d in snap.source_disagreements)
        if snap.source_disagreements else "  (none)"
    )

    return (
        f"research_snapshot_id={snap.research_snapshot_id!r}\n"
        f"canonical_event_id={snap.canonical_event_id!r}\n"
        f"city={snap.city!r}  station={snap.station!r}\n"
        f"market_date={snap.market_date!r}\n"
        f"source_cutoff_timestamp={snap.source_cutoff_timestamp!r}\n"
        f"\n"
        f"WEATHER EVIDENCE\n"
        f"forecast_high_used_by_deterministic_model="
        f"{snap.forecast_high_used_by_deterministic_model:.2f}\u00b0F\n"
        f"weather_data_source_tier={snap.weather_data_source_tier!r}\n"
        f"forecast_horizon_hours={snap.forecast_horizon_hours:.1f}h\n"
        f"sigma_f={snap.sigma_f:.3f}\u00b0F\n"
        f"deterministic_weather_readiness_state="
        f"{snap.deterministic_weather_readiness_state!r}\n"
        f"{_fmt_dict(snap.nws_gridpoint_forecast, 'nws_gridpoint_forecast')}\n"
        f"{_fmt_dict(snap.open_meteo_forecast, 'open_meteo_forecast')}\n"
        f"{_fmt_dict(snap.noaa_ncei_forecast, 'noaa_ncei_forecast')}\n"
        f"{_fmt_dict(snap.official_observations_at_cutoff, 'official_observations_at_cutoff')}\n"
        f"source_failures:\n{failures_str}\n"
        f"source_disagreements:\n{disagreements_str}"
    )


# ── SubagentResult ────────────────────────────────────────────────────────────

@dataclasses.dataclass
class SubagentResult:
    """
    Result of running a single shadow research subagent.

    success=True  → tool_input contains the model's structured analysis dict.
    success=False → tool_input is empty {}; failure_reason explains why.

    hook_violations is a list of dicts, each with keys:
      stage       : "pre" or "post"
      subagent_id : the subagent that triggered the violation
      tool_name   : the tool involved
      reason      : the hook's denial/failure reason string
    """
    subagent_id:    str
    tool_name:      str
    tool_input:     dict        # always a plain dict; {} on failure
    hook_violations: list       # list of violation dicts
    success:        bool
    failure_reason: Optional[str] = None
    turns_used:     int = 0
    # ── Usage accounting fields ───────────────────────────────────────────────
    # Populated from response.usage when the Anthropic SDK returns real token
    # counts.  UNAVAILABLE means the SDK response lacked parseable usage data;
    # None (not 0) is stored so callers can distinguish "unknown" from "zero".
    input_tokens:   Optional[int] = None
    output_tokens:  Optional[int] = None
    usage_accounting_status: str = "UNAVAILABLE"


# ── Generic single-tool subagent loop ─────────────────────────────────────────

def _run_single_tool_subagent(
    client: Any,
    subagent_id: str,
    tool_def: dict,
    system_prompt: str,
    user_message: str,
    capability_boundary: CapabilityBoundary,
    model: str = _MODEL,
    max_output_tokens: int = _MAX_TOKENS,
    max_turns: int = _MAX_TURNS,
) -> SubagentResult:
    """
    Run a single-tool subagent with pre- and post-tool-use hooks.

    With tool_choice forced to the subagent's one tool, the model calls that
    tool on the first turn.  The loop retries up to max_turns if no tool call
    appears (defensive guard only — should not be reached in production).

    The "execution" of the tool is recording its structured input dict.
    No external API call, no side effect occurs during execution.

    Returns SubagentResult with success=True and tool_input=<model's dict>
    on success, or success=False with a failure_reason on any error.
    """
    tool_name = tool_def["name"]
    messages: list[dict] = [{"role": "user", "content": user_message}]
    hook_violations: list[dict] = []

    # ── Gate B: SHADOW_RESEARCH_API_ENABLED ──────────────────────────────────
    # Defense-in-depth authorization gate — independent of Gate A in the pilot
    # runner (call_one_agent) and completely independent of the CAN_EXECUTE /
    # PRODUCTION_AUTHORITY / USER_OUTPUT_AUTHORITY constants in
    # KalshiWxShadowResearchClient (those govern trading authority, not
    # research-call authorization).  Set SHADOW_RESEARCH_API_ENABLED=true
    # explicitly to allow live messages.create() calls on this path.
    if not _RESEARCH_API_ENABLED:
        return SubagentResult(
            subagent_id=subagent_id,
            tool_name=tool_name,
            tool_input={},
            hook_violations=[],
            success=False,
            failure_reason=(
                "RESEARCH_API_DISABLED: SHADOW_RESEARCH_API_ENABLED is not set "
                "to 'true' — set it explicitly to enable live Anthropic API calls"
            ),
        )

    # ── Usage tracking across turns ───────────────────────────────────────────
    _total_in:  int  = 0
    _total_out: int  = 0
    _all_avail: bool = True

    for turn in range(max_turns):
        # ── SDK call ──────────────────────────────────────────────────────────
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_output_tokens,
                system=system_prompt,
                tools=[tool_def],
                messages=messages,
                tool_choice={"type": "tool", "name": tool_name},
            )
        except Exception as exc:
            return SubagentResult(
                subagent_id=subagent_id,
                tool_name=tool_name,
                tool_input={},
                hook_violations=hook_violations,
                success=False,
                failure_reason=f"SDK_ERROR: {type(exc).__name__}: {exc}",
                turns_used=turn + 1,
            )

        # ── Extract usage from this response ──────────────────────────────────
        # isinstance(int) guards prevent MagicMock values from silently reading
        # as AVAILABLE during unit tests that don't configure usage explicitly.
        try:
            _u  = getattr(response, "usage", None)
            _it = getattr(_u, "input_tokens",  None) if _u is not None else None
            _ot = getattr(_u, "output_tokens", None) if _u is not None else None
            if isinstance(_it, int) and isinstance(_ot, int):
                _total_in  += _it
                _total_out += _ot
            else:
                _all_avail = False
        except Exception:
            _all_avail = False

        # ── Extract tool_use block ────────────────────────────────────────────
        tool_uses = [
            b for b in response.content
            if getattr(b, "type", None) == "tool_use"
        ]

        if not tool_uses:
            # Model ended without calling a tool; add its response and retry
            if response.stop_reason == "end_turn":
                messages.append({"role": "assistant", "content": response.content})
                continue
            # Non-tool stop — break and fail
            return SubagentResult(
                subagent_id=subagent_id,
                tool_name=tool_name,
                tool_input={},
                hook_violations=hook_violations,
                success=False,
                failure_reason=(
                    f"NO_TOOL_CALL: model stop_reason={response.stop_reason!r} "
                    f"without calling tool on turn {turn + 1}"
                ),
                turns_used=turn + 1,
            )

        tool_use = tool_uses[0]
        called_name = getattr(tool_use, "name", "")
        raw_input = getattr(tool_use, "input", {})
        # Ensure plain dict (MagicMock safe)
        tool_input_dict = dict(raw_input) if isinstance(raw_input, dict) else {}

        # ── Pre-tool-use hook (deny-by-default) ───────────────────────────────
        pre = capability_boundary.pre_tool_use_hook(
            subagent_id, called_name, tool_input_dict
        )
        if not pre.allowed:
            hook_violations.append({
                "stage": "pre",
                "subagent_id": subagent_id,
                "tool_name": called_name,
                "reason": pre.reason,
            })
            return SubagentResult(
                subagent_id=subagent_id,
                tool_name=tool_name,
                tool_input={},
                hook_violations=hook_violations,
                success=False,
                failure_reason=f"PRE_HOOK_DENIED: {pre.reason}",
                turns_used=turn + 1,
            )

        # ── Post-tool-use hook ────────────────────────────────────────────────
        post = capability_boundary.post_tool_use_hook(
            subagent_id, called_name, tool_input_dict
        )
        if not post.passed:
            hook_violations.append({
                "stage": "post",
                "subagent_id": subagent_id,
                "tool_name": called_name,
                "reason": post.reason,
            })
            # Post-hook failure is recorded but does not block collection;
            # the orchestrator may promote it to a run-level failure if desired.

        # ── Native per-subagent closed-schema validation ──────────────────────
        # Runs AFTER CapabilityBoundary hooks.  Enforces additionalProperties=false
        # and full type/enum checks on the model's tool_input.  Failure is fatal:
        # the invalid output is discarded (tool_input={}) and NOT persisted.
        from gate_engine.kalshi_wx_shadow_native_schema import (  # noqa: PLC0415
            validate_subagent_output as _validate_native,
        )
        _ns_passed, _ns_reason = _validate_native(subagent_id, tool_input_dict)
        if not _ns_passed:
            return SubagentResult(
                subagent_id=subagent_id,
                tool_name=tool_name,
                tool_input={},
                hook_violations=hook_violations,
                success=False,
                failure_reason=f"NATIVE_SCHEMA_VIOLATION: {_ns_reason}",
                turns_used=turn + 1,
            )

        # ── Tool input accepted — build usage fields ──────────────────────────
        if _all_avail and (_total_in > 0 or _total_out > 0):
            _in_tok:    Optional[int] = _total_in
            _out_tok:   Optional[int] = _total_out
            _tok_status: str          = "AVAILABLE"
        else:
            _in_tok     = None
            _out_tok    = None
            _tok_status = "UNAVAILABLE"

        return SubagentResult(
            subagent_id=subagent_id,
            tool_name=called_name,
            tool_input=tool_input_dict,
            hook_violations=hook_violations,
            success=True,
            turns_used=turn + 1,
            input_tokens=_in_tok,
            output_tokens=_out_tok,
            usage_accounting_status=_tok_status,
        )

    # Exceeded max_turns without a tool call
    return SubagentResult(
        subagent_id=subagent_id,
        tool_name=tool_name,
        tool_input={},
        hook_violations=hook_violations,
        success=False,
        failure_reason=f"MAX_TURNS_EXCEEDED: {max_turns} turns without tool call",
        turns_used=max_turns,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Subagent 1: Forecast-Context Interpretation
# ═════════════════════════════════════════════════════════════════════════════

_FC_TOOL_DEF: dict = {
    "name": "emit_forecast_context",
    "description": (
        "Emit your structured forecast-context interpretation. "
        "Call this tool exactly once to record your analysis."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "scoring_mode": {
                "type": "string",
                "description": (
                    "Scoring mode for this market: 'gaussian_forecast' for a "
                    "future forecast horizon, or 'binary_final_cli' for a "
                    "finalized NWS CLI observation."
                ),
                "enum": ["gaussian_forecast", "binary_final_cli"],
            },
            "calibration_status": {
                "type": "string",
                "description": "Model calibration status.",
                "enum": ["CALIBRATED", "PROVISIONAL", "UNAVAILABLE"],
            },
            "uncertainty_tier": {
                "type": "string",
                "description": "Overall forecast uncertainty tier.",
                "enum": ["LOW", "MODERATE", "HIGH"],
            },
            "recommended_ceiling": {
                "type": "string",
                "description": "Recommended shadow ceiling from the approved set.",
                "enum": list(_VALID_CEILINGS),
            },
            "blockers": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Observed blockers or risk flags. Empty list [] if none. "
                    "Each entry is a short string label."
                ),
            },
            "notes": {
                "type": "string",
                "description": "Optional reasoning notes (max 400 chars).",
            },
        },
        "required": [
            "scoring_mode",
            "calibration_status",
            "uncertainty_tier",
            "recommended_ceiling",
            "blockers",
        ],
    },
}

_FC_SYSTEM_PROMPT: str = f"""\
You are a read-only Kalshi Weather shadow research agent — forecast-context specialist.

YOUR TASK
Interpret the forecast context for the Kalshi Weather temperature market described in
the user message.  Based on your meteorological knowledge of the city, date, and season,
assess the scoring mode, calibration status, uncertainty, and recommend a shadow ceiling.

{_ADVISORY_REMINDER}

CALL YOUR TOOL
Call the emit_forecast_context tool exactly once.  Do not produce any prose output.

{_FORBIDDEN_KEYS_REMINDER}
"""


def run_forecast_context_subagent(
    client: Any,
    context: dict,
    capability_boundary: CapabilityBoundary,
    snapshot: Optional[WeatherResearchSnapshot] = None,
    max_output_tokens: int = _MAX_TOKENS,
) -> SubagentResult:
    """
    Run the forecast-context interpretation subagent.

    Parameters
    ----------
    client             : Anthropic SDK client (or mock for tests).
    context            : Dict with keys: city, date, run_id, and optionally
                         any additional weather context the caller provides.
    capability_boundary: CapabilityBoundary instance for hook enforcement.
    snapshot           : Immutable evidence snapshot for this run.  When
                         provided, its fields replace the city/date-only message.
                         When None (default), falls back to context-only format
                         for backward compatibility.
    """
    run_id = context.get("run_id", "unknown")

    if snapshot is not None:
        evidence = _format_snapshot_evidence(snapshot)
        user_msg = (
            f"Shadow research run:\n{evidence}\n\n"
            f"Interpret the forecast context from the weather evidence above. "
            f"Assess the scoring mode (gaussian_forecast vs binary_final_cli based on "
            f"forecast_horizon_hours and deterministic_weather_readiness_state), "
            f"calibration status, uncertainty tier, and recommend a shadow ceiling. "
            f"Call emit_forecast_context with your structured analysis."
        )
    else:
        city  = context.get("city", "unknown")
        date  = context.get("date", "unknown")
        user_msg = (
            f"Shadow research run: run_id={run_id!r}\n"
            f"Market: Kalshi Weather temperature market\n"
            f"City: {city}\n"
            f"Date: {date}\n\n"
            f"Interpret the forecast context for this city and date. "
            f"Assess the scoring mode, calibration, uncertainty, and shadow ceiling. "
            f"Call emit_forecast_context with your structured analysis."
        )

    return _run_single_tool_subagent(
        client=client,
        subagent_id="forecast_context",
        tool_def=_FC_TOOL_DEF,
        system_prompt=_FC_SYSTEM_PROMPT,
        user_message=user_msg,
        capability_boundary=capability_boundary,
        max_output_tokens=max_output_tokens,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Subagent 2: Source Reconciliation
# ═════════════════════════════════════════════════════════════════════════════

_SR_TOOL_DEF: dict = {
    "name": "emit_source_reconciliation",
    "description": (
        "Emit your structured source reconciliation result. "
        "Call this tool exactly once."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sources_present": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Data sources that would typically be available for this market (e.g. ['nws_forecast', 'metar_obs']).",
            },
            "sources_missing": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Sources expected but not available. Empty [] if all present.",
            },
            "conflicts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Source-level conflicts detected. Empty [] if none.",
            },
            "reconciliation_status": {
                "type": "string",
                "description": "Overall source reconciliation outcome.",
                "enum": ["OK", "PARTIAL", "CONFLICT", "MISSING"],
            },
            "notes": {
                "type": "string",
                "description": "Optional reconciliation notes.",
            },
        },
        "required": [
            "sources_present",
            "sources_missing",
            "conflicts",
            "reconciliation_status",
        ],
    },
}

_SR_SYSTEM_PROMPT: str = f"""\
You are a read-only Kalshi Weather shadow research agent — source reconciliation specialist.

YOUR TASK
Assess which weather data sources would typically be present for the described market,
identify any that would be missing or conflicting, and emit a reconciliation summary.

{_ADVISORY_REMINDER}

CALL YOUR TOOL
Call the emit_source_reconciliation tool exactly once.

{_FORBIDDEN_KEYS_REMINDER}
"""


def run_source_reconciliation_subagent(
    client: Any,
    context: dict,
    capability_boundary: CapabilityBoundary,
    snapshot: Optional[WeatherResearchSnapshot] = None,
    max_output_tokens: int = _MAX_TOKENS,
) -> SubagentResult:
    """Run the source reconciliation subagent."""
    run_id = context.get("run_id", "unknown")

    if snapshot is not None:
        evidence = _format_snapshot_evidence(snapshot)
        user_msg = (
            f"Shadow research run:\n{evidence}\n\n"
            f"Assess which weather data sources are present vs missing for this market. "
            f"Use source_failures and source_disagreements from the evidence above. "
            f"Identify any source-level conflicts. "
            f"Emit a source reconciliation result via emit_source_reconciliation."
        )
    else:
        city   = context.get("city", "unknown")
        date   = context.get("date", "unknown")
        user_msg = (
            f"Shadow research run: run_id={run_id!r}\n"
            f"City: {city}  Date: {date}\n\n"
            f"Identify the expected weather data sources for this Kalshi Weather market, "
            f"assess which are present vs missing, note any source conflicts, "
            f"and emit a reconciliation result."
        )

    return _run_single_tool_subagent(
        client=client,
        subagent_id="source_reconciliation",
        tool_def=_SR_TOOL_DEF,
        system_prompt=_SR_SYSTEM_PROMPT,
        user_message=user_msg,
        capability_boundary=capability_boundary,
        max_output_tokens=max_output_tokens,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Subagent 3: Contradiction Detection
# ═════════════════════════════════════════════════════════════════════════════

_CD_TOOL_DEF: dict = {
    "name": "emit_contradiction_detection",
    "description": (
        "Emit your structured contradiction detection result. "
        "Call this tool exactly once."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "contradictions_found": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Contradictions detected between signals or sources. Empty [] if none.",
            },
            "ceiling_impacted": {
                "type": "boolean",
                "description": "True if any contradiction impacts the recommended ceiling.",
            },
            "revised_ceiling": {
                "type": "string",
                "description": (
                    "Revised ceiling if ceiling_impacted=true. "
                    "Must be one of the approved values. Omit if ceiling_impacted=false."
                ),
                "enum": list(_VALID_CEILINGS),
            },
            "notes": {
                "type": "string",
                "description": "Optional notes on the contradiction analysis.",
            },
        },
        "required": ["contradictions_found", "ceiling_impacted"],
    },
}

_CD_SYSTEM_PROMPT: str = f"""\
You are a read-only Kalshi Weather shadow research agent — contradiction detection specialist.

YOUR TASK
Given the forecast context and source reconciliation results, identify any contradictions
between signals or sources.  If a contradiction impacts the recommended ceiling, provide
a revised ceiling.

{_ADVISORY_REMINDER}

CALL YOUR TOOL
Call the emit_contradiction_detection tool exactly once.

{_FORBIDDEN_KEYS_REMINDER}
"""


def run_contradiction_detection_subagent(
    client: Any,
    context: dict,
    capability_boundary: CapabilityBoundary,
    forecast_context: Optional[SubagentResult] = None,
    source_reconciliation: Optional[SubagentResult] = None,
    snapshot: Optional[WeatherResearchSnapshot] = None,
    max_output_tokens: int = _MAX_TOKENS,
) -> SubagentResult:
    """Run the contradiction detection subagent."""
    run_id = context.get("run_id", "unknown")

    fc_summary = ""
    if forecast_context and forecast_context.success:
        fc_out = forecast_context.tool_input
        fc_summary = (
            f"\nForecast context: scoring_mode={fc_out.get('scoring_mode')!r}, "
            f"uncertainty={fc_out.get('uncertainty_tier')!r}, "
            f"ceiling={fc_out.get('recommended_ceiling')!r}"
        )

    sr_summary = ""
    if source_reconciliation and source_reconciliation.success:
        sr_out = source_reconciliation.tool_input
        sr_summary = (
            f"\nSource reconciliation: status={sr_out.get('reconciliation_status')!r}, "
            f"conflicts={sr_out.get('conflicts', [])!r}"
        )

    if snapshot is not None:
        evidence = _format_snapshot_evidence(snapshot)
        user_msg = (
            f"Shadow research run:\n{evidence}"
            f"{fc_summary}{sr_summary}\n\n"
            f"Detect any contradictions between signals or sources in the evidence above. "
            f"Pay attention to source_disagreements and any tension between the "
            f"forecast_context and source_reconciliation results. "
            f"Determine if any contradiction revises the ceiling. "
            f"Call emit_contradiction_detection with your result."
        )
    else:
        city   = context.get("city", "unknown")
        date   = context.get("date", "unknown")
        user_msg = (
            f"Shadow research run: run_id={run_id!r}\n"
            f"City: {city}  Date: {date}"
            f"{fc_summary}{sr_summary}\n\n"
            f"Detect any contradictions between signals or sources. "
            f"Determine if any contradiction revises the ceiling. "
            f"Call emit_contradiction_detection with your result."
        )

    return _run_single_tool_subagent(
        client=client,
        subagent_id="contradiction_detection",
        tool_def=_CD_TOOL_DEF,
        system_prompt=_CD_SYSTEM_PROMPT,
        user_message=user_msg,
        capability_boundary=capability_boundary,
        max_output_tokens=max_output_tokens,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Subagent 4: Unusual-Regime Identification
# ═════════════════════════════════════════════════════════════════════════════

_UR_TOOL_DEF: dict = {
    "name": "emit_regime_assessment",
    "description": (
        "Emit your unusual-regime identification result. "
        "Call this tool exactly once."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "regime_unusual": {
                "type": "boolean",
                "description": "True if the current meteorological regime is unusual for this city and season.",
            },
            "regime_factors": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Factors making the regime unusual. Empty [] if regime_unusual=false.",
            },
            "reliability_impact": {
                "type": "string",
                "description": "Impact of any unusual regime on forecast reliability.",
                "enum": ["NONE", "MINOR", "MODERATE", "SIGNIFICANT"],
            },
            "notes": {
                "type": "string",
                "description": "Optional notes on the regime assessment.",
            },
        },
        "required": ["regime_unusual", "regime_factors", "reliability_impact"],
    },
}

_UR_SYSTEM_PROMPT: str = f"""\
You are a read-only Kalshi Weather shadow research agent — unusual-regime identification specialist.

YOUR TASK
Assess whether the meteorological regime for this city and date is unusual relative to
climatological norms for the season.  Identify any factors that make it unusual and assess
their impact on forecast reliability.

{_ADVISORY_REMINDER}

CALL YOUR TOOL
Call the emit_regime_assessment tool exactly once.

{_FORBIDDEN_KEYS_REMINDER}
"""


def run_unusual_regime_subagent(
    client: Any,
    context: dict,
    capability_boundary: CapabilityBoundary,
    snapshot: Optional[WeatherResearchSnapshot] = None,
    max_output_tokens: int = _MAX_TOKENS,
) -> SubagentResult:
    """Run the unusual-regime identification subagent."""
    run_id = context.get("run_id", "unknown")

    if snapshot is not None:
        evidence = _format_snapshot_evidence(snapshot)
        user_msg = (
            f"Shadow research run:\n{evidence}\n\n"
            f"Assess whether the meteorological regime for this city, station, and date "
            f"is unusual relative to climatological norms for the season. "
            f"Consider the forecast_high, sigma_f, and forecast_horizon_hours from the "
            f"evidence above. Identify any factors that make it unusual and assess their "
            f"impact on forecast reliability. "
            f"Call emit_regime_assessment with your analysis."
        )
    else:
        city   = context.get("city", "unknown")
        date   = context.get("date", "unknown")
        user_msg = (
            f"Shadow research run: run_id={run_id!r}\n"
            f"City: {city}  Date: {date}\n\n"
            f"Assess the meteorological regime for this city and date. "
            f"Is it unusual for this season? What factors drive any unusualness? "
            f"How does it impact forecast reliability? "
            f"Call emit_regime_assessment with your analysis."
        )

    return _run_single_tool_subagent(
        client=client,
        subagent_id="unusual_regime",
        tool_def=_UR_TOOL_DEF,
        system_prompt=_UR_SYSTEM_PROMPT,
        user_message=user_msg,
        capability_boundary=capability_boundary,
        max_output_tokens=max_output_tokens,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Subagent 5: Uncertainty Explanation
# ═════════════════════════════════════════════════════════════════════════════

_UE_TOOL_DEF: dict = {
    "name": "emit_uncertainty_summary",
    "description": (
        "Emit your final uncertainty explanation. "
        "Call this tool exactly once."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "uncertainty_tier": {
                "type": "string",
                "description": "Final uncertainty tier after considering all upstream analysis.",
                "enum": ["LOW", "MODERATE", "HIGH"],
            },
            "uncertainty_sources": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Primary sources of uncertainty (e.g. ['forecast_horizon', 'model_spread']).",
            },
            "ceiling_impact": {
                "type": "string",
                "description": "How uncertainty affects the recommended ceiling.",
                "enum": ["NONE", "MINOR", "MODERATE", "SIGNIFICANT"],
            },
            "sigma_f_estimate": {
                "type": "number",
                "description": "Estimated forecast standard deviation in °F, if assessable. Omit if not.",
            },
            "horizon_hours_estimate": {
                "type": "number",
                "description": "Estimated forecast horizon in hours. Omit if not assessable.",
            },
            "notes": {
                "type": "string",
                "description": "Optional reasoning notes.",
            },
        },
        "required": ["uncertainty_tier", "uncertainty_sources", "ceiling_impact"],
    },
}

_UE_SYSTEM_PROMPT: str = f"""\
You are a read-only Kalshi Weather shadow research agent — uncertainty explanation specialist.

YOUR TASK
Synthesize the forecast context, contradiction detection, and unusual-regime assessment
to produce a final uncertainty explanation.  Identify the primary sources of forecast
uncertainty and their impact on the recommended ceiling.

{_ADVISORY_REMINDER}

CALL YOUR TOOL
Call the emit_uncertainty_summary tool exactly once.

{_FORBIDDEN_KEYS_REMINDER}
"""


def run_uncertainty_explanation_subagent(
    client: Any,
    context: dict,
    capability_boundary: CapabilityBoundary,
    forecast_context: Optional[SubagentResult] = None,
    contradiction_detection: Optional[SubagentResult] = None,
    unusual_regime: Optional[SubagentResult] = None,
    snapshot: Optional[WeatherResearchSnapshot] = None,
    max_output_tokens: int = _MAX_TOKENS,
) -> SubagentResult:
    """Run the uncertainty explanation subagent."""
    run_id = context.get("run_id", "unknown")

    fc_summary = ""
    if forecast_context and forecast_context.success:
        fc_out = forecast_context.tool_input
        fc_summary = (
            f"\nForecast context: uncertainty_tier={fc_out.get('uncertainty_tier')!r}, "
            f"calibration={fc_out.get('calibration_status')!r}"
        )

    cd_summary = ""
    if contradiction_detection and contradiction_detection.success:
        cd_out = contradiction_detection.tool_input
        cd_summary = (
            f"\nContradictions: {cd_out.get('contradictions_found', [])!r}, "
            f"ceiling_impacted={cd_out.get('ceiling_impacted')!r}"
        )

    ur_summary = ""
    if unusual_regime and unusual_regime.success:
        ur_out = unusual_regime.tool_input
        ur_summary = (
            f"\nRegime: unusual={ur_out.get('regime_unusual')!r}, "
            f"reliability_impact={ur_out.get('reliability_impact')!r}"
        )

    if snapshot is not None:
        evidence = _format_snapshot_evidence(snapshot)
        user_msg = (
            f"Shadow research run:\n{evidence}"
            f"{fc_summary}{cd_summary}{ur_summary}\n\n"
            f"Synthesize the weather evidence and upstream results above. "
            f"Use sigma_f={snapshot.sigma_f:.3f}°F and "
            f"forecast_horizon_hours={snapshot.forecast_horizon_hours:.1f}h as "
            f"the authoritative quantitative uncertainty inputs. "
            f"What are the primary sources of forecast uncertainty? "
            f"What is the final uncertainty tier? How does it impact the ceiling? "
            f"Call emit_uncertainty_summary with your final assessment."
        )
    else:
        city   = context.get("city", "unknown")
        date   = context.get("date", "unknown")
        user_msg = (
            f"Shadow research run: run_id={run_id!r}\n"
            f"City: {city}  Date: {date}"
            f"{fc_summary}{cd_summary}{ur_summary}\n\n"
            f"Synthesize the above results. What are the primary sources of forecast "
            f"uncertainty? What is the final uncertainty tier? How does it impact the ceiling? "
            f"Call emit_uncertainty_summary with your final assessment."
        )

    return _run_single_tool_subagent(
        client=client,
        subagent_id="uncertainty_explanation",
        tool_def=_UE_TOOL_DEF,
        system_prompt=_UE_SYSTEM_PROMPT,
        user_message=user_msg,
        capability_boundary=capability_boundary,
        max_output_tokens=max_output_tokens,
    )
