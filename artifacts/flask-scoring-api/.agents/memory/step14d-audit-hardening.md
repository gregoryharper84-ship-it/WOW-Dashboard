---
name: Step 14D audit-hardening patches
description: Two fixes applied to the Kalshi WX shadow pilot after Step 14C canary; model identity persistence and mock-path enforcement choke point.
---

## Fix 1 — Model identity persistence

**Rule:** `call_one_agent` in `scripts/run_kalshi_wx_shadow_pilot.py` must return model via
`_sa_mod._MODEL` (module attribute reference, lazy import inside the function), never a
hardcoded literal. This ensures a future `_MODEL` constant change in
`gate_engine/kalshi_wx_shadow_subagents.py` flows into persisted rows automatically.

**Why:** All 130 pre-Step-14D rows had `model=NULL` because `call_one_agent` returned
`"model": None`. Backfill executed targeting exactly the three known run_ids (89+36+5=130 rows).

**Backfill (one-time, already executed):**
```sql
UPDATE kalshi_wx_shadow_results
SET model = 'claude-haiku-4-5-20251001'
WHERE run_id = ANY(ARRAY[
    'pilot-77549cab-b5df-40a5-94dc-2c6b1fcdad95',
    'pilot-3a141b0e-8d72-4cd8-a2d1-b784bb47d1a1',
    'canary-14c-3b00e8ca-9cc7-4e4f-9adc-39929929c2cc'
]) AND model IS NULL;
-- Confirmed updated=130 (exact match), model IS NULL = 0 after backfill.
```

**Tests:** `tests/test_kalshi_wx_shadow_14d.py::TestModelIdentityPersistence` (5 tests) — includes
sentinel-patching proof and structural no-hardcode grep.

---

## Fix 2 — Outer enforcement choke point

**Rule:** In `run_pilot()`, immediately after `status = "COMPLETE" if success else "BLOCKED"`,
an outer enforcement block runs for ALL results regardless of whether `_caller` was
`call_one_agent` (real SDK path) or a test-supplied `call_agent_fn` (mock path):

1. `validate_subagent_output(agent_id, tool_input)` — same function used inside
   `_run_single_tool_subagent`; covers unknown properties, missing required fields,
   type errors, enum violations.
2. `cap_boundary.post_tool_use_hook(agent_id, agent_id, tool_input)` — governance key scan;
   uses `post_tool_use_hook` (not `pre`) because the outer point lacks the model's
   actual called-tool name. Treated as BLOCKING here (unlike inner post-hook which is advisory).

On failure, sets `success=False`, `status="BLOCKED"`, `tool_input={}`, and sets
`failure_reason` to `OUTER_NATIVE_SCHEMA_VIOLATION: ...` or `OUTER_CAP_BOUNDARY_VIOLATION: ...`.

**Why:** Without this, a `call_agent_fn` mock returning `{"final_decision": "APPROVED", ...}`
would pass straight into `prior_results` and the canonical assembler — producing a false PASS.

**Existing test update:** `test_SDRROW_success_writes_COMPLETE_status` was updated to use
`_valid_result(agent_id)` (returns per-agent minimum valid tool_input). `_VALID_TOOL_INPUTS`
and `_valid_result()` helpers added to `tests/test_kalshi_wx_shadow_pilot.py`.

**Tests:** `tests/test_kalshi_wx_shadow_14d.py::TestMockPathEnforcement` (9 tests) — 7 adversarial
cases + 2 shared-code structural proofs using `patch.object` on `validate_subagent_output`
and `CapabilityBoundary.post_tool_use_hook`.

---

## Regression baseline after Step 14D

- 4305 passed, 9 pre-existing failures (unchanged), 12 skipped, 420 subtests
- 9 failures: 5× MLB 1IP tests + 4× WNBA evidence acquisition — unrelated to shadow pilot
- Zero new failures from the outer enforcement or model identity changes
