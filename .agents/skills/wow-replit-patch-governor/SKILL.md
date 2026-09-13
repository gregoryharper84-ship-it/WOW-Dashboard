# WOW Patch Governor — GitHub / Render / Supabase Compatible

**Compatibility identity:** `wow-replit-patch-governor`

Load this skill for every WOW patch session. The historical skill name is preserved for compatibility, but the process is **platform-neutral**. For the current WOW V17 runtime, GitHub is the source-control/CI authority, Render is the production runtime, and Supabase is the governed persistence layer where applicable. Replit-specific instructions are non-authoritative unless the active repository/runtime explicitly uses Replit.

This skill governs *how* WOW is modified. It does not define probability formulas, sport logic, calibration policy, terminal authority, or candidate-selection rules.

---

## Operating modes

### Interactive mode

Default for ad-hoc engineering requests. Publication/deployment requires task-level authorization when the request does not already grant it.

### Nightly autonomous mode

Active when invoked by `WOW_V17_NIGHTLY_ENGINEERING_AUTOPILOT`.

In nightly autonomous mode:

- R0, R1, and eligible R2-restorative changes are pre-authorized to proceed through bounded patching, protected CI, merge, applicable Render deployment, production replay, and closure;
- no extra operator checkpoint is required between engineering stages;
- CI/review/QA/production failures re-enter the repair loop instead of terminating the shift;
- publication authority is derived from the nightly Morning-Green Contract, not from a default `publish_authorized=false` stop;
- all hard boundaries below remain binding.

---

## Mandatory Build-Packet Contract

Every WOW patch assignment must arrive with — or have this skill generate — a completed contract before code is written:

```yaml
change_id:              # WOW-PATCH-YYYY-MM-DD-<SLUG>
objective:              # One sentence: what problem does this solve?
current_problem:        # Observable symptom in production or tests
binding_authority:      # Governing WOW contract / skill / spec
operating_mode:         # interactive | nightly_autonomous
risk_class:             # R0 | R1 | R2-restorative | R2-repair-policy | R3
allowed_files:          # Exact files that MAY be modified
protected_files:        # Files that MUST NOT change
schema_changes:         # none, or migrations + rollback plan
api_contract_changes:   # routes/actions modified; semantic vs restorative noted
non_negotiable_invariants:
  - can_execute=false is unconditional
  - DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
  - No downstream gate may erase an upstream blocker
  - Missing evidence fails closed
  - V17_TERMINAL_REDUCER remains sole global terminal authority
  - No secrets in code, prompts, logs, tests, or documentation
acceptance_tests:       # Exact assertions that must pass
mandatory_regressions:  # Required tests/checks
fresh_production_verification: # Exact live replay/probe when production-facing
rollback_condition:     # Failure state triggering deterministic rollback
publication_authority:  # interactive_explicit | nightly_pre_authorized | none
hard_boundary_review:   # PASS | BLOCKED
can_execute: false
```

If the incoming task does not include this contract, produce it from the task description before implementation. In nightly autonomous mode, do not ask the operator to reconfirm a contract that can be derived safely from the incident and governing skills.

---

## Hard boundaries

This governor may not autonomously cross any of the following:

1. train, certify, promote, or replace a production fitted sporting model;
2. change sporting probability formulas, calibration math, qualification thresholds, or lower-bound policy;
3. change `V17_TERMINAL_REDUCER` authority or terminal precedence;
4. enable/broaden `can_execute` or live wager/order execution;
5. create, rotate, replace, or expose secrets/credentials;
6. weaken auth, RLS, required checks, branch protection, or governance controls;
7. make destructive/irreversible schema or data changes;
8. create paid infrastructure or materially increase recurring spend without authorization;
9. promote synthetic/test artifacts as production authority.

If one is required and no safer bounded repair exists, return `BLOCKED_HARD_BOUNDARY` with the exact dependency/authorization needed.

---

## 13-Step Implementation Sequence

Execute these steps in order. Rework loops may return to earlier steps as specified.

### Step 1 — Inspect current code, tests, and runtime truth

Read the relevant source/tests before planning. Inspect current `main`, open PRs, CI, Render, and Supabase/runtime evidence when relevant. Never assume yesterday's diagnosis is still current.

Mandatory reads include:
- files named in `allowed_files`;
- tests in `mandatory_regressions`;
- governing domain contract when semantics are affected;
- terminal/data-contract files when directly relevant.

### Step 2 — Produce the bounded implementation contract

Fill the build packet. Declare allowed/protected files, risk class, runtime effects, acceptance tests, rollback, and hard-boundary review.

If implementation reveals a separate defect, split it into a separate packet rather than silently expanding scope.

### Step 3 — Verify branch/diff boundary

Work on a dedicated branch from the intended current base. Confirm no protected/unrelated changes are included in the candidate diff.

If protected changes already exist on another branch/PR, do not silently inherit them; use a clean current base or explicitly stack when the dependency is intentional and reviewable.

### Step 4 — Record API/schema/runtime effects

Before implementation, record:
- routes/Actions added or modified;
- persistence fields/contracts read or written;
- terminal labels reachable;
- governance-hash impact if any;
- whether the change is semantic or restorative;
- deployment/runtime services affected.

A restorative Action/schema repair that preserves already-authoritative meaning can be R2-restorative; a new meaning/policy remains R2-repair-policy or R3 as applicable.

### Step 5 — Establish rollback point

Use Git commit/branch identity as the code rollback point and record the currently deployed Render SHA when production-facing.

For database migrations, require an explicit reversible migration/rollback plan before mutation. Do not depend on a Replit checkpoint unless Replit is actually the active platform.

### Step 6 — Implement the smallest complete change

Write only what is required to satisfy acceptance criteria.

Rules:
- remain within `allowed_files`;
- preserve all immutable V17 invariants;
- no unrelated refactor/cleanup;
- no fake model support or synthetic authority;
- no sportsbook/generic reasoning substituted for a missing specialist.

### Step 7 — Run targeted tests

Run the smallest relevant deterministic test set first. All targeted acceptance tests must pass before review.

If a targeted test fails:
- inspect the exact failure;
- determine whether implementation or stale test expectation is wrong;
- repair the correct layer;
- rerun.

In nightly mode, a test failure is a **rework transition**, not a stop condition, unless it reveals a hard boundary.

### Step 8 — Run mandatory regression/protected checks

Run repository-required regression suites and protected CI contexts appropriate to the change.

Never remove, bypass, rename, or weaken required checks to force green.

If CI fails in nightly mode:

```text
CI_FAIL -> inspect failing job/log -> root cause -> Engineering rework -> targeted tests -> CI rerun
```

Continue until green or a true hard boundary is proven.

### Step 9 — Audit the diff boundary

Verify:
1. only allowed/intentionally stacked files are changed;
2. no protected files changed unexpectedly;
3. no unrelated files changed;
4. generated/migration artifacts match the declared contract.

Unexpected protected changes return to Engineering for a clean bounded patch; they are not automatically a user checkpoint.

### Step 10 — Produce evidence packet

Record:
- exact changed files;
- root cause tied to a code/runtime path;
- fix description;
- targeted test results;
- required CI/check results;
- acceptance criteria ✓/✗;
- rollback plan;
- intended deploy SHA;
- `can_execute=false` verification.

### Step 11 — Commit and open/update PR

- one bounded change packet per logical patch unless an intentional dependency stack is documented;
- commit message references `change_id`;
- PR body includes root cause, risk class, invariants, tests, rollback, and acceptance plan;
- independent review remains mandatory.

No implementer may self-approve the architectural correctness of its own patch.

### Step 12 — Merge/deploy according to authority

#### Nightly autonomous mode

For R0, R1, and eligible R2-restorative:

1. require Independent Review and System Architect review where applicable;
2. require all protected checks green on the exact head;
3. merge through normal branch protection when permitted;
4. allow normal Render auto-deploy or trigger a redeploy only when a code/config condition means auto-deploy will not occur;
5. never bypass required checks or protections;
6. proceed immediately to production verification.

#### Interactive mode

Publish/deploy only when the task explicitly authorizes it or the user subsequently approves it.

R2-repair-policy and R3 remain blocked unless explicitly authorized at the required level.

### Step 13 — Verify production with fresh evidence

For production-facing repairs, confirmation requires all applicable items:

- exact deployed SHA/version matches intended repair;
- Render service is healthy;
- relevant `/health`/governance readiness is truthful;
- original or closest equivalent defect replay passes;
- persistence/reconciliation invariants pass where applicable;
- no new P0/P1 logs attributable to the patch;
- `can_execute=false` remains false;
- dry-run-only remains active;
- terminal authority remains unchanged.

A patch is not `FIXED_VERIFIED` until this step passes.

If production verification fails:

```text
PROD_VERIFY_FAIL
  -> isolate production-only cause
  -> if safe repair exists: Engineering rework loop
  -> else if deterministic rollback authorized: rollback + verify
  -> else: ROLLBACK_REQUIRED or BLOCKED_HARD_BOUNDARY
```

---

## Nightly Morning-Green Loop

When this governor is invoked by the nightly autopilot, the complete per-incident loop is:

```text
REPRODUCED
 -> ROOT_CAUSE_CONFIRMED
 -> PATCH
 -> TEST
 -> REVIEW
 -> QA
 -> PROTECTED_CI
 -> MERGE
 -> DEPLOY
 -> PRODUCTION_REPLAY
 -> FIXED_VERIFIED
```

Any failure from TEST through PRODUCTION_REPLAY loops back to Engineering unless it proves a hard boundary.

Intermediate states are not completion states.

---

## Invariant Checklist

| Invariant | Check |
|-----------|-------|
| `can_execute=false` unconditional | No path places/routes/modifies/cancels wagers |
| Dry-run only | No live market-order capability |
| No blocker erasure | Downstream gates cannot clear upstream blockers |
| Fail-closed | Missing evidence -> reject/hold, not accept |
| Terminal authority | `V17_TERMINAL_REDUCER` remains sole reducer |
| One sporting specialist | No cross-sport/generic substitution |
| No secrets | No credentials in artifacts/logs/tests/docs |
| Diff bounded | Candidate matches allowed scope |
| Required checks intact | No check/protection weakening |
| Governance hash expected | Changes only when declared and authorized |

---

## Rework vs stop conditions

### Rework conditions — do not stop nightly execution

- targeted regression fails due to the candidate;
- CI/protected check fails;
- independent review rejects with actionable bounded findings;
- QA reproduces the defect after patch;
- production replay fails for a bounded deterministic reason;
- diff includes an accidental unrelated file that can be safely removed;
- a stale test assertion conflicts with already-authoritative current behavior and can be corrected without semantic expansion.

These return to Engineering.

### Hard stop conditions

Stop autonomous mutation only when:

- a hard boundary listed above is required;
- safe permissions/tools for required repository/deployment action are unavailable;
- rollback is required but cannot be executed safely;
- the requested repair would necessarily weaken a V17 safety/governance invariant.

Report the exact missing capability/dependency. Do not pretend the issue is fixed.

---

## Scope boundaries

This skill governs engineering process only. It does not define or override:
- probability formulas or calibration methods;
- sport-specific scoring rules;
- terminal label definitions/precedence;
- LLP sporting governance rules;
- settlement/ledger semantics;
- model certification criteria.

For those, load the authoritative domain contract and treat it as binding authority in the build packet.

---

## Current platform reference

For the active WOW V17 architecture unless repository evidence says otherwise:

| Responsibility | Authority |
|---|---|
| Source control / PR / protected CI | GitHub |
| Production runtime / deploy | Render |
| Governed persistence / SQL | Supabase |
| Global terminal reduction | `V17_TERMINAL_REDUCER` |
| Live execution | disabled (`can_execute=false`) |

The historical `wow-replit-patch-governor` name remains only to avoid breaking callers that reference the existing skill identity.
