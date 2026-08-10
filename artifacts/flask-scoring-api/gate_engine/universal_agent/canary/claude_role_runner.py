"""
gate_engine/universal_agent/canary/claude_role_runner.py
WOW-PATCH-2026-08-10-UNIVERSAL-AGENT-CORE-V1 / Phase B3C

ClaudeRoleRunner — live-Claude-backed role runner for the bounded canary.

INVARIANTS (enforced structurally, not by convention):
  can_execute    = False   — no wagers, orders, capital, trading, deployment
  advisory_only  = True    — always forced; never sourced from model output
  PINNED_MODEL   = "claude-haiku-4-5-20251001" — exact literal in every call
  MAX_TOKENS     = 1024    — uniform across all 6 roles, no per-role override
  AUTOMATIC_RETRIES = 0    — no retry on any failure mode
  AUTO_BUDGET_INCREASE = False — budget ceiling never raised

AUTHORIZED CALL PATH (ONE only):
  frozen MLB ML snapshot
  → existing B3A MlbMoneylineAdapter (via canary_pipeline.py)
  → ClaudeRoleRunner.__call__(entry, packet)     ← this file
  → REAL B0 _scan_forbidden_keys() (imported, not duplicated)
  → REAL B2 run_orchestrator() / B1 validators (called by orchestrator)
  → canonical evidence bundle

FORBIDDEN-KEY BEHAVIOR:
  Any of {can_execute, terminal_label, final_decision, stake_tier, is_playable,
  production_authority, user_output_authority, capital, deploy, execute} appearing
  at ANY nesting depth in the Claude response → OUTPUT_REJECTED, CanaryOutputRejectedError.
  Uses the real B0 _scan_forbidden_keys() — not a reimplementation.

FAIL-CLOSED MODES (all raise, no synthetic replacement, zero retries):
  Timeout            → CanaryCallFailedError("CALL_TIMEOUT")
  Network failure    → CanaryCallFailedError("CALL_NETWORK_ERROR")
  API error          → CanaryCallFailedError("CALL_API_ERROR")
  Wrong model        → CanaryModelIdentityError("CANARY_FAIL_MODEL_IDENTITY")
  Missing usage      → CanaryCallFailedError("MISSING_USAGE_METADATA")
  No tool_use block  → CanaryCallFailedError("MISSING_TOOL_USE")
  Malformed response → CanaryCallFailedError("MALFORMED_RESPONSE")
  Forbidden key      → CanaryOutputRejectedError("OUTPUT_REJECTED")
  Budget exceeded    → CanaryBudgetGuardError("STOP_CANARY_BUDGET_GUARD")

No app.py import. No Flask routes. No Weather/Kalshi imports.
No CAN_EXECUTE, SHADOW_ENABLED, or weather-lane references.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional

# ── B0 real imports (reuse, never duplicate) ──────────────────────────────────
# Tests must assert these are the SAME objects as the B0/B1/B2 definitions
# (object-identity or call-count checks via unittest.mock.patch).
from gate_engine.universal_agent.output_contract import (
    FORBIDDEN_GOVERNANCE_KEYS,   # noqa: F401 — re-exported for test assertIs
    _scan_forbidden_keys,        # THE real scanner; not reimplemented here
)
from gate_engine.universal_agent.roles.role_base import SCHEMA_VERSION

# ── Canary config (module-level constants) ────────────────────────────────────
from gate_engine.universal_agent.canary.canary_config import (
    AUTO_BUDGET_INCREASE,        # noqa: F401 — always False
    AUTOMATIC_RETRIES,           # noqa: F401 — always 0
    INPUT_COST_PER_MTOK,
    MAX_CALLS,
    MAX_TOKENS,
    MAX_TOTAL_SPEND_USD,
    OUTPUT_COST_PER_MTOK,
    PER_CALL_TIMEOUT_SECONDS,
    WORST_CASE_COST_PER_CALL,
    WORST_CASE_INPUT_TOKENS,
)

can_execute    = False
advisory_only  = True

# ── Pinned model literal ──────────────────────────────────────────────────────
# Defined HERE as the authoritative source for the actual API call.
# Not a forwarded constant — assigning this local name prevents callers from
# reassigning canary_config.PINNED_MODEL and silently changing the call.
_PINNED_MODEL: str = "claude-haiku-4-5-20251001"


# ── Canary-specific status codes ──────────────────────────────────────────────

class CanaryCallStatus:
    """B3C-specific status codes recorded in call_log and b3c_canary_runs."""
    SUCCESS                  = "CANARY_CALL_SUCCESS"
    FAIL_MODEL_IDENTITY      = "CANARY_FAIL_MODEL_IDENTITY"
    STOP_BUDGET_GUARD        = "STOP_CANARY_BUDGET_GUARD"
    CALL_TIMEOUT             = "CALL_TIMEOUT"
    CALL_NETWORK_ERROR       = "CALL_NETWORK_ERROR"
    CALL_API_ERROR           = "CALL_API_ERROR"
    OUTPUT_REJECTED          = "OUTPUT_REJECTED"
    MISSING_TOOL_USE         = "MISSING_TOOL_USE"
    MISSING_USAGE_METADATA   = "MISSING_USAGE_METADATA"
    MALFORMED_RESPONSE       = "MALFORMED_RESPONSE"
    NO_TOOL_DEFINITION       = "NO_TOOL_DEFINITION"
    VALIDATOR_EXCEPTION      = "VALIDATOR_EXCEPTION"
    CAPABILITY_DENIED        = "CAPABILITY_DENIED"


# ── Exception hierarchy ───────────────────────────────────────────────────────

class CanaryRunnerError(RuntimeError):
    """Base for all canary runner failures. Orchestrator catches as RUNNER_FAILED."""
    def __init__(self, status: str, detail: str = "") -> None:
        self.canary_status = status
        self.detail = detail
        super().__init__(f"{status}: {detail}" if detail else status)


class CanaryBudgetGuardError(CanaryRunnerError):
    """Budget guard tripped. No API call was made."""
    def __init__(self, detail: str = "") -> None:
        super().__init__(CanaryCallStatus.STOP_BUDGET_GUARD, detail)


class CanaryModelIdentityError(CanaryRunnerError):
    """response.model != PINNED_MODEL."""
    def __init__(self, detail: str = "") -> None:
        super().__init__(CanaryCallStatus.FAIL_MODEL_IDENTITY, detail)


class CanaryOutputRejectedError(CanaryRunnerError):
    """Forbidden governance key found in Claude output."""
    def __init__(self, detail: str = "") -> None:
        super().__init__(CanaryCallStatus.OUTPUT_REJECTED, detail)


class CanaryCallFailedError(CanaryRunnerError):
    """Any other call-level failure (timeout, network, malformed, etc.)."""
    def __init__(self, status: str, detail: str = "") -> None:
        super().__init__(status, detail)


# ── Per-call record ───────────────────────────────────────────────────────────

@dataclass
class CanaryCallRecord:
    """
    Mutable record for one Claude API call attempt.
    Appended to ClaudeRoleRunner.call_log regardless of success or failure.
    Merged with OrchestratorResult by canary_pipeline for b3c_canary_runs persistence.
    """
    role_id:                     str
    agent_id:                    str
    status:                      str           # CanaryCallStatus constant
    requested_model:             str           # always _PINNED_MODEL
    response_model:              Optional[str] = None
    request_timestamp:           Optional[datetime] = None
    completion_timestamp:        Optional[datetime] = None
    latency_ms:                  Optional[int] = None
    input_tokens:                Optional[int] = None
    output_tokens:               Optional[int] = None
    cache_read_input_tokens:     Optional[int] = None
    cache_creation_input_tokens: Optional[int] = None
    calculated_cost_usd:         Optional[float] = None
    cumulative_run_cost_usd:     Optional[float] = None  # filled by pipeline
    raw_output_hash:             Optional[str] = None
    canonical_output_hash:       Optional[str] = None
    violation_codes:             List[str] = field(default_factory=list)
    error_classification:        Optional[str] = None


# ── Budget state ──────────────────────────────────────────────────────────────

class BudgetState:
    """
    Mutable budget tracker shared across all role calls in one canary run.

    Pre-call contract:
      1. calls_attempted < MAX_CALLS (structural; no 7th call ever)
      2. cumulative_spend_usd + WORST_CASE_COST_PER_CALL < MAX_TOTAL_SPEND_USD

    Both checks must pass before an API call is attempted.
    calls_attempted increments at attempt time (before the call) so failures
    count toward the ceiling. calls_successful increments only on full success.
    """

    def __init__(self) -> None:
        self.calls_attempted:    int   = 0
        self.calls_successful:   int   = 0
        self.cumulative_spend_usd: float = 0.0

    def pre_call_check(self) -> tuple[bool, str]:
        """
        Returns (allowed: bool, denial_reason: str).
        Checks BOTH the call-count ceiling AND the spend ceiling.
        Does NOT mutate state — call record_attempt() after this passes.
        """
        # Structural call-count ceiling
        if self.calls_attempted >= MAX_CALLS:
            return False, (
                f"calls_attempted={self.calls_attempted} >= MAX_CALLS={MAX_CALLS}; "
                "no further calls structurally permitted"
            )
        # Spend ceiling (worst-case estimate for next call)
        projected = self.cumulative_spend_usd + WORST_CASE_COST_PER_CALL
        if projected >= MAX_TOTAL_SPEND_USD:
            return False, (
                f"cumulative_spend={self.cumulative_spend_usd:.6f} + "
                f"worst_case_next={WORST_CASE_COST_PER_CALL:.6f} = {projected:.6f} "
                f">= MAX_TOTAL_SPEND_USD={MAX_TOTAL_SPEND_USD}; "
                "budget guard engaged"
            )
        return True, ""

    def record_attempt(self) -> None:
        """
        Increment calls_attempted. Call AFTER pre_call_check() passes,
        BEFORE the actual API call, so that failures count toward the ceiling.
        """
        self.calls_attempted += 1

    def record_success(self, actual_cost_usd: float) -> None:
        """Update on a fully successful call with real token counts."""
        self.calls_successful += 1
        self.cumulative_spend_usd += actual_cost_usd

    def record_failure_cost(self, estimated_cost_usd: float = 0.0) -> None:
        """
        On a failed call, add the estimated/partial cost (conservative).
        If no usage data is available, pass 0.0 (caller's choice).
        """
        self.cumulative_spend_usd += estimated_cost_usd

    @property
    def worst_case_cost_per_call(self) -> float:
        return WORST_CASE_COST_PER_CALL


# ── Role tool definitions ─────────────────────────────────────────────────────
# One Anthropic tool definition per B1 role. Names match allowed_capabilities[0]
# from each B1 REGISTRY_ENTRY. Input schema describes advisory_findings content.
# Root output contract fields (agent_id, advisory_only, lane, etc.) are NOT
# included — the runner injects them after the tool call.
# role_id and schema_version are also injected by the runner (not from Claude).

_ROLE_TOOL_DEFINITIONS: dict[str, dict] = {
    "DATA_SLATE_INTEGRITY": {
        "name": "emit_data_slate_integrity",
        "description": (
            "Emit a Data/Slate Integrity advisory finding. "
            "Assess whether the evidence packet data is fresh, complete, and consistent."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "data_freshness_status": {
                    "type": "string",
                    "enum": ["FRESH", "STALE", "UNKNOWN", "MISSING"],
                },
                "slate_consistency_check": {
                    "type": "string",
                    "enum": ["CONSISTENT", "INCONSISTENT", "UNKNOWN"],
                },
                "source_coverage": {
                    "type": "object",
                    "description": "Map of source_key to availability status.",
                },
                "data_gaps_identified": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "stale_sources":      {"type": "array"},
                "timestamp_audit":    {"type": "object"},
                "integrity_confidence": {
                    "type": "string",
                    "enum": ["HIGH", "MEDIUM", "LOW", "UNKNOWN"],
                },
            },
            "required": [
                "data_freshness_status",
                "slate_consistency_check",
                "source_coverage",
                "data_gaps_identified",
            ],
        },
    },
    "NEWS_STATUS": {
        "name": "emit_news_status",
        "description": (
            "Emit a News/Status advisory finding. "
            "Assess current player or team status from available sources."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "player_status": {
                    "type": "string",
                    "enum": ["ACTIVE", "QUESTIONABLE", "DOUBTFUL", "OUT", "UNKNOWN", "MISSING"],
                },
                "status_source":  {"type": "string"},
                "status_as_of":   {"type": "string"},
                "injury_flag":    {"type": "boolean"},
                "news_items":     {"type": "array"},
                "status_confidence": {
                    "type": "string",
                    "enum": ["HIGH", "MEDIUM", "LOW", "UNKNOWN"],
                },
                "dnp_risk":       {"type": "boolean"},
            },
            "required": ["player_status", "status_source", "status_as_of", "injury_flag"],
        },
    },
    "MARKET_EXACT_LINE": {
        "name": "emit_market_exact_line",
        "description": (
            "Emit a Market/Exact-Line advisory finding. "
            "Confirm the current market line from live sportsbook data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "line_confirmed":  {"type": "boolean"},
                "line_source":     {"type": "string"},
                "market_status": {
                    "type": "string",
                    "enum": ["OPEN", "SUSPENDED", "CLOSED", "UNKNOWN"],
                },
                "confirmed_line":      {},
                "over_odds":           {},
                "under_odds":          {},
                "line_movement_note":  {"type": "string"},
                "line_confidence": {
                    "type": "string",
                    "enum": ["HIGH", "MEDIUM", "LOW", "UNKNOWN"],
                },
            },
            "required": ["line_confirmed", "line_source", "market_status"],
        },
    },
    "SPORT_SPECIALIST": {
        "name": "emit_sport_specialist",
        "description": (
            "Emit a Sport Specialist advisory finding. "
            "Provide statistical assessment for the sport and market."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sport":                {"type": "string"},
                "statistical_assessment": {"type": "object"},
                "key_metrics":          {"type": "array"},
                "missing_metrics":      {"type": "array"},
                "assessment_confidence": {
                    "type": "string",
                    "enum": ["HIGH", "MEDIUM", "LOW", "UNKNOWN"],
                },
                "model_inputs_used":    {"type": "object"},
            },
            "required": ["sport", "statistical_assessment", "key_metrics"],
        },
    },
    "FAILURE_CONTRADICTION": {
        "name": "emit_failure_contradiction",
        "description": (
            "Emit a Failure/Contradiction advisory finding. "
            "Detect conflicts or failures across other role findings."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "contradiction_detected": {"type": "boolean"},
                "failure_detected":       {"type": "boolean"},
                "resolution_recommendation": {
                    "type": "string",
                    "enum": ["PROCEED", "HOLD", "ABORT", "UNKNOWN"],
                },
                "contradictions":          {"type": "array"},
                "failures":                {"type": "array"},
                "contradiction_severity": {
                    "type": "string",
                    "enum": ["NONE", "LOW", "MEDIUM", "HIGH", "UNKNOWN"],
                },
            },
            "required": [
                "contradiction_detected",
                "failure_detected",
                "resolution_recommendation",
            ],
        },
    },
    "FINAL_REFRESH": {
        "name": "emit_final_refresh",
        "description": (
            "Emit a Final Refresh advisory finding. "
            "Synthesize all prior role outputs and issue a readiness signal."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "all_roles_completed":    {"type": "boolean"},
                "roles_completed":        {"type": "array", "items": {"type": "string"}},
                "roles_missing":          {"type": "array", "items": {"type": "string"}},
                "refresh_status": {
                    "type": "string",
                    "enum": ["COMPLETE", "PARTIAL", "FAILED", "UNKNOWN"],
                },
                "evidence_snapshot_valid": {"type": "boolean"},
                "synthesis_note":          {"type": "string"},
                "role_outputs_summary":    {"type": "object"},
            },
            "required": [
                "all_roles_completed",
                "roles_completed",
                "roles_missing",
                "refresh_status",
                "evidence_snapshot_valid",
            ],
        },
    },
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sha256_json(obj: Any) -> str:
    """SHA-256 hex digest of deterministic JSON serialization."""
    serialized = json.dumps(obj, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


def _build_prompt(role_id: str, packet: Any) -> str:
    """
    Build a minimal advisory prompt for one role call.
    Uses real EvidencePacket attributes (to_dict() when available, otherwise
    falls back to direct attribute access with getattr defaults).
    Does NOT include any governance language or terminal-label instructions.
    """
    try:
        if hasattr(packet, "to_dict"):
            pkt_data = packet.to_dict()
        else:
            # Fallback for mock packets in tests
            pkt_data = {
                "event_name":         getattr(packet, "event_name", None),
                "event_date":         getattr(packet, "event_date", None),
                "team_name":          getattr(packet, "team_name", None),
                "opponent_team_name": getattr(packet, "opponent_team_name", None),
                "market_snapshot":    getattr(packet, "market_snapshot", {}),
                "deterministic_model_inputs":
                    getattr(packet, "deterministic_model_inputs", {}),
            }
        # Keep prompt lean: only include non-None, non-empty values
        ctx_data = {
            k: v for k, v in pkt_data.items()
            if v is not None and v != {} and v != []
            and k not in ("run_id", "snapshot_id", "created_at", "source_provenance")
        }
        ctx = json.dumps(ctx_data, default=str, indent=2)
    except Exception:  # noqa: BLE001
        ctx = "{}"

    canonical_event_id = getattr(packet, "canonical_event_id", "UNKNOWN")
    lane               = getattr(packet, "lane",               "UNKNOWN")
    event_date         = getattr(packet, "event_date",         "UNKNOWN")

    return (
        f"You are an advisory analyst for role: {role_id}.\n"
        f"Event ID: {canonical_event_id}\n"
        f"Lane: {lane}\n"
        f"Date: {event_date}\n\n"
        f"Evidence context:\n{ctx}\n\n"
        f"Use the tool to emit your advisory finding. "
        f"Your finding is ADVISORY ONLY — it does not affect any decision."
    )


# ── ClaudeRoleRunner ──────────────────────────────────────────────────────────

class ClaudeRoleRunner:
    """
    Live-Claude-backed role runner for the B3C bounded canary.

    One instance handles ALL six B1 roles for a single canary run.
    Dispatches to the correct tool definition by entry.role (role_id).

    The SAME instance must be shared across all 6 role calls so that
    BudgetState accumulates correctly across the full run.

    Callable interface (matches B2 orchestrator role_runner contract):
        runner(entry, packet) -> dict

    On any failure: raises a CanaryRunnerError subclass (fail-closed).
    The orchestrator catches this as RUNNER_FAILED.
    Call metadata is always recorded in self.call_log regardless of outcome.

    Parameters
    ----------
    client
        Anthropic client instance (real or mocked). Lazy import of the
        Anthropic SDK occurs only inside __call__ — not at module load time.
        Pass a mock client to prevent any real API calls.
    budget
        Shared BudgetState instance. Must be the same object for all 6 calls.
        Pre-call check runs BEFORE record_attempt(), so budget guard cannot
        be bypassed by concurrent calls.

    can_execute = False — no live trading, no market mutations, no capital.
    """

    can_execute    = False
    advisory_only  = True
    PINNED_MODEL   = _PINNED_MODEL  # exposed for test assertIs checks
    MAX_TOKENS     = MAX_TOKENS
    AUTOMATIC_RETRIES = 0  # re-declared locally; not configurable

    def __init__(
        self,
        client: Any,
        budget: Optional[BudgetState] = None,
    ) -> None:
        self._client = client
        self._budget = budget if budget is not None else BudgetState()
        self.call_log: List[CanaryCallRecord] = []

    def __call__(self, entry: Any, packet: Any) -> dict:
        """
        Execute one advisory role call against Claude.

        Parameters
        ----------
        entry   AgentRegistryEntry — contains entry.role (role_id) and entry.agent_id
        packet  EvidencePacket — immutable scoring context

        Returns
        -------
        dict — full output contract payload ready for B1/B2 validation.

        Raises
        ------
        CanaryBudgetGuardError      — budget ceiling exceeded before call
        CanaryModelIdentityError    — response.model != PINNED_MODEL
        CanaryOutputRejectedError   — forbidden governance key in Claude output
        CanaryCallFailedError       — all other fail-closed modes
        """
        role_id  = entry.role
        agent_id = entry.agent_id

        # ── 1. Budget guard — checked BEFORE any API call ─────────────────────
        ok, denial_reason = self._budget.pre_call_check()
        if not ok:
            rec = CanaryCallRecord(
                role_id=role_id,
                agent_id=agent_id,
                status=CanaryCallStatus.STOP_BUDGET_GUARD,
                requested_model=_PINNED_MODEL,
                error_classification=CanaryCallStatus.STOP_BUDGET_GUARD,
            )
            self.call_log.append(rec)
            raise CanaryBudgetGuardError(denial_reason)

        # ── 2. Record attempt (increment BEFORE the call) ─────────────────────
        self._budget.record_attempt()
        request_ts = datetime.now(timezone.utc)

        # ── 3. Select tool definition ──────────────────────────────────────────
        tool_def = _ROLE_TOOL_DEFINITIONS.get(role_id)
        if tool_def is None:
            rec = CanaryCallRecord(
                role_id=role_id,
                agent_id=agent_id,
                status=CanaryCallStatus.NO_TOOL_DEFINITION,
                requested_model=_PINNED_MODEL,
                request_timestamp=request_ts,
                error_classification=CanaryCallStatus.NO_TOOL_DEFINITION,
            )
            self.call_log.append(rec)
            raise CanaryCallFailedError(
                CanaryCallStatus.NO_TOOL_DEFINITION,
                f"No tool definition for role_id={role_id!r}",
            )

        # ── 4. Make the API call ───────────────────────────────────────────────
        try:
            prompt = _build_prompt(role_id, packet)
        except Exception as exc:  # noqa: BLE001
            # Prompt-building failure is unusual but must be recorded.
            rec = CanaryCallRecord(
                role_id=role_id,
                agent_id=agent_id,
                status=CanaryCallStatus.CALL_API_ERROR,
                requested_model=_PINNED_MODEL,
                request_timestamp=request_ts,
                error_classification=f"PROMPT_BUILD_ERROR: {exc}",
            )
            self.call_log.append(rec)
            self._budget.record_failure_cost(0.0)
            raise CanaryCallFailedError(CanaryCallStatus.CALL_API_ERROR, f"prompt build failed: {exc}") from exc

        t0 = time.monotonic()
        try:
            response = self._client.messages.create(
                model=_PINNED_MODEL,          # exact literal — not a variable
                max_tokens=MAX_TOKENS,        # 1024, uniform, not configurable
                tools=[tool_def],
                tool_choice={"type": "tool", "name": tool_def["name"]},
                messages=[{"role": "user", "content": prompt}],
                timeout=PER_CALL_TIMEOUT_SECONDS,  # 30.0s
            )
        except Exception as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            completion_ts = datetime.now(timezone.utc)
            # Classify the error by exception type name (lazy — no anthropic import at top)
            exc_type = type(exc).__name__
            if "Timeout" in exc_type or "timeout" in str(exc).lower():
                status = CanaryCallStatus.CALL_TIMEOUT
            elif "Connection" in exc_type or "Network" in exc_type:
                status = CanaryCallStatus.CALL_NETWORK_ERROR
            else:
                status = CanaryCallStatus.CALL_API_ERROR
            rec = CanaryCallRecord(
                role_id=role_id,
                agent_id=agent_id,
                status=status,
                requested_model=_PINNED_MODEL,
                request_timestamp=request_ts,
                completion_timestamp=completion_ts,
                latency_ms=latency_ms,
                error_classification=f"{exc_type}: {exc}",
            )
            self.call_log.append(rec)
            self._budget.record_failure_cost(0.0)  # no usage data
            raise CanaryCallFailedError(status, str(exc)) from exc

        latency_ms = int((time.monotonic() - t0) * 1000)
        completion_ts = datetime.now(timezone.utc)

        # ── 5. Validate response structure ─────────────────────────────────────
        if not hasattr(response, "content") or not hasattr(response, "usage"):
            rec = CanaryCallRecord(
                role_id=role_id,
                agent_id=agent_id,
                status=CanaryCallStatus.MALFORMED_RESPONSE,
                requested_model=_PINNED_MODEL,
                request_timestamp=request_ts,
                completion_timestamp=completion_ts,
                latency_ms=latency_ms,
                error_classification="response missing content or usage attribute",
            )
            self.call_log.append(rec)
            self._budget.record_failure_cost(0.0)
            raise CanaryCallFailedError(
                CanaryCallStatus.MALFORMED_RESPONSE,
                "response missing .content or .usage",
            )

        # ── 6. Usage metadata — fail-closed if missing ─────────────────────────
        usage = response.usage
        if usage is None:
            rec = CanaryCallRecord(
                role_id=role_id,
                agent_id=agent_id,
                status=CanaryCallStatus.MISSING_USAGE_METADATA,
                requested_model=_PINNED_MODEL,
                request_timestamp=request_ts,
                completion_timestamp=completion_ts,
                latency_ms=latency_ms,
                error_classification="response.usage is None",
            )
            self.call_log.append(rec)
            self._budget.record_failure_cost(0.0)
            raise CanaryCallFailedError(
                CanaryCallStatus.MISSING_USAGE_METADATA,
                "response.usage is None",
            )

        input_tokens  = getattr(usage, "input_tokens",  None)
        output_tokens = getattr(usage, "output_tokens", None)

        if input_tokens is None or output_tokens is None:
            rec = CanaryCallRecord(
                role_id=role_id,
                agent_id=agent_id,
                status=CanaryCallStatus.MISSING_USAGE_METADATA,
                requested_model=_PINNED_MODEL,
                request_timestamp=request_ts,
                completion_timestamp=completion_ts,
                latency_ms=latency_ms,
                error_classification="response.usage missing input_tokens or output_tokens",
            )
            self.call_log.append(rec)
            self._budget.record_failure_cost(0.0)
            raise CanaryCallFailedError(
                CanaryCallStatus.MISSING_USAGE_METADATA,
                "response.usage missing input_tokens or output_tokens",
            )

        # ── 7. Model identity assertion — both requested AND response must match ─
        response_model = getattr(response, "model", None)
        if response_model != _PINNED_MODEL:
            rec = CanaryCallRecord(
                role_id=role_id,
                agent_id=agent_id,
                status=CanaryCallStatus.FAIL_MODEL_IDENTITY,
                requested_model=_PINNED_MODEL,
                response_model=response_model,
                request_timestamp=request_ts,
                completion_timestamp=completion_ts,
                latency_ms=latency_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                error_classification=(
                    f"model_identity_mismatch: "
                    f"requested={_PINNED_MODEL!r} response={response_model!r}"
                ),
            )
            self.call_log.append(rec)
            # Charge conservative cost (we did spend tokens)
            actual_cost = (
                input_tokens / 1_000_000 * INPUT_COST_PER_MTOK
                + output_tokens / 1_000_000 * OUTPUT_COST_PER_MTOK
            )
            self._budget.record_failure_cost(actual_cost)
            raise CanaryModelIdentityError(
                f"requested={_PINNED_MODEL!r} but response.model={response_model!r}"
            )

        # ── 8. Parse tool_use block from response.content ─────────────────────
        tool_input: Optional[dict] = None
        try:
            for block in response.content:
                if getattr(block, "type", None) == "tool_use":
                    tool_input = block.input
                    break
        except Exception as exc:
            rec = CanaryCallRecord(
                role_id=role_id,
                agent_id=agent_id,
                status=CanaryCallStatus.MALFORMED_RESPONSE,
                requested_model=_PINNED_MODEL,
                response_model=response_model,
                request_timestamp=request_ts,
                completion_timestamp=completion_ts,
                latency_ms=latency_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                error_classification=f"content parse error: {exc}",
            )
            self.call_log.append(rec)
            actual_cost = (
                input_tokens / 1_000_000 * INPUT_COST_PER_MTOK
                + output_tokens / 1_000_000 * OUTPUT_COST_PER_MTOK
            )
            self._budget.record_failure_cost(actual_cost)
            raise CanaryCallFailedError(
                CanaryCallStatus.MALFORMED_RESPONSE, str(exc)
            ) from exc

        if tool_input is None or not isinstance(tool_input, dict):
            rec = CanaryCallRecord(
                role_id=role_id,
                agent_id=agent_id,
                status=CanaryCallStatus.MISSING_TOOL_USE,
                requested_model=_PINNED_MODEL,
                response_model=response_model,
                request_timestamp=request_ts,
                completion_timestamp=completion_ts,
                latency_ms=latency_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                error_classification=(
                    "no tool_use block in response.content"
                    if tool_input is None
                    else f"tool_input type={type(tool_input).__name__}"
                ),
            )
            self.call_log.append(rec)
            actual_cost = (
                input_tokens / 1_000_000 * INPUT_COST_PER_MTOK
                + output_tokens / 1_000_000 * OUTPUT_COST_PER_MTOK
            )
            self._budget.record_failure_cost(actual_cost)
            raise CanaryCallFailedError(
                CanaryCallStatus.MISSING_TOOL_USE,
                "no tool_use block with dict input in response.content",
            )

        # ── 9. Forbidden-key scan — THE REAL B0 scanner (not reimplemented) ───
        # _scan_forbidden_keys is imported from output_contract at the top of
        # this module. Tests must verify this is the SAME function object as B0's
        # via unittest.mock.patch at gate_engine.universal_agent.canary.claude_role_runner._scan_forbidden_keys.
        violation = _scan_forbidden_keys(tool_input, path="claude_tool_output")
        if violation is not None:
            rec = CanaryCallRecord(
                role_id=role_id,
                agent_id=agent_id,
                status=CanaryCallStatus.OUTPUT_REJECTED,
                requested_model=_PINNED_MODEL,
                response_model=response_model,
                request_timestamp=request_ts,
                completion_timestamp=completion_ts,
                latency_ms=latency_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                raw_output_hash=_sha256_json(tool_input),
                violation_codes=[violation.code],
                error_classification=f"OUTPUT_REJECTED: {violation.message}",
            )
            self.call_log.append(rec)
            actual_cost = (
                input_tokens / 1_000_000 * INPUT_COST_PER_MTOK
                + output_tokens / 1_000_000 * OUTPUT_COST_PER_MTOK
            )
            self._budget.record_failure_cost(actual_cost)
            raise CanaryOutputRejectedError(
                f"{violation.code} at {violation.path}: {violation.message}"
            )

        # ── 10. Inject common fields and build full output contract payload ────
        # advisory_only=True is ALWAYS injected here — never sourced from Claude.
        findings = dict(tool_input)
        findings.setdefault("role_id",        role_id)
        findings.setdefault("schema_version", SCHEMA_VERSION)

        payload: dict = {
            "agent_id":         agent_id,
            "advisory_only":    True,          # FORCED — never from Claude
            "lane":             packet.lane,
            "snapshot_id":      packet.snapshot_id,
            "run_id":           packet.run_id,
            "advisory_findings": findings,
            "model_id":         _PINNED_MODEL,
            "input_tokens":     input_tokens,
            "output_tokens":    output_tokens,
        }

        # ── 11. Calculate cost and update budget ───────────────────────────────
        actual_cost = (
            input_tokens  / 1_000_000 * INPUT_COST_PER_MTOK
            + output_tokens / 1_000_000 * OUTPUT_COST_PER_MTOK
        )
        self._budget.record_success(actual_cost)

        # ── 12. Record call in call_log ────────────────────────────────────────
        raw_hash = _sha256_json(tool_input)
        rec = CanaryCallRecord(
            role_id=role_id,
            agent_id=agent_id,
            status=CanaryCallStatus.SUCCESS,
            requested_model=_PINNED_MODEL,
            response_model=response_model,
            request_timestamp=request_ts,
            completion_timestamp=completion_ts,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", None),
            cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", None),
            calculated_cost_usd=actual_cost,
            raw_output_hash=raw_hash,
            violation_codes=[],
            error_classification=None,
        )
        self.call_log.append(rec)

        return payload
