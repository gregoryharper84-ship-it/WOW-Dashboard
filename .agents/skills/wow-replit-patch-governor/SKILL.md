# WOW Replit Patch Governor

**Load this skill for every WOW patch session.** It enforces a 13-step implementation sequence and a mandatory build-packet contract. It governs *how* Replit modifies WOW. It does not contain probability formulas, sport logic, or candidate-selection rules — those live in `gate_engine/` and in the authoritative WOW contract documents under `docs/wow/contracts/`.

---

## Mandatory Build-Packet Contract

Every WOW patch assignment must arrive with — or have this skill generate — a completed contract before any code is written:

```yaml
change_id:          # WOW-PATCH-YYYY-MM-DD-<SLUG>
objective:          # One sentence: what problem does this solve?
current_problem:    # Observable symptom in production or tests
binding_authority:  # Which WOW document / skill / spec section governs this
allowed_files:      # Exact list of files that MAY be modified
protected_files:    # Files that MUST NOT change (any diff here = immediate stop)
schema_changes:     # DB migrations required (none / list with rollback plan)
api_contract_changes: # Routes added/modified/removed; breaking changes flagged
non_negotiable_invariants:
  - can_execute=false is unconditional
  - No downstream gate may erase an upstream blocker
  - Missing evidence fails closed
  - Terminal labels are preserved verbatim
  - No secrets in code, prompts, logs, tests, or documentation
acceptance_tests:   # Exact assertions that must pass (player_id, games_available, etc.)
mandatory_regressions: # Test files / classes that must pass unchanged
fresh_production_verification: # Live endpoint call with fresh session/run IDs
rollback_condition: # Which failure state triggers a checkpoint rollback
publish_authorized: # true / false — default false; only true when task explicitly includes deploy
can_execute: false
```

If the incoming task does not include this contract, produce it from the task description before proceeding. Do not write a single line of implementation code until the contract is filled in and confirmed.

---

## 13-Step Implementation Sequence

Execute these steps in order. Do not skip, reorder, or combine steps.

### Step 1 — Inspect current code and tests

Read the relevant source files before forming any plan. Use `subagent({ config: { $kind: 'explore' } })` for broad codebase questions. Never assume the current state matches prior knowledge.

Mandatory reads:
- The file(s) named in `allowed_files`
- The test file(s) in `mandatory_regressions`
- `gate_engine/labels.py` if terminal labels are touched
- `gate_engine/data_contract.py` if enrichment fields are touched

### Step 2 — Produce the bounded implementation contract

Fill in the build-packet contract above. Identify every file the patch will touch. Declare the protected files explicitly. If the scope exceeds the contract, stop and ask for a narrower task.

### Step 3 — Identify allowed and protected files

```bash
# Verify protected files are clean before starting
git diff --name-only HEAD
```

If any protected file has uncommitted changes, stop. Do not proceed until the working tree is clean.

### Step 4 — Record API/schema/runtime effects

Before writing code, write down:
- Which Flask routes are added, modified, or removed
- Which enrichment keys are read or written
- Which terminal labels can be reached by the new code path
- Whether the governance hash will change (if gate count, patch count, or precedence changes)

### Step 5 — Create a checkpoint

Ask the user to confirm a Replit checkpoint exists or create one via the Replit UI before starting implementation. Note: Replit production database restoration is separate from code rollback — database migrations need their own recovery plan (`docs/wow/runbooks/db-rollback.md`).

### Step 6 — Implement the smallest complete change

Write only what is required to satisfy `acceptance_tests`. No additional refactors, cleanups, or "while I'm in here" changes. Every line written must map to a requirement in the contract.

Rules:
- Changes must be confined to `allowed_files`
- Any edit to a `protected_file` is an immediate stop condition
- If implementation reveals a larger problem, document it in a new task — do not expand scope

### Step 7 — Run targeted tests

```bash
cd artifacts/flask-scoring-api
python -m pytest <targeted-test-file> -q --tb=short
```

All targeted tests must pass before proceeding. If they fail, fix the implementation (Step 6) — do not move forward.

### Step 8 — Run mandatory regression tests

```bash
cd artifacts/flask-scoring-api
python -m pytest gate_engine/tests/ -q --tb=no 2>&1 | tail -5
```

**Acceptance gate:** `N passed, 1 failed` where the 1 failure is exclusively the runtime isolation sentinel (`test_no_uncommitted_changes_to_forbidden_files`). Any other failure is a regression — fix it before committing.

The isolation sentinel resolves after commit (Step 11). Do not commit to silence it prematurely.

### Step 9 — Audit the diff boundary

```bash
git diff --name-only HEAD
```

Verify:
1. Only files in `allowed_files` appear in the diff
2. No `protected_files` appear in the diff
3. No unrelated files were modified

If any protected or unrelated file appears, revert it immediately:
```bash
git checkout HEAD -- <file>
```

Run the regression suite again after any revert.

### Step 10 — Report raw evidence

Before committing, produce a written summary containing:
- Exact changed files (from `git diff --name-only HEAD`)
- Root cause (traceable to a specific code path)
- Fix description (what changed and why)
- Test result line: `N passed, M failed, K skipped`
- Acceptance criteria status: each criterion from the contract as ✓ or ✗

Do not summarize in prose without the evidence table. The evidence must be reproducible.

### Step 11 — Commit independently

```bash
git add <only allowed_files>
git commit -m "WOW-PATCH-YYYY-MM-DD-<SLUG>: <one-line summary>

Root cause: ...
Fix: ...
Tests: N passed, M failed (runtime sentinel only), K skipped"
```

- One commit per patch packet
- Never bundle governance changes with model/probability/schema changes
- Commit message must reference the `change_id` from the contract

### Step 12 — Publish only when authorized

**Default: do not publish.** Publication is authorized only when `publish_authorized: true` in the contract AND the task explicitly includes a deploy step.

When publication is authorized:
1. Run `bash scripts/wow-preflight` and confirm all checks pass
2. Deploy via Replit deploy button or `replit deploy` CLI
3. Proceed immediately to Step 13

### Step 13 — Verify production using fresh session/run IDs

After every commit (and after any publication), verify the fix end-to-end:

```bash
# Get current governance hash
curl -s http://localhost:25643/wow/governance/status | python3 -m json.tool | grep governance_hash

# Run with fresh IDs (replace with actual values)
python3 scripts/wow-verify-patch <commit-sha>
```

Required production assertions (minimum — supplement with contract's `acceptance_tests`):
- HTTP 200 from `/gate-engine/run`
- `terminal_label` is NOT `DATA_CONTRACT_FAIL` (unless the patch intentionally triggers it)
- No regressions in `mandatory_regressions` test files
- Fresh `session_id` and `research_run_id` used (never reuse IDs from dev sessions)

---

## Invariant Checklist (run mentally before every commit)

| Invariant | Check |
|-----------|-------|
| `can_execute=false` is unconditional | No code path places, routes, modifies, or cancels wagers |
| No blocker erasure | Downstream gates cannot clear upstream blockers |
| Fail-closed | Missing evidence → reject, not accept |
| Terminal labels unchanged | `PropLabel` enum members are verbatim; no renames |
| No secrets in artifacts | `git grep -i "api_key\|password\|secret\|token" -- "*.py" "*.md" "*.yaml"` |
| Diff is bounded | `git diff --name-only HEAD` matches `allowed_files` exactly |
| Governance hash stable | Hash only changes when gate count, patch count, or precedence changes |

---

## Stop Conditions

Immediately stop implementation and report to the user when:

1. A protected file appears in `git diff --name-only HEAD`
2. A regression test fails that was passing before this session
3. The fix requires modifying more files than declared in `allowed_files`
4. A schema change is needed that has no documented rollback plan
5. The governance hash changes unexpectedly
6. Any live endpoint test returns a terminal_label that the contract does not account for

---

## Scope Boundaries

This skill governs **engineering process only**. It does not define or override:
- Probability formulas or calibration methods
- Sport-specific scoring rules (MLB, NBA, WNBA, NFL, Tennis, Kalshi)
- Terminal label definitions or precedence
- LLP governance rules
- Settlement or ledger logic

For those, load the appropriate domain skill from `docs/wow/contracts/` and treat its requirements as binding authority in the contract's `binding_authority` field.

---

## Quick Reference: Key File Locations

| Purpose | File |
|---------|------|
| Terminal labels | `gate_engine/labels.py` |
| Required enrichment fields | `gate_engine/data_contract.py` |
| Pipeline entry point | `gate_engine/pipeline.py` → `run_pipeline()` |
| Row normalization | `gate_engine/board_intake.py` → `normalize_board()` |
| Auto-enrichment | `gate_engine/auto_enrichment.py` → `build_auto_enrichment()` |
| Acquisition key-promotion | `gate_engine/acquisition_orchestrator.py` → `_check_prop_game_log()` |
| Governance | `gate_engine/llp_governance.py` → `run_llp_governance()` |
| Flask routes | `artifacts/flask-scoring-api/app.py` |
| All tests | `gate_engine/tests/` |
| Isolation sentinel | `gate_engine/tests/test_stage_a_isolation.py` |
