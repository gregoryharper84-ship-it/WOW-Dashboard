---
name: Mandatory route completion patch #22
description: Patch #22 — route_registry.py enforces that all required gates ran before a qualifying label stands. Wired after classifier.classify() in pipeline.py.
---

# Mandatory Route Completion (Patch #22)

**Patch ID:** `WOW-PATCH-2026-08-02-MANDATORY-ROUTE-COMPLETION`
**Precedence:** 101
**Patch count after this:** 22

## What it does

After `classifier.classify(row)` in pipeline.py, `route_registry.enforce_route_completion(row)` runs per-row.

If the row holds FINAL_APPROVED, MONEY_QUALIFIED, or MARKET_VERIFIED_HOLD but a required gate is absent from `row['gates']`, the terminal_label is downgraded to MODEL_QUALIFIED_HOLD with blocker `REQUIRED_GATE_NOT_EXECUTED:<gate_id>`.

One-way only — can only lower labels, never raise them.

## Required gates

- **Universal (all rows):** slate_validation, status_role, l5_l10_ledger, market_gate, ev_gate, slip_structure, exposure_gate
- **Sport extra:** MLB/NBA/WNBA/NFL/NHL require `acquisition`
- **Prop-type extra:** 1IP_PITCHES_THROWN variants require `calibration_health`

## Execution trace

`_build_output` now computes `gate_execution_summary` — a list of per-row dicts with: gates_ran, gates_passed, gates_failed, required_gates, required_missing, route_complete, route_downgraded, original_label_before_route_enforcement. Present in every run output.

`summary.route_completion_failures` counts rows that were downgraded.

## Key implementation detail

The computation `gate_execution_summary = [...]` must live in `_build_output` BEFORE the return dict, not in `run_pipeline` — the variable is referenced inside the return dict, so it must be in scope there.

## Addresses architecture gap

Doc requirement: "no row may hold a qualifying label unless all required gates for its sport and prop_type are present in row['gates']." Previously classifier only enforced REQUIRED_FOR_FINAL for the FINAL_APPROVED path via _all_required_gates_passed(); MONEY_QUALIFIED and MARKET_VERIFIED_HOLD had no such enforcement.
