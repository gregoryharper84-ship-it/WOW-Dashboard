---
name: Kalshi WX shadow pilot Step 12.5B2 — dual-gate auth + usage accounting
description: Two-gate authorization defense, max_tokens threading, and real AVAILABLE/UNAVAILABLE usage accounting in the shadow pilot runner and subagent layer.
---

## Architecture

### Gate A (runner layer — `scripts/run_kalshi_wx_shadow_pilot.py`)
- Checked live on every call inside `call_one_agent()` using `os.environ.get("SHADOW_RESEARCH_API_ENABLED", "false")` (env var, not cached).
- Returns a failure dict with `SHADOW_RESEARCH_API_DISABLED` reason and `None` for token fields if false.

### Gate B (subagent layer — `gate_engine/kalshi_wx_shadow_subagents.py`)
- Module-level `_RESEARCH_API_ENABLED: bool` cached at import time from `SHADOW_RESEARCH_API_ENABLED` env var.
- Checked inside `_run_single_tool_subagent()` before the SDK for-loop.
- Returns `SubagentResult(success=False, failure_reason="RESEARCH_API_DISABLED:...")` if false.
- Patchable in tests: `gate_engine.kalshi_wx_shadow_subagents._RESEARCH_API_ENABLED = True`.

**Why two gates:** Gate A prevents runner-level dispatch; Gate B prevents any SDK call even if the runner gate is somehow bypassed (defense in depth).

## `max_output_tokens` threading
- All 5 `run_*_subagent()` functions accept `max_output_tokens: int = _MAX_TOKENS`.
- Passed through to `_run_single_tool_subagent(max_output_tokens=...)`.
- Runner passes `config["MAX_OUTPUT_TOKENS_PER_CALL"]` to `call_agent_fn`.
- SDK call: `max_tokens=max_output_tokens` (not the module constant).

## Usage accounting contract
- `SubagentResult` has 3 new fields: `input_tokens: Optional[int]`, `output_tokens: Optional[int]`, `usage_accounting_status: str = "UNAVAILABLE"`.
- AVAILABLE: both token values extracted from `response.usage` using `isinstance(int)` guard (MagicMock fails the guard → UNAVAILABLE, not a test-breaking false-positive).
- Runner: `row_in_tok`/`row_out_tok`/`row_cost` initialized as `None`/`None`/`None`; only set to real values when `usage_accounting_status == "AVAILABLE"` and both tokens are `int`.
- Cumulative tracking always uses pessimistic estimate regardless (never weakened by cheap actual).
- `write_result_row` accepts `Optional[int]` and `Optional[float]` — `None` not `0` on row when unavailable.

## `pre-call budget check invariant`
- The `wc_cost` guard uses `input_tok_est + MAX_OUTPUT_TOKENS` (worst-case), not actual prior spend.
- This ensures budget is never exceeded even when actual calls are cheap.
- The runner serializes a small metadata dict (not the full snapshot) for token estimation — so test code cannot replicate exact token counts without matching that serialization.
- Tests for this gate: use zero budget (no calls at all) or ample budget (all calls go through), not exact boundary math.

## Test infrastructure (Gate B patch pattern)
Four test modules call `run_*_subagent()` with mock SDK and need Gate B open:
- `tests/test_kalshi_wx_shadow_stage2.py` — SA1–SA11
- `tests/test_kalshi_wx_shadow_snapshot.py` — SC3
- `tests/test_kalshi_wx_shadow_stage3.py` — OR1–OR3, OR7
- `tests/test_kalshi_wx_shadow_b2.py` — B2T3, B2T5, B2T6

Pattern in each:
```python
_patch_research_enabled = patch(
    "gate_engine.kalshi_wx_shadow_subagents._RESEARCH_API_ENABLED", True
)
def setUpModule(): _patch_research_enabled.start()
def tearDownModule(): _patch_research_enabled.stop()
```

**Why:** `_RESEARCH_API_ENABLED` is cached at import time from the env var. Module-level patching of the bool is the only reliable way to open Gate B in tests without setting the env var (which would affect process-wide state and risk leaking between tests).

## Test file
`tests/test_kalshi_wx_shadow_b2.py` — 8 test classes, ~40 tests:
- TestB2GateA: Gate A blocks + no call_agent_fn invoked
- TestB2GateB: Gate B blocks + no SDK call
- TestB2FlagTrueMockSdk: both gates open → SDK called, max_tokens threaded
- TestB2AuthorityStructural: CAN_EXECUTE/PRODUCTION_AUTHORITY/USER_OUTPUT_AUTHORITY still False
- TestB2UsageAccountingAvailable: AVAILABLE → real counts propagate to row
- TestB2UsageAccountingUnavailable: UNAVAILABLE → None not 0 on row and result
- TestB2AppPyIsolation: `SHADOW_RESEARCH_API_ENABLED` not in app.py
- TestB2PreCallBudgetCheckUnchanged: zero-budget refuses all calls; ample budget allows all calls
