# Replit Implementation Prompt — WOW v16 Skills Pack

Implement the attached WOW v16 skill pack as a deterministic, testable orchestration layer.

## Required structure
- Add `skills/registry.py` to load `skill-registry.json`.
- Add `skills/contracts.py` with typed `SkillResult`, `SourceEvidence`, `Blocker`, and label enums.
- Add one adapter module per skill; adapters may call existing WOW engines but may not duplicate authoritative calculations.
- Add `skills/orchestrator.py` to enforce `ORCHESTRATOR.md` ordering and lowest-ceiling propagation.
- Validate every output against `schemas/skill-result.schema.json`.
- Persist `run_id`, skill version, source timestamps, blockers, label, and downstream handoffs.

## Critical invariants
- `can_execute` must always be false.
- No live trading or market orders.
- Kalshi sports must stop on `INVENTORY_EMPTY` and scan only on `INVENTORY_READY`.
- Operator-supplied and synthetic prices cannot become direct/live sources.
- Stale Kalshi price >10 minutes is unobtainable.
- Canonical dry-run label is `LLP_PLAYABLE_LIMIT_ONLY_DRY_RUN`; normalize forbidden bare label.
- Apply active Reliability Freeze combo and exposure caps.
- Preserve existing master spec and patch precedence.

## Acceptance tests
Run all tests in `tests/acceptance-tests.md`; add unit tests for every invariant and regression test for each active patch touched by integration.

## Delivery
Return changed files, test counts, failures, migration notes, and a registry of which existing WOW modules each skill adapter calls. Do not claim shipped until tests pass and Flask starts cleanly.
