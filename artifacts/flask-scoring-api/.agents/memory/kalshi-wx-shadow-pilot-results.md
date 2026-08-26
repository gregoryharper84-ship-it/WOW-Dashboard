---
name: Kalshi WX shadow pilot trial results and BLOCKED-SHADOW-PASS pattern
description: Results of the 25-trial shadow pilot and key behavioral invariants discovered during trials.
pilot_status: VALIDATED_COMPLETE
step15_ruling: APPROVED_CLOSED
total_real_spend: $0.410970
closure_step: 16
---

## Pilot status: VALIDATED_COMPLETE

Step 15 ChatGPT final ruling: **APPROVED_CLOSED**  
Total real spend: **$0.410970** (125 rows Step 14B + 5 rows Step 14C canary)  
Full step history and spend breakdown: `pilot_audit/kalshi_wx_shadow_pilot_status_tracker.md`  
Closure record: `pilot_audit/step16_closure.md`

## BLOCKED-but-SHADOW-PASS is correct and expected behavior

When `KalshiWxShadowResearchClient.research()` is inert (CAN_EXECUTE=False), the
`forecast_context` subagent fails immediately. The orchestrator records status=BLOCKED
in the ledger but still validates the assembled "blocked payload" via `validate_shadow_output`.

The blocked payload is designed to be schema-valid: it carries ceiling=KALSHI_WATCH
(a CEILING_CAPABLE_LABELS member) and advisory_only=True. So `svr.passed=True` (SHADOW_PASS)
and `ledger_entry.status=BLOCKED` are simultaneously true and correct.

**Do not treat BLOCKED+SHADOW_PASS as a contradiction.** It means:
- The safety boundary worked (client is inert)
- The schema validation worked (payload is well-formed)
- No production authority was exercised

## 25-trial pilot results (2026-08-09)
- Total: 25 | SHADOW_PASS: 25 (100%) | SCHEMA_FAIL: 0 | EXCEPTION: 0
- Hook violations: 0 | advisory_only violations: 0 | ceiling violations: 0
- All ceilings: KALSHI_WATCH (expected when research client is inert)
- Verdict: ACCEPTED

## Trial script setup — critical order requirement
`kalshi_wx_shadow_agent.KALSHI_WX_SHADOW_AGENT_ENABLED` is a **module-level bool**
evaluated at import time. To enable real API calls in test scripts:
1. Set `os.environ["KALSHI_WX_SHADOW_AGENT_ENABLED"] = "true"` BEFORE any shadow import
2. Also patch the constant directly after import: `_agent_mod.KALSHI_WX_SHADOW_AGENT_ENABLED = True`
   (handles pre-cached imports)
Either alone is insufficient when modules are cached.

## Validator patch target for payload interception
The orchestrator does `from gate_engine.kalshi_wx_shadow_schema import validate_shadow_output`
(local name in orchestrator module). Correct patch target:
`"gate_engine.kalshi_wx_shadow_orchestrator.validate_shadow_output"`
NOT `"gate_engine.kalshi_wx_shadow_schema.validate_shadow_output"` (source module patch
won't intercept the local reference already captured at import time).

## What real Claude calls would require
`KalshiWxShadowResearchClient` needs CAN_EXECUTE=True granted by a future step.
That is explicitly out of scope for the current pilot. Until then, all runs produce
BLOCKED status with conservative KALSHI_WATCH ceiling. This is correct behavior.
