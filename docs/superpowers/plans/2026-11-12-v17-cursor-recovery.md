# WOW V17 Cursor Waves 1–2 Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore truthful protected release admission (`CUR-REL-20261112`) and genuine certified fitted-model Action delivery (`CUR-DELIV-20261112`) without relaxing gates or fabricating probabilities.

**Architecture:** Two isolated worktrees/PRs. CUR-REL independently re-authors the #2630 automatic QA/Release callback HOLD isolation under Cursor nonowner authorship to clear `TRUST_ROOT_OWNER_AUTHORED`. CUR-DELIV inventories sport×market×route and repairs only hydration/scorer/Action producing layers for already-certified specialists.

**Tech Stack:** GitHub Actions workflows, `artifacts/wow-engine` FastAPI V17 runtime, pytest, Render web+worker, Supabase receipts, ChatGPT Actions.

## Global Constraints

- `can_execute=false`
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`
- `V17_TERMINAL_REDUCER` sole terminal disposition authority
- Do not touch Freebuff #2605 offline fitting/calibration
- Do not bypass ruleset #24907422 or mint false App PASS
- Independent QA App 5245832 and Release App 5245853 must author exact-head checks
- Owner-authored trust-root PRs cannot self-certify (`TRUST_ROOT_OWNER_AUTHORED`)

---

### Task 1: CUR-REL — Independent nonowner adoption of #2630

**Files:**
- Modify: `.github/workflows/wow-v17-independent-qa-check.yml`
- Modify: `.github/workflows/wow-v17-release-authority-check.yml`
- Modify: `artifacts/wow-engine/test_v17_independent_release_checks.py`
- Create: `docs/superpowers/specs/2026-11-12-v17-cursor-recovery-design.md`
- Create: `docs/superpowers/plans/2026-11-12-v17-cursor-recovery.md`

**Interfaces:**
- Consumes: #2630 change blobs; ruleset #24907422; incident #2136
- Produces: Cursor-authored PR with identical HOLD semantics + residual negative tests

- [x] **Step 1: Refresh truth** — main `1ab243ca31`, Render web+worker LIVE parity, #2630 head `ef532f7ee`, App checks present but owner-authored
- [x] **Step 2: Create worktree** `cursor/qa-release-callback-admission-d789`
- [x] **Step 3: Adopt #2630 source** via cherry-pick `--no-commit` of the four semantic commits
- [ ] **Step 4: Strengthen residual negatives** (manual dispatch hard-fail remains; hold step gated; no App mint without `pr_number`/`applicable=true`)
- [ ] **Step 5: Run focused pytest** with venv `python` on PATH
- [ ] **Step 6: Commit + push + open Cursor PR**; request CODEOWNER / independent review; do not merge
- [ ] **Step 7: Record BLOCKED** until independent approvals + authorized merge + postdeploy SHA parity

### Task 2: CUR-DELIV — Route inventory and producing-layer repairs

**Files:** TBD after inventory (hydration/scorer/Action wrappers only; no fitted artifacts)

- [ ] **Step 1: Build sport × market × route matrix** from registry + OpenAPI + open PRs #2560/#2571/#2550/#2526
- [ ] **Step 2: Trace one eligible current event** end-to-end for each claimed restored route
- [ ] **Step 3: Repair PROP_EVIDENCE_SNAPSHOT_NOT_FOUND** only if fixture/lifecycle/hydration defect (not by fabricating snapshots)
- [ ] **Step 4: Authenticated Action canary** or record `NO_CURRENT_EVENTS_VERIFIED` / auth blocker
- [ ] **Step 5: Conservation tests** (0/1/50/51 candidates; typed terminals once)
- [ ] **Step 6: Editor parity** or `LIVE_GPT_EDITOR_SYNC=UNVERIFIED`

### Task 3: Acceptance reporting

- [ ] Emit exact SHA/test/check/Render/Action/SIRT status rows per objective
- [ ] Never claim COMPLETE without independent acceptance receipts
