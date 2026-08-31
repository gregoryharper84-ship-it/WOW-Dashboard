---
name: Kalshi WX shadow pilot run patterns
description: Durable lessons from the first real Step 14B shadow pilot execution — flag handling, model column, schema validator scope, cost baseline.
---

## Pilot execution requires two shell invocations
The 5-minute process timeout kills the runner mid-run (~90 calls complete). The runner commits each row immediately (`write_result_row` + `conn.commit()`), so everything persisted before SIGKILL is safe. Re-run immediately — `is_pair_completed` skips all already-committed pairs and only processes the remainder. Natural `stop_reason=EXHAUSTED` is reached on run 2.

**Why:** ShellExec max timeout is 300s; 125 calls × ~3s/call = ~6 min total.

**How to apply:** Always plan two invocations for a full 25×5 pilot run. Run 2 output JSON summary is the authoritative stop_reason/cost; aggregate token totals must be queried from the DB across both run_ids.

## model column is always NULL in kalshi_wx_shadow_results
`call_one_agent` (run_kalshi_wx_shadow_pilot.py line ~521) hardcodes `model=None` in its return dict. The `model` column in the DB will be NULL for all rows. Model identity is enforced by the `_MODEL` constant in `gate_engine/kalshi_wx_shadow_subagents.py` and verified by M1 migration tests — not by the DB column.

**Why:** The subagent dispatch layer returns `SubagentResult` which doesn't carry the model string back to the caller.

**How to apply:** Don't query `model` column for audit evidence — use M1 test results instead.

## usage_accounting_status is inside JSONB, not a column
`validated_output_json->>'usage_accounting_status'` is the correct SQL path. There is no top-level `usage_accounting_status` column. `input_tokens`, `output_tokens`, and `estimated_cost_usd` ARE top-level columns (added by `apply_schema_migrations`).

## Step 9 schema validator scope: assembled output only
`validate_shadow_output` validates the ASSEMBLED 5-agent orchestrator output, not individual per-agent tool_inputs. Applying it row-by-row gives EXTRA_FIELD rejections for legitimate agent keys (blockers, conflicts, notes, etc.). For per-row audit, scan for FORBIDDEN GOVERNANCE KEYS directly (terminal_label, can_execute, final_label, etc.) — these must be zero.

**Why:** The assembler (Step 15) that combines the 5 per-agent outputs into the validated format hasn't been built yet.

## Cost baseline: $0.395 for 25-snapshot × 5-agent full run
At INPUT_PRICE=0.000001, OUTPUT_PRICE=0.000005, MAX_OUTPUT_TOKENS=1024:
- Total: $0.39507100 (~$0.00316/call average)
- Input tokens: 190,851 total (~1,527/call avg)
- Output tokens: 40,844 total (~327/call avg)
- Uncertainty_explanation agent is the most expensive (~$0.003612/call)
- Unusual_regime is the cheapest (~$0.002661/call)

## SHADOW_RESEARCH_API_ENABLED: pass inline, never persist
Pass as inline subprocess env (`SHADOW_RESEARCH_API_ENABLED=true python script.py`). Never set it via legacy platform env var system. After subprocess exits, it is naturally gone — no cleanup step needed. KALSHI_WX_SHADOW_AGENT_ENABLED must remain false during pilot runs (live-route capture, not pilot execution).

## All 25 snapshots got PROVISIONAL calibration except finalized observations
Snapshots with `forecast_horizon_hours=0.0` (same-day, already-finalized) got `scoring_mode=binary_final_cli` and sometimes `CALIBRATED`. Snapshots with horizon > 0 got `scoring_mode=gaussian_forecast` and `PROVISIONAL`. This is expected signal from the agents.
