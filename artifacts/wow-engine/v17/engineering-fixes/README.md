# WOW V17 Engineering Fixes

This directory is the canonical home for V17 remediation records: bug fixes, regression fixes, model improvements, active patches, and deployment/infrastructure repairs.

## Purpose

An engineering fix records what changed, why it changed, the exact files/components affected, tests executed, deployment evidence, production verification, and rollback information.

## Required separation

- Postmortem = diagnosis/evidence/root cause.
- Engineering fix = implementation/testing/deployment/verification.
- Fixes should reference the originating postmortem ID whenever one exists.
- A fix is not complete until regression and production verification evidence are attached or an explicit blocker is recorded.

## ID convention

`FIX-YYYY-MM-DD-NNN`

Example: `FIX-2026-09-03-001-action-schema-validation-repair.md`

## Suggested categories

- `active-patches/`
- `bug-fixes/`
- `regression-fixes/`
- `model-improvements/`
- `deployment-infrastructure/`

GitHub creates directories only when they contain files, so category folders should be created when the first fix in that category is added.

## V17 engineering rules

1. Never weaken fail-closed semantics to make a test pass or manufacture a pick.
2. Preserve controlling-lane ownership and exact typed model failures.
3. Do not modify `model_probability`, `calibrated_probability`, or `calibrated_lower_bound` merely to address portfolio duplication or narrative concerns.
4. Canonical V17 instruction and OpenAPI paths remain stable unless an explicit migration updates all consumers.
5. Secrets must never be written into engineering records, logs, tests, or commits.
6. `can_execute=false` and dry-run/no-live-trading constraints remain invariant.

Use `TEMPLATE.md` for all new engineering fixes.
