"""
gate_engine/kalshi_wx_shadow_capability_boundary.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — Stage 1

Deterministic application capability boundary and deny-by-default tool-use
safety hooks for the Kalshi Weather shadow research agent.

Because the base anthropic SDK (v0.102.0) has no built-in hook framework,
these hooks are implemented as pure application logic that wraps every tool
call in the shadow tool-use loop.  They are NOT SDK framework features.

HOOK EXECUTION ORDER (enforced for every tool call in every subagent)
  PRE_TOOL_USE
    1. Verify the calling subagent_id is registered.
    2. Verify the requested tool is in THAT subagent's per-subagent allowlist.
       Deny-by-default: even another shadow tool is denied if not in the list.
    3. Scan the tool input for forbidden governance keys (recursive, any depth).
    → On denial: tool call is NEVER executed; PreHookResult(allowed=False) returned.

  POST_TOOL_USE
    1. Verify tool_output is a dict.
    2. Scan tool_output for forbidden governance keys.
    → On failure: PostHookResult(passed=False) returned; violation is logged but
      does not block collection (orchestrator decides run-level promotion).

PER-SUBAGENT ALLOWLIST — hardcoded, immutable at runtime
  Each subagent may call EXACTLY ONE designated tool.  No API exists to extend
  the allowlist.  Deny-by-default means cross-subagent tool calls are rejected
  even though the target tool is in ALL_ALLOWED_SHADOW_TOOLS.

INVARIANTS (not enforced here — enforced on KalshiWxShadowResearchClient)
  CAN_EXECUTE = False, PRODUCTION_AUTHORITY = False, USER_OUTPUT_AUTHORITY = False

OUT OF SCOPE — verified by tests
  No Flask routes, no DB imports, no scoring/market/settlement/label/execution
  logic is touched or imported.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Optional

from gate_engine.kalshi_wx_shadow_schema import FORBIDDEN_GOVERNANCE_KEYS

# ── Per-subagent tool allowlist ───────────────────────────────────────────────
# Hardcoded mapping: subagent_id → frozenset of tool names that subagent may call.
# This dict and its values are never mutated at runtime.
_SUBAGENT_TOOL_MAP: dict[str, frozenset] = {
    "forecast_context":        frozenset({"emit_forecast_context"}),
    "source_reconciliation":   frozenset({"emit_source_reconciliation"}),
    "contradiction_detection": frozenset({"emit_contradiction_detection"}),
    "unusual_regime":          frozenset({"emit_regime_assessment"}),
    "uncertainty_explanation": frozenset({"emit_uncertainty_summary"}),
}

# Flat union for reference.  A tool here is NOT automatically allowed for every
# subagent — it must appear in that subagent's per-subagent frozenset.
ALL_ALLOWED_SHADOW_TOOLS: frozenset[str] = frozenset().union(
    *_SUBAGENT_TOOL_MAP.values()
)

REGISTERED_SUBAGENT_IDS: frozenset[str] = frozenset(_SUBAGENT_TOOL_MAP.keys())


# ── Local forbidden-key scanner ───────────────────────────────────────────────
# Mirrors the private helper in kalshi_wx_shadow_schema.py without importing it.

def _scan_forbidden_keys_local(
    obj: Any,
    path: str,
) -> tuple[Optional[str], Optional[str]]:
    """
    Recursively scan obj for any key in FORBIDDEN_GOVERNANCE_KEYS.
    Returns (key, json_path) on first hit, (None, None) if clean.
    Descends into nested dicts and lists at any depth.
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in FORBIDDEN_GOVERNANCE_KEYS:
                return key, f"{path}.{key}"
            hit, hit_path = _scan_forbidden_keys_local(value, f"{path}.{key}")
            if hit is not None:
                return hit, hit_path
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            hit, hit_path = _scan_forbidden_keys_local(item, f"{path}[{i}]")
            if hit is not None:
                return hit, hit_path
    return None, None


# ── Result dataclasses ────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class PreHookResult:
    """
    Result of the pre-tool-use hook.

    allowed=True  → tool input is accepted for recording.
    allowed=False → tool call must NOT be executed; reason explains the denial.
    """
    allowed: bool
    reason: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class PostHookResult:
    """
    Result of the post-tool-use hook.

    passed=True  → output is clean.
    passed=False → forbidden key or type error found; reason explains it.
    """
    passed: bool
    reason: Optional[str] = None


# ── Capability boundary ───────────────────────────────────────────────────────

class CapabilityBoundary:
    """
    Deny-by-default application capability boundary for shadow research tools.

    Stateless — a single instance is safe to share across all subagent calls
    within one orchestrator run.  No side effects, no writes, no network calls.

    DENY-BY-DEFAULT CONTRACT
      Any tool_name not in the calling subagent's per-subagent frozenset
      is unconditionally denied — regardless of whether the tool name appears
      in ALL_ALLOWED_SHADOW_TOOLS or not.  There is no runtime override.
    """

    def pre_tool_use_hook(
        self,
        subagent_id: str,
        tool_name: str,
        tool_input: Any,
    ) -> PreHookResult:
        """
        Pre-tool-use gate.  Must be called BEFORE any tool input is accepted.

        Returns PreHookResult(allowed=True) on full pass.
        Returns PreHookResult(allowed=False, reason=...) on any denial.

        When allowed=False the caller must NOT execute the tool and must record
        the denial in the subagent's hook_violations list.
        """
        # ── Gate 1: registered subagent ───────────────────────────────────────
        allowed_tools = _SUBAGENT_TOOL_MAP.get(subagent_id)
        if allowed_tools is None:
            return PreHookResult(
                allowed=False,
                reason=(
                    f"UNKNOWN_SUBAGENT: {subagent_id!r} is not a registered "
                    f"subagent. Registered IDs: {sorted(REGISTERED_SUBAGENT_IDS)}"
                ),
            )

        # ── Gate 2: per-subagent allowlist (deny-by-default) ──────────────────
        if tool_name not in allowed_tools:
            return PreHookResult(
                allowed=False,
                reason=(
                    f"TOOL_NOT_ALLOWED: subagent {subagent_id!r} may not call "
                    f"tool {tool_name!r}. "
                    f"Allowed for this subagent: {sorted(allowed_tools)}. "
                    f"All shadow tools: {sorted(ALL_ALLOWED_SHADOW_TOOLS)}. "
                    f"Cross-subagent tool calls are denied even if the target tool "
                    f"is in the global allowlist."
                ),
            )

        # ── Gate 3: forbidden governance key scan in tool input ───────────────
        if isinstance(tool_input, dict):
            forbidden_key, forbidden_path = _scan_forbidden_keys_local(
                tool_input, "$.tool_input"
            )
            if forbidden_key is not None:
                return PreHookResult(
                    allowed=False,
                    reason=(
                        f"FORBIDDEN_KEY_IN_TOOL_INPUT: governance key "
                        f"{forbidden_key!r} found at {forbidden_path} in input "
                        f"to tool {tool_name!r}. Shadow tools must not assert or "
                        f"receive governance authority."
                    ),
                )

        return PreHookResult(allowed=True)

    def post_tool_use_hook(
        self,
        subagent_id: str,
        tool_name: str,
        tool_output: Any,
    ) -> PostHookResult:
        """
        Post-tool-use validation.  Called AFTER the tool input is accepted.

        Post-hook failure is recorded in hook_violations but does NOT
        immediately block the subagent run.  The orchestrator decides whether
        to promote any post-hook violation to a run-level failure.
        """
        if not isinstance(tool_output, dict):
            return PostHookResult(
                passed=False,
                reason=(
                    f"POST_HOOK_TYPE: tool_output for {tool_name!r} must be a "
                    f"dict; got {type(tool_output).__name__!r}"
                ),
            )

        forbidden_key, forbidden_path = _scan_forbidden_keys_local(
            tool_output, "$.tool_output"
        )
        if forbidden_key is not None:
            return PostHookResult(
                passed=False,
                reason=(
                    f"FORBIDDEN_KEY_IN_TOOL_OUTPUT: governance key "
                    f"{forbidden_key!r} at {forbidden_path} in output of "
                    f"{tool_name!r}"
                ),
            )

        return PostHookResult(passed=True)
