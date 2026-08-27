# WOW Dashboard — Claude Code Engineering Contract

## 0. Governance ruling — checkpoint-ancestry supersession (2026-08-27)

This ruling reconciles a real contradiction this contract previously contained
between its original rescue→main ancestry requirement (Section 3, as
originally written) and the later, separately approved sanitized clean-origin
reconstruction strategy actually used for the Phase 3/4 migration. It does
not authorize, and was not used to justify, any history rewrite, force-push,
or rebase of `main`, and none was performed.

Verified evidence (this repository, full unshallowed clone):

```text
merge-base(origin/main, origin/rescue/replit-emergency-20260820-1221)
  = 47d5e4f488af6f18645498321ffca4dcc2a8e9b0  (2026-08-05)

e0ffb040cd7376ade1b7f1861ab99b13de37a72c (preserved Replit checkpoint,
  2026-08-20) IS an ancestor of origin/rescue/replit-emergency-20260820-1221
  -- confirmed: git merge-base --is-ancestor ... exit 0

e0ffb040... is NOT reachable from 47d5e4f... -- the checkpoint postdates
  the main/rescue fork point by 15 days, so it was never possible for it
  to be an ancestor of a `main` built from that fork point

origin/rescue/replit-emergency-20260820-1221 is exactly 3 commits ahead of
  e0ffb040..., one of which (b330713, "docs: add Claude Code engineering
  contract") is this file's own origin -- this contract was authored
  directly on the rescue branch, on top of the checkpoint, which is why
  its original ancestry requirement made sense at the time it was written

commit 8c750e9 ("Merge Phase 4 sanitized migration and Task #305 repair"),
  in origin/main's history, has parents 47d5e4f... (old clean main) and
  bee7838... (Phase 4 sanitized transfer branch, confirmed present on
  origin/migration/phase4-transfer-20260825) -- confirming `main` was
  built by reconstructing the migration content from clean main, not by
  merging the rescue branch's history
```

Ruling:

```text
MIGRATION_ANCESTRY_RULING = SUPERSEDED_BY_SANITIZED_RECONSTRUCTION

main_must_descend_from_e0ffb040 = false

checkpoint_preservation_requirement = true
checkpoint_preservation_location =
  origin/rescue/replit-emergency-20260820-1221
  + verified rollback bundle

sanitized_main_authority =
  clean-origin reconstruction
  + reviewed migration PR
  + exact-head CI
  + PR-only merge
  + preserved rescue/checkpoint lineage

history_rewrite_required = false
force_push_required = false
```

Effective immediately, the required invariant is checkpoint **preservation**
(the checkpoint remains intact and reachable on the rescue branch and in the
verified rollback bundle), not checkpoint **ancestry of `main`**. A failed
`git merge-base --is-ancestor e0ffb040... main` must not be read as
repository corruption or history loss, and must not be "repaired" by
merging, grafting, rebasing, reparenting, or force-pushing `main`. Section
3's original ancestry language is retained below for its historical record
and because it still correctly governs the rescue branch itself; it no
longer governs `main`.

Sections 1 and 4 below still describe the original rescue→main A.2 workflow
and its "A.2 = ACTIVE" state. That describes a plan superseded by the
sanitized clean-origin reconstruction actually executed (Phase 3/4 — see
`origin/migration/phase4-transfer-20260825` and its merge into `main` at
commit `8c750e9`). Treat those sections' specific migration-phase claims as
known-stale pending a fuller reconciliation; GitHub `main`'s canonical
status is not blocked on completing the original A.2 rescue→main merge
described there, since that specific mechanism was not the one used.

---

## 1. Authority and purpose

This repository contains the backend implementation for WOW v16 Clean Core.

Claude Code is an engineering implementation agent.

Claude Code is NOT:
- the WOW governance authority
- the betting/model approval authority
- permitted to execute bets or trades
- permitted to weaken fail-closed behavior
- permitted to self-approve governance changes

Architecture:

ChatGPT / WOW governance
→ approved engineering specification
→ Claude Code implementation
→ automated tests / CI
→ review
→ protected GitHub main
→ deployment/runtime backend

GitHub `main` becomes canonical only after the active A.2 migration is successfully completed.

> See Section 0: the rescue→main ancestry mechanism this sentence originally
> assumed was superseded by the sanitized clean-origin reconstruction
> strategy actually used for Phase 3/4.

---

## 2. Absolute safety invariants

These requirements are binding.

```text
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```

No code, test, configuration, API integration, deployment, or agent may:

- place a live wager
- place a market order
- modify or cancel a live wager/order
- imply that live execution occurred
- bypass approval gates
- silently weaken a fail-closed condition

Missing required evidence must fail closed.

No downstream stage may erase an upstream blocker.

Controlling-model failure must remain `MODEL_UNAVAILABLE` where governed.

Do not replace unavailable specialist/model output with trends, L5/L10, market intuition, or qualitative fallback.

---

## 3. Git rules

### Never write directly to `main`

`main` may change only through an authorized merged pull request after required CI and review gates pass.

Do not:

- commit directly to `main`
- push directly to `main`
- force-push any protected branch
- rewrite preserved migration history
- rebase or squash the preserved Replit history unless explicitly authorized
- delete the rescue branch during migration

Normal workflow:

```text
feature/rescue branch
→ focused changes
→ tests
→ push
→ pull request
→ CI
→ review
→ authorized merge
```

During Phase A.2, the active migration branch is:

```text
rescue/replit-emergency-20260820-1221
```

Preserved Replit checkpoint:

```text
e0ffb040cd7376ade1b7f1861ab99b13de37a72c
```

This commit represents the exact preserved Replit source checkpoint and must remain intact in repository history.

HEAD is allowed to advance beyond this checkpoint through authorized migration commits.

Required invariant:

```text
git merge-base --is-ancestor \
e0ffb040cd7376ade1b7f1861ab99b13de37a72c \
HEAD
```

must return exit code `0`.

Do not require current HEAD to equal the preservation checkpoint.

> Superseded for `main` by the Section 0 ruling (2026-08-27): this invariant
> still governs `origin/rescue/replit-emergency-20260820-1221` itself
> (verified ancestor, confirmed exit 0) but no longer governs GitHub `main`,
> which was built via sanitized clean-origin reconstruction rather than a
> rescue→main merge. Do not attempt to satisfy this invariant against `main`
> by merging, grafting, rebasing, reparenting, or force-pushing.

---

## 4. Migration state

Current migration:

```text
A.1 = COMPLETE
A.2 = ACTIVE
A.3 = PENDING
```

> Superseded in part by the Section 0 ruling (2026-08-27): the rescue→main
> merge mechanism described in "A.2 purpose" items 4-8 below was not the
> mechanism actually used to make GitHub `main` canonical — `main` was built
> via sanitized clean-origin reconstruction (Phase 3/4 migration, merged at
> commit `8c750e9`). Treat this section's specific mechanism and
> active-phase claim as known-stale pending a fuller reconciliation of the
> migration-phase model; see Section 0 for what is currently ruled and
> verified.

A.1 closed with one knowingly accepted evidence exception:

```text
literal ancestor_exit=0 shell capture was not obtained during A.1
```

Closure was accepted because:
- reported merge base equaled the historical GitHub main SHA
- reported divergence was 286 ahead / 0 behind
- the rescue branch was remotely preserved
- a verified Git bundle independently preserved the repository state
- A.2 re-tests ancestry by construction

A.2 purpose:

1. bootstrap GitHub Actions CI
2. run authoritative regression baseline
3. classify failures
4. open rescue → main PR
5. establish branch protection
6. merge through reviewed/authorized PR
7. verify the preserved Replit checkpoint remains in ancestry
8. declare GitHub `main` canonical

Do not perform A.3 governance cleanup during A.2 unless explicitly authorized.

---

## 5. A.2 change restrictions

During migration verification:

Allowed:
- CI/workflow infrastructure
- CI-local PostgreSQL setup
- diagnostics
- logging
- dependency-resolution evidence
- minimal infrastructure corrections required to run existing tests

Not allowed without explicit approval:
- production behavior changes
- betting/model logic changes
- probability changes
- calibration changes
- governance semantics changes
- terminal-label changes
- test expectation changes
- skip guards added merely to make CI green
- deletion or weakening of failing tests
- production credential injection

A red test is evidence, not permission to change application behavior.

First classify the failure.

---

## 6. A.2 failure taxonomy

Classify each relevant CI/test failure as one of:

```text
APPLICATION_REGRESSION
CI_ENVIRONMENT_GAP
DEPENDENCY_VERSION_DRIFT
DATABASE_SCHEMA_BOOTSTRAP_GAP
MISSING_EXTERNAL_SERVICE_BY_DESIGN
PREEXISTING_TEST_FAILURE
UNKNOWN
```

Do not modify application behavior until the failure class is established.

Diagnostic infrastructure failures must not unnecessarily suppress unrelated test execution.

Current intended failure hierarchy:

```text
dependency installation failure
    → hard prerequisite; suites cannot run

pip check conflict
    → diagnostic evidence; continue

DB reachability failure
    → CI_ENVIRONMENT_GAP; continue where possible

known schema bootstrap failure
    → DATABASE_SCHEMA_BOOTSTRAP_GAP; continue unrelated suites

collection failure
    → attributable red; individual suites still run where possible

individual suite failure
    → attributable red; remaining suites continue
```

---

## 7. Test surface

The regression surface is NOT only `gate_engine/tests`.

The five required test surfaces live under:

```text
artifacts/flask-scoring-api/
```

Required suites:

```text
gate_engine/tests
kalshi_engine/tests
services/tests
tests/
validation/tests
```

All five must be represented in migration CI.

Repository documentation that describes only `gate_engine/tests/` as the "full regression suite" is known documentation drift and is deferred to A.3.

Do not silently reduce the regression surface.

---

## 8. Current Python/dependency baseline

Known Replit environment at migration:

```text
Python 3.11.14
pytest 9.1.1
```

Current install manifest:

```text
artifacts/flask-scoring-api/requirements.txt
```

Known dependency-governance state:

- dependencies use `>=` bounds rather than reproducible exact pins
- pytest is not declared in the current requirements manifest
- CI dependency resolution may differ from historical Replit resolution
- `uv.lock` exists but uv is not yet declared the authoritative package manager
- root `pyproject.toml` does not currently establish the authoritative build/package-management contract

Do not silently convert the project to uv, Poetry, pinned requirements, or another dependency-management system during A.2.

For the A.2 bootstrap CI baseline:

```text
pytest = 9.1.1
Python = 3.11 minor line
dependencies = requirements.txt resolution
```

Record resolved versions so dependency drift is diagnosable.

---

## 9. CI database policy

Production database credentials must never be used in CI.

Migration CI uses an ephemeral CI-local PostgreSQL 16 instance.

Disposable CI-local database credentials are allowed.

Known special case:

```text
wow_session_exposure
```

is not initialized by its own real-PostgreSQL tests.

The existing public helper:

```python
from gate_engine.pg_session_ledger import ensure_table_exists
```

is verified to exist and may be used to initialize this known CI schema.

The helper creates the `wow_session_exposure` table and its expiry index idempotently.

Known schema bootstrap is diagnostic/non-gating for the overall five-suite baseline.

A failure in schema bootstrap must not prevent unrelated suites from running.

Do not add skip decorators merely to suppress database failures.

Other identified real-PostgreSQL test groups currently bootstrap their own required schema or persistence tables through their existing test/setup paths.

---

## 10. Production state is not Git state

Preserving Git source does NOT prove production reproducibility.

The following remain separate runtime state:

- production database contents
- database migrations already applied to production
- secrets
- provider credentials
- environment variables
- Replit/deployment configuration
- external provider state
- production build/runtime identity

Never claim that cloning GitHub reproduces production unless these states are independently reconciled.

GitHub becoming canonical source means:

```text
canonical source-control authority
```

It does NOT automatically mean:

```text
production runtime equivalence
```

### Migration source vs. Replit workspace

```text
PRESERVED_MIGRATION_SOURCE =
origin/rescue/replit-emergency-20260820-1221
checkpoint e0ffb040cd7376ade1b7f1861ab99b13de37a72c

CURRENT_REPLIT_WORKSPACE =
cef41003841a96459b9d7d4c597e7fabce2cf162

RELATION =
Replit workspace is 1 preservation-only commit ahead
(.agents metadata + rescue bundle; no source, governance, or CI changes)

MIGRATION_AUTHORITY =
rescue branch / e0ffb040 ancestry

REPLIT_WORKSPACE_IS_MIGRATION_AUTHORITY =
false

provenance =
externally verified from the Replit workspace;
not expected to be reachable from the GitHub origin
```

---

## 11. WOW model/governance invariants

Source authority for this summary:

```text
wow-full-model-gatekeeper-SKILL-v2.2-l10-discernment.md
revision = V2.2_TYPED_HYDRATION_L10_DISCERNMENT
project_contract = ACTIVE
```

This section summarizes the active Full Model Gatekeeper contract.

If this summary conflicts with the controlling Gatekeeper specification or a higher-precedence WOW governance authority, the controlling authority wins and the conflict must be reported before implementation proceeds.

Each modeled row routes to exactly one controlling specialist where applicable.

A controlling specialist failure cannot be replaced with:
- generic reasoning
- raw L5/L10
- trends
- market intuition
- qualitative fallback

Raw recent history is evidence only and must be role/opportunity comparable where governed.

Published probability requires controlling-model support and dynamic calibration, including a calibrated numerical lower bound where required.

Probability values must remain valid:

```text
0 < p < 1
```

Multi-outcome markets must normalize appropriately and preserve material draw/push/void states where applicable.

Two-sided props require both directions to be assessed.

Failure of MORE does not approve LESS.

Failure of LESS does not approve MORE.

A reversal requires applicable gates to rerun.

Material failure paths must alter unconditional probability; narrative-only risk discussion is insufficient.

Probability, market edge, settlement, money/EV, and portfolio/slip objectives remain separate.

Correlation/structure and session/directional exposure remain separate controls.

Multi-leg cards must permit weakest-leg removal or replacement and must never force a requested leg count.

Final refresh must recheck applicable:
- event identity/status
- market identity
- price/timestamp freshness
- critical participant status
- settlement identity
- relevant role/lineup/starter status
- weather/venue where applicable
- source conflicts

No downstream stage may upgrade a row past an earlier applicable terminal ceiling.

Until global backend row-level ceiling enforcement is proven:

```text
backend_global_ceiling_enforcement_status = PARTIAL_OR_PENDING
```

the strict project-local ceiling applies.

Final approval requires every applicable mandatory gate to pass.

---

## 12. Known A.3 carry-forward findings

Do not silently repair these during A.2.

### A3-GOV-001

MLB PATCH-010 through PATCH-013 are implemented/running but are not formally registered in the runtime patch registry.

### A3-GOV-002

PATCH-014 / PATCH-015 historical numbering collision requires formal normalization.

### A3-DOC-001

Repository documentation understates the full regression surface.

### A3-DOC-002

Contract README and registry contain known state drift.

### A3-SPEC-001

WOW-v16 master-spec version is known, but the canonical master-spec file pointer is unresolved.

### A3-DEPS-001

pytest is not declared as a test/development dependency.

### A3-BUILD-001

Root `pyproject.toml` lacks the authoritative build-system/package-management contract.

### A3-DEPS-002

`uv.lock` exists without an explicit decision that uv is authoritative.

### A3-DEPS-003

`requirements.txt` uses non-reproducible `>=` dependency bounds.

CI, Replit, and future production environments may resolve materially different package versions.

### A3-CI-001

Bootstrap GitHub Actions and PostgreSQL service configuration use mutable major-version tags.

Evaluate immutable action SHA and container image version/digest pinning after the A.2 baseline is established.

---

## 13. Scope discipline

### Audit provenance

Every repository audit, factual verification, test inventory, migration comparison,
or implementation report must state the exact Git target inspected.

At minimum report:

    audited_branch = <branch>
    audited_commit = <full SHA>
    working_tree_branch = <branch>
    working_tree_head = <full SHA>

When the requested authority is a branch or preserved commit, verify facts against
that Git tree rather than assuming the current working directory represents it.

For migration work, do not use stale `main`, an untracked working tree, or the
current Replit workspace as a substitute for the explicitly designated migration
source.

If the audit target differs from the current checkout, say so explicitly.

No factual repository claim should be presented without identifying the Git
branch/commit from which it was derived.

Before changing anything:

1. inspect the requested files
2. state the intended change
3. identify affected tests
4. avoid unrelated cleanup
5. make the smallest viable diff
6. run the relevant tests
7. inspect `git diff`
8. report exactly what changed

Never bundle unrelated refactoring with a migration fix.

Never stage unrelated files.

Prefer:

```bash
git add -- exact/path
```

Do not default to:

```bash
git add .
git add -A
git add --all
```

If unrelated modifications already exist in the worktree, leave them untouched unless explicitly brought into scope.

---

## 14. Commit and push discipline

### Pre-commit branch verification

Before any commit, verify that the currently checked-out branch is the intended
target branch for the authorized work.

Report:

```text
current_branch = <branch>
current_head = <full SHA>
intended_target_branch = <branch>
```

Do not commit if these do not match the authorized task.
Claude Code auto-generated working branches are not automatically governed
migration or implementation branches.
The existence of a Claude-generated branch does not authorize work to be
committed there.
If branch ownership or target intent is unclear, stop before staging or
committing.

Commits must be small, descriptive, and auditable.

Examples:

```text
ci: bootstrap WOW migration verification workflow
ci: repair CI-local PostgreSQL bootstrap
test: restore migration regression coverage
docs: reconcile WOW governance registry
```

Do not use vague commit messages such as:
- fix stuff
- updates
- cleanup
- working version

Creating/editing a file does not automatically authorize staging.

Staging does not automatically authorize committing.

Committing does not automatically authorize pushing.

Pushing does not automatically authorize opening or merging a pull request.

Respect the explicit authorization boundary for each operation.

During A.2:

```text
direct main write = forbidden
force push = forbidden
history rewrite = forbidden
automatic deployment = forbidden
```

---

## 15. Stop conditions

Stop and request review if:

- a change would alter WOW governance
- a change would alter model probability behavior
- a test would need to be weakened
- production credentials appear necessary
- destructive Git history rewriting appears necessary
- branch ancestry differs from migration assumptions
- `main` would need a direct write
- live execution capability appears enabled
- `can_execute` could become true
- the correct fix is uncertain between infrastructure and application behavior
- an instruction conflicts with this `CLAUDE.md` or the current migration phase
- an instruction references a phase/state that appears already completed, superseded, or inconsistent with repository evidence
- the authority chain for a requested action is unclear

When a stop condition occurs:

1. do not guess
2. do not silently choose an interpretation
3. preserve the current repository state
4. report the evidence
5. wait for explicit resolution
