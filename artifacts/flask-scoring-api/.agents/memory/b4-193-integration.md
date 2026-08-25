---
name: B4 #193 integration wiring
description: How pipeline_state.py was wired into the real B4 adapter decision path; key design choices and non-obvious constraints.
---

## What was wired

`PipelineStateGuard.can_upgrade()` is now called inside `WnbaPropsAdapter.adapt()` for every failure path, making pipeline_state.py load-bearing. Before this, pipeline_state.py had zero callers outside its own unit tests.

Two failure paths wired in `adapter.py`:
1. **ACQUISITION_PROVIDER_ERROR** — `acquisition_error: str | None = None` kwarg; returns TECHNICAL_FAILURE immediately before row processing.
2. **GAME_SCRIPT_MODEL_ERROR** — `_run_game_script_shadow_classified()` returns a tuple `(result_or_None, error_str_or_None)`; exception path produces TECHNICAL_FAILURE with packet+role_payloads preserved.

New module: `gate_engine/universal_agent/lanes/wnba_props/pipeline_gateway.py` — post-adapter gateway that propagates adapter failures into `RowPipelineState` and evaluates the HOLD ceiling.

## Critical design choice: row_id re-scoping

**Problem:** `adapter.py` derives its internal row_id as `f"{event_id}:{run_id}"`. The gateway caller uses its own row_id string. These differ. `RowPipelineState.record_failure()` enforces `failure.row_id == state.row_id` and raises `ValueError` on mismatch.

**Fix:** `WnbaPipelineGateway.process()` re-scopes the adapter's failure to the gateway's row_id before calling `record_failure()`. The failure_kind/code/layer/message are preserved; only row_id changes. This is semantically correct: the gateway "claims" the adapter's detection for its own row tracking boundary.

**Why:** Without re-scoping, any call to `gateway.process(adapter_result, row_id="...")` where row_id differs from the adapter's internal ID raises ValueError, making the gateway unusable with real rows.

## Key distinction: _run_game_script_shadow_classified

Old `_run_game_script_shadow()` swallowed ALL exceptions → `None`. A model crash was indistinguishable from legitimate `None` (no game-script inputs). 

New `_run_game_script_shadow_classified()` returns a 3-state tuple:
- `(dict, None)` — shadow gate ran, produced result
- `(None, None)` — shadow gate ran normally, returned None (legitimate absence)
- `(None, "ExcType: message")` — shadow gate raised → TECHNICAL_FAILURE

Test B11 explicitly verifies the `(None, None)` case does NOT trigger TECHNICAL_FAILURE.

## New AdapterStatus constants

`TECHNICAL_FAILURE` and `CONTRACT_FAILURE` added alongside existing `COMPLETE`/`DEGRADED`. Existing tests unaffected (new constants are additive; new fields on WnbaPropsAdapterResult have `Optional` defaults of None).

## UpgradeGuardResult is now frozen

Changed from `@dataclass` to `@dataclass(frozen=True)` so it can be embedded in the frozen `WnbaPropsAdapterResult` without violating immutability semantics. Existing tests unaffected (they mutate the dict value inside `preserved_upstream_result`, not the field reference — still allowed with frozen).

## What is NOT wired yet

`acquisition_error` param and `WnbaPipelineGateway` are not called from any production route or `app.py`. The distinction exists in the shadow B4 path (adapter + tests) but live GPT scoring sessions still see DEGRADED for provider failures, not TECHNICAL_FAILURE. Wiring into the production route was explicitly out of scope.

## How to apply

- To classify an acquisition failure in a B4 context: `adapter.adapt(..., acquisition_error="HTTP 503 from provider")`
- To process through the gateway: `WnbaPipelineGateway().process(adapter_result, row_id="...")`
- To inject a market-layer failure after a clean adapter run: `gw.process_with_market_failure(result, row_id, gw.make_market_failure(row_id=...))`
- To record a legitimate NO_PLAY: `gw.record_legitimate_rejection(result, row_id, rejection_code="NO_PLAY")`
