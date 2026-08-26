---
name: Kalshi WX shadow schema validator
description: Step 9 of the shadow pilot — closed schema + validator for future shadow agent output; gate_engine/kalshi_wx_shadow_schema.py.
---

## Rule
`validate_shadow_output(payload)` in `gate_engine/kalshi_wx_shadow_schema.py` validates shadow agent output against a strict closed schema.
Returns `SHADOW_PASS` (singleton) on success, or a `ShadowValidationResult(passed=False, shadow_failure_only=True, ...)` on failure.

**Why:** Shadow agent output must never reach production governance paths. The schema is the enforcement boundary: any payload that passes validation is structurally safe to hand to the (not-yet-wired) shadow comparison layer; any failure is tagged `shadow_failure_only=True` and must never reach `weather_scout_log`, production API responses, or existing route behavior.

## Validation order (each step aborts on first failure)
1. Root type must be `dict`
2. **Recursive forbidden-key scan** — runs FIRST before structural checks; presence of any key in `FORBIDDEN_GOVERNANCE_KEYS` at any depth is an unconditional `FORBIDDEN_GOVERNANCE_KEY` violation regardless of surrounding structure validity
3. `additionalProperties=false` at root
4. All 11 required root fields present
5. Scalar string types (`agent_id`, `run_id`, `lane`, `status`, `recommended_ceiling`)
6. `lane == "KALSHI_WEATHER"` (exact literal)
7. `status in {"COMPLETE","SCHEMA_FAIL","TOOL_FAIL","BLOCKED"}`
8. `advisory_only` is `type(v) is bool and v is True` — integer `1` and string `"true"` both fail
9. `recommended_ceiling in CEILING_CAPABLE_LABELS` (imported from `kalshi_wx_shadow_registry`)
10–12. Nested object schemas: `facts`, `probabilities`, `uncertainty` — each with `additionalProperties=false`
13–14. `agent_observed_blockers` and `source_conflicts` are `list[str]`

## Forbidden governance-authority keys (11 total)
`terminal_label`, `final_label`, `label`, `can_execute`, `execute`, `capital_allocation`, `execution_permission`, `trade_authorization`, `governance_state`, `authorized`, `approved_for_execution`

The scan is recursive through dicts AND lists. String VALUES containing these words are NOT affected — only dict KEYS trigger the violation.

## Key design decisions
- **Forbidden scan before all else**: even a forbidden key inside an extra/unknown field surfaces as `FORBIDDEN_GOVERNANCE_KEY`, not `EXTRA_FIELD`. More informative and more secure.
- **`advisory_only` identity check**: `type(v) is bool and v is True` — not `v == True` — because `1 == True` is True in Python but `type(1) is bool` is False.
- **`SHADOW_PASS` singleton**: `validate_shadow_output` returns the same object on every success; test J3 asserts `assertIs`.
- **`"blockers"` is NOT an alias**: using `"blockers"` instead of `"agent_observed_blockers"` fails as `EXTRA_FIELD` (unrecognized root key) — test G5.
- **`"label"` in forbidden set**: bracket items use `"bracket_range"` not `"label"` to avoid the forbidden key triggering on legitimate bracket data.

## Nested schema allowed keys
- `facts`: city, date, nws_station_code, scoring_mode, forecast_high_f, cli_high_f, forecast_source_tier, data_acquisition_notes
- `probabilities`: brackets_scored, model_prob_sum, calibration_status
- `brackets_scored` items: bracket_range, model_prob, verdict
- `uncertainty`: horizon_hours, sigma_f, uncertainty_tier, notes

## `ShadowValidationResult` fields
`passed`, `violation` (ShadowSchemaViolation enum), `failure_reason` (str), `failure_path` (JSONPath-style), `shadow_failure_only` (bool — always True on failure)

## Isolation invariant
`kalshi_wx_shadow_schema` must NOT be imported by `wow_runtime_manifest.py`, `cc_labels.py`, or `ceiling_resolver.py`. Tests K1–K3 enforce this with grep-based assertions.

## Out of scope (not in this module)
Agent SDK code, subagent definitions, orchestrator, hooks, shadow ledger persistence, paired-snapshot fields (research_snapshot_id etc.) — all deferred to later steps.
