---
name: B3C-R1 repair
description: Three offline fixes applied to the B3C bounded Claude canary after the first live run revealed two OUTPUT_REJECTED roles and cost-accounting gaps.
---

# B3C-R1 Canary Repair

**Fix 1 — Fail-fast abort (CanaryAbortState)**
- `CanaryAbortState` (is_aborted, abort_reason, set_aborted) created ONCE per canary run in `run_canary_pipeline()`, passed into `ClaudeRoleRunner`.
- Step 0 in `__call__()` checks `abort_state.is_aborted` BEFORE budget guard and `record_attempt()`. Aborted roles return `SKIPPED_DUE_TO_PRIOR_ABORT`, make ZERO API calls.
- `set_aborted()` is called at every structural failure mode (budget guard, model identity, missing usage, malformed response, forbidden key scan, API error).
- Does NOT change B2 orchestrator loop — abort is canary-runner-layer only.

**Fix 2 — All-attempt cost accounting**
- `actual_cost` is computed at step 6b (immediately after input/output tokens confirmed from usage), BEFORE model identity check, content parse, or B0 scan.
- All `CanaryCallRecord` instances created after step 6b carry `calculated_cost_usd`.
- `record_failure_cost(actual_cost)` now called with real computed cost (not 0.0) for rejected calls.
- `_persist_all_results` cumulative loop (`if rec.calculated_cost_usd is not None`) now correctly accumulates rejected-call costs too.

**Fix 3 — Prompt hardening**
- `_build_prompt()` appends `_KEY_CONTRACT` block to every role prompt, explicitly naming forbidden key names (terminal_label, final_label, etc.) and stating that B0 recursive scanner is the real enforcement.
- Defense-in-depth ONLY — does not replace or weaken the real B0 scanner.

**Test results**
- Focused B3C suite: 144 passed, 0 failed (123 original + 21 new B3C-R1 tests)
- Full regression: 9 pre-existing failures only, 5441 passed, 0 new failures

**10th pre-existing failure fixed**
- `test_M6_new_model_present_only_in_subagents_and_agent` was failing because `run_b3c_canary.py` (added in original B3C build) wasn't in the authorized list.
- Added `"run_b3c_canary.py"` to `_AUTHORIZED` in `test_kalshi_wx_shadow_model_migration.py`.
- This was Task #171's root cause for scripts/run_b3c_canary.py specifically.

**Why:**
First live canary run showed SPORT_SPECIALIST and FINAL_REFRESH rejected (terminal_label nested in Claude output). The orchestrator continued iterating to roles 5 and 6 after the first rejection (spending real money), and rejected calls had null calculated_cost_usd in the DB.

**Open gap (not fixed here):**
`place_bet` and `settlement` are not in `FORBIDDEN_GOVERNANCE_KEYS`. When injected via `advisory_findings` (an allowed root key), they bypass the root-level allowlist. No adversarial test coverage exists. `trade` IS in the frozenset.
