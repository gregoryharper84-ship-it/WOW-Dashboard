---
name: Expert review Step H
description: Autonomous downgrade-only audit pass wired into /analyze-and-score; architecture, constraints, and postmortem retrieval pattern.
---

## Rule
`gate_engine/expert_review.py` runs `run_expert_review()` after every scored slip.
It is downgrade-only: can make a verdict more conservative, never loosen it, never reinstate a killed leg.

## Why
Claude's review spec (wow-claude-integration-spec Step H) requires a second independent pass over the pipeline's own output with no human in the loop. Fully autonomous = downgrade-only safety net only; anything that can also upgrade is unsafe without live oversight.

## How to apply
- `run_expert_review(slip_id, legs, correlation_risk)` returns an audit dict; always returns (stub on error).
- App.py calls it after `generate_explanation`, applies downgrades to `legs_out` (adds `EXPERT_REVIEW_DOWNGRADE` flag + replaces `terminal_label`), then calls `_write_expert_review_log()` (best-effort, never raises).
- `slip_expert_review_log` table created in `_CM_SCHEMA_DDL`; indexed by slip_id and reviewed_at DESC.
- `GET /wow/slip-review-log` retrieves entries; supports `?slip_id=`, `?verdict=CONFIRMED|DOWNGRADED`, `?limit=`, `?offset=`.
- Response includes `expert_audit: { audit_verdict, correlation_audit, error }` at top level.
- Schema operation: `getSlipReviewLog` in gpt-action-schema-gate-engine.yaml; requires X-API-Key.

## Constraints
- `_write_expert_review_log` must be best-effort (`try/except` swallows all errors) — DB failure cannot block the main response.
- `_normalise()` reverts DOWNGRADED entries that lack a distinct `audit_label` (Claude bug protection).
- `escalated=True` in correlation_audit counts as a downgrade even if no per-leg audit_result is DOWNGRADED.
