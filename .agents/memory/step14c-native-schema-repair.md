---
name: Step 14C native schema repair
description: Native per-subagent closed-schema validator, canonical assembly wiring, and offline replay results for the Kalshi WX shadow pilot.
---

## What was built
- `gate_engine/kalshi_wx_shadow_native_schema.py` — 5 per-subagent validators + `validate_subagent_output(subagent_id, tool_input)` dispatcher; enforces additionalProperties=false, required fields, types, enums.
- Wired into `_run_single_tool_subagent()` AFTER the CapabilityBoundary post-hook comment and BEFORE "Tool input accepted — build usage fields". Failure returns `SubagentResult(success=False, failure_reason="NATIVE_SCHEMA_VIOLATION: ...")`.
- `kalshi_wx_shadow_snapshot_schema_validation` table (SERIAL id, research_snapshot_id UNIQUE, canonical_payload_json JSONB, validation_status TEXT, validation_detail TEXT, recorded_at TIMESTAMPTZ) — one row per snapshot.
- `write_snapshot_validation_row()` and `_record_snapshot_schema_validation()` helpers added to runner.
- `_record_snapshot_schema_validation()` wired into `run_pilot()` after the inner agent loop (just before `snapshots_done += 1`).
- `scripts/replay_shadow_14c.py` — one-off offline replay script; reads 125 rows, runs native + canonical validation, upserts to new table.

## Key design decisions

**Why:** CapabilityBoundary FORBIDDEN_GOVERNANCE_KEYS has only 11 specific keys. final_decision, stake_tier, is_playable are NOT in that set and would pass through undetected without the native validator.

**Ceiling valid values:** `['KALSHI_DATA_UNOBTAINABLE', 'KALSHI_PLAYABLE_LIMIT_ONLY', 'KALSHI_REJECT_BAD_RULES', 'KALSHI_REJECT_NO_EDGE', 'KALSHI_WATCH']` — sourced from `KALSHI_WX_TERMINAL_LABEL_REGISTRY`.

**_assemble_payload is private** (underscore prefix in orchestrator.py) but is imported directly by the runner and replay script. This is intentional — reuse, not reimplementation.

**INCOMPLETE status logic:** `len(prior_results) < 5` after the inner agent loop → INCOMPLETE. The canonical_payload is still assembled and stored (partial assembly is visible in DB). validate_shadow_output is only called when all 5 are COMPLETE.

## Offline replay results (125 rows from Step 14B pilot)
- Native validation: 125 PASS / 0 FAIL
- Canonical assembly + Step 9: 25 SCHEMA_VALID / 0 SCHEMA_INVALID / 0 INCOMPLETE
- 25 rows upserted to kalshi_wx_shadow_snapshot_schema_validation

## Test suite
- `tests/test_kalshi_wx_shadow_native_schema.py`: 60 tests, all pass
- Full suite after 14C: 9 pre-existing failures (5 MLB 1IP + 4 WNBA acquisition), 0 new, 4291 passed

## What still doesn't happen
- Phase 2 (live run with SHADOW_RESEARCH_API_ENABLED=true) has not confirmed the wiring fires under real conditions — that is a separate step.
- The Step 9 schema validator is still not called on individual per-agent rows — only on the assembled 5-agent payload. The per-agent rows are now validated by the native schema instead.
