---
name: Kalshi WX shadow pilot — Stages 1–3 architecture
description: Capability boundary hooks, 5 subagents, orchestrator, in-process ledger; 179 tests pass.
---

## Rule
Stages 1–3 of WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW are complete and all 179 shadow tests pass.

**Stage 1** — `gate_engine/kalshi_wx_shadow_capability_boundary.py`
- `CapabilityBoundary`: deny-by-default per-subagent tool allowlist (5 subagents × 1 tool each)
- `pre_tool_use_hook` + `post_tool_use_hook`: recursive forbidden-key scan; pre-hook is fail-closed, post-hook is non-blocking (records violation, never returns success=False from the subagent loop)

**Stage 2** — `gate_engine/kalshi_wx_shadow_subagents.py`
- `SubagentResult` dataclass; `_run_single_tool_subagent()` generic loop using `tool_choice={"type":"tool","name":...}` to force single-tool calls
- 5 public `run_*_subagent()` functions (forecast_context, source_reconciliation, contradiction_detection, unusual_regime, uncertainty_explanation)
- Tools are structured-output channels — no external API calls; "executing" = capturing model's input dict

**Stage 3** — `gate_engine/kalshi_wx_shadow_orchestrator.py`, `gate_engine/kalshi_wx_shadow_ledger.py`
- `run_shadow_orchestrator()`: 5 subagents sequentially; upstream results passed downstream; contradiction `revised_ceiling` overrides forecast_context ceiling; `_build_blocked_payload()` produces schema-valid BLOCKED payloads on subagent failure
- `ShadowLedger`: thread-safe bounded deque (default 500), no DB imports, module-level singleton via `get_default_ledger()`
- `kalshi_wx_shadow_client.py` `research()` now has 4 gates: flag → authority → SDK client → orchestrator delegation

**Why:**
Shadow pilot must run without any production authority; feature flag is `False` by default; all authority constants hardcoded False on the class.

**How to apply:**
When adding new shadow subagents or stages, route through the existing `CapabilityBoundary` and `run_shadow_orchestrator` pattern; do not add new authority flags.
