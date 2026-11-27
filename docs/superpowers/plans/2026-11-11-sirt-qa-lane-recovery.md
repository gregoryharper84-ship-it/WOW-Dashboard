# WOW SIRT intake recovery implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Accept existing PRs into the existing SIRT intake without unsafe authorship, stale-SHA routing or approval spoofing.

**Architecture:** Extend the already deployed SIRT intake workflow with exact-head `workflow_dispatch` and label-gated `pull_request_target`. Keep the existing worker-origin path and issue marker unchanged. The QA path remains as-is because live evidence confirmed that it self-recovered; use the established manual exact-head dispatch as fallback.

**Tech Stack:** GitHub Actions YAML, Bash, GitHub API, pytest/PyYAML, Python 3.11.

**Spec:** `docs/superpowers/specs/2026-11-11-sirt-qa-lane-recovery.md`

## Global constraints
- Project Hub #2076, canonical issue #1128; no duplicate SIRT team or controller.
- `can_execute=false`; `V17_TERMINAL_REDUCER` remains sole terminal authority.
- PR code and content must never be executed in trusted event.
- Workflow edits require protected exact-head QA, Release Authority and independent non-author review.
- User authorizes protected merge only after all required acceptance.

## Review focus
- A malicious fork's label/body cannot create trusted SIRT intake
- Stale manual SHA cannot create issue
- Duplicate events do not create duplicate intake
- Existing worker-origin intake cannot silently be bypassed
- Created issue never asserts independent PASS or authorizes merge/deploy

---

### Task 1: Add existing-PR SIRT entrypoints
**Files:** Modify `.github/workflows/wow-v17-engineering-sirt-intake.yml`, test `artifacts/wow-engine/test_v17_engineering_sirt_intake_routing.py`

- [ ] Write fail-closed tests for manual exact-head and label-gated source admission.
- [ ] Verify tests fail against old workflow.
- [ ] Add `workflow_dispatch` inputs and `pull_request_target` label event with trusted main guard.
- [ ] Keep legacy worker-origin branch/source gate unchanged.
- [ ] Verify workflow and negative tests.

### Task 2: Preserve idempotent issues and honest escalation
**Files:** Same workflow and test; update documentation.

- [ ] Test no source code checkout, exact identity/incident, preserved legacy backtick headers and duplicate marker across concurrent entrypoints.
- [ ] Implement minimal branch-dependent resolver and a single repository-scoped `queue: max` writer lock; preserve pre-PR gate and legacy incident syntax for worker origin.
- [ ] Verify tests and independent hosted CI.
- [ ] Record 'SIRT intake only' and zero-credit QA recovery steps.

### Task 3: Acceptance and handoff
**Files:** PR body / issue #2076.
- [ ] Verify current main ancestry, exact SHA checks, independent SIRT/QA/Release provenance.
- [ ] Request non-author SIRT review and protected merge only after acceptance.
- [ ] Verify merged SHA and live workflow behavior separately; no assumed deployment.
