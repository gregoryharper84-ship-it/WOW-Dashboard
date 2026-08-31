# WOW Dashboard — Engineering Contract

## 0. Current authority and phase — 2026-08-30

This repository implements WOW v16 Clean Core.

The historical legacy platform rescue/migration workflow is no longer the active engineering phase. The sanitized clean-origin reconstruction is the canonical source-control path used to establish GitHub `main`.

Current state:

```text
MIGRATION_A1 = COMPLETE
MIGRATION_A2 = SUPERSEDED_CLOSED_BY_SANITIZED_RECONSTRUCTION
MIGRATION_A3 = RECONCILED_INTO_V16_CLOSEOUT
ACTIVE_ENGINEERING_PHASE = V16_CLOSEOUT
CANONICAL_SOURCE_CONTROL = GitHub main
PRODUCTION_WEB_RUNTIME = Render wow-governed-probability-engine
PERSISTENCE_AUTHORITY = Supabase governed schema
legacy platform_RUNTIME_AUTHORITY = false
```

Preserved migration evidence remains historical rollback material and must not be rewritten or deleted merely to simplify history:

```text
preserved_checkpoint = e0ffb040cd7376ade1b7f1861ab99b13de37a72c
preserved_rescue_branch = rescue/legacy platform-emergency-20260820-1221
main_must_descend_from_preserved_checkpoint = false
history_rewrite_required = false
force_push_required = false
```

A failed ancestry test between that historical checkpoint and modern `main` is not a defect and must not be repaired by grafting, rebasing, reparenting, merging rescue history into `main`, or force-pushing.

### Authority order

If instructions conflict, use this precedence:

1. active WOW v16 Clean Core project governance
2. active approved WOW patches
3. active controlling sport/market specialist contract
4. this engineering contract
5. implementation notes, migration history, READMEs, and older patch documents

Historical A.1/A.2 text, legacy legacy platform runtime assumptions, and older Full Model summaries are evidence only and must not override current project governance.

---

## 1. Engineering role

Claude Code, ChatGPT coding agents, GitHub automation, Render, Supabase tooling, and any other engineering helper are implementation capabilities. They are not WOW betting/model approval authorities.

Architecture:

```text
WOW governance
→ approved engineering specification
→ implementation branch
→ automated tests / CI
→ review
→ protected GitHub main
→ controlled deployment/runtime
```

No engineering tool may self-approve a probability, bet, trade, market order, governance exception, or weakening of a mandatory gate.

---

## 2. Absolute safety invariants

These requirements are binding everywhere:

```text
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```

No code, test, configuration, API integration, deployment, agent, workflow, or connected service may:

- place a live wager
- place a market order
- modify or cancel a live wager/order
- imply that live execution occurred
- enable capital allocation
- bypass approval gates
- silently weaken a fail-closed condition

Missing required evidence fails closed.

No downstream stage may erase an upstream blocker.

Controlling-model failure remains `MODEL_UNAVAILABLE` where governed.

Unavailable specialist/model output must not be replaced with raw L5/L10, trends, market intuition, qualitative fallback, sportsbook-implied probability, or synthetic probability.

---

## 3. Active Full Model engineering invariants

Implementations must preserve the active WOW v16 Clean Core workflow and its native terminal semantics.

Required properties include:

- exactly one controlling specialist per modeled row where applicable
- raw L10 is evidence only; role/opportunity comparability and ESS/discernment remain separate gates
- two-sided props assess both directions; failure of MORE does not approve LESS and vice versa
- material failure paths alter unconditional probability
- Calibration Health and Dynamic Calibration are separate gates
- published probability requires controlling-model support plus calibrated lower bounds where governed
- probability domain is strictly `0 < p < 1`
- multi-outcome markets normalize and preserve material push/draw/void states
- exact comparisons require matching event/date/period/stat/line/boundary/settlement/push rules
- probability, edge, settlement, money/EV, and portfolio/slip are separate objectives
- dependency/correlation and session/directional/duplicate-thesis exposure are separate controls
- multi-leg cards run weakest-leg removal/replacement and never force a requested count
- final refresh rechecks event, market, status, critical participants, settlement, price freshness, and conflicts
- every row terminates exactly once
- reconciliation must satisfy `rows_in = rows_completed + rows_held + rows_rejected`
- the final terminal ceiling is the strictest applicable upstream native ceiling

Until global row-level backend enforcement is independently proven:

```text
backend_global_ceiling_enforcement_status = PARTIAL_OR_PENDING
```

The stricter project-local ceiling remains authoritative.

---

## 4. Git rules

### Never write directly to `main`

`main` may change only through a focused pull request after required CI/review gates pass.

Do not:

- commit directly to `main`
- push directly to `main`
- force-push a protected or preserved branch
- rewrite preserved migration history
- delete the rescue/checkpoint lineage as routine cleanup

Normal workflow:

```text
focused feature/fix branch
→ smallest viable diff
→ targeted tests
→ full required CI
→ pull request
→ review
→ authorized merge
```

Before any write, report or determine:

```text
audited_branch
baseline_commit
working_branch
working_head
intended_change
relevant_tests
```

Do not stage or bundle unrelated cleanup.

---

## 5. Test and CI discipline

A red test is evidence, not permission to weaken production behavior.

Classify failures before changing application behavior:

```text
APPLICATION_REGRESSION
CI_ENVIRONMENT_GAP
DEPENDENCY_VERSION_DRIFT
DATABASE_SCHEMA_BOOTSTRAP_GAP
MISSING_EXTERNAL_SERVICE_BY_DESIGN
PREEXISTING_TEST_FAILURE
UNKNOWN
```

Do not:

- delete or weaken failing tests merely to make CI green
- add skip guards solely to suppress a real regression
- use production credentials in CI
- convert a missing fitted model into a qualitative fallback
- promote a research artifact by changing expected test labels

Use ephemeral CI infrastructure for database/queue integration tests.

Legacy Flask regression suites remain historical regression evidence where still exercised, but they are not production runtime authority. New production-path acceptance tests must target `artifacts/wow-engine` and the actual final application entrypoint.

---

## 6. Production state is separate from Git state

Git source alone does not prove production equivalence.

Verify independently where relevant:

- deployed commit SHA
- Render service topology
- environment-variable presence (never expose secret values)
- Supabase migrations/schema
- artifact/model registry state
- calibration cohorts
- provider credentials/configuration
- worker/queue topology
- scheduled graders/hydrators
- live API route surface

Never claim production is fixed merely because code exists on a branch or in `main`.

---

## 7. Runtime identity

Current normal production runtime is the governed FastAPI service under:

```text
artifacts/wow-engine
```

The historical:

```text
artifacts/flask-scoring-api
```

is legacy/reference regression material unless a current approved patch explicitly invokes it.

legacy platform Agent/editor is development-only and is not a normal runtime gate. Its availability or unavailability does not make the WOW model unavailable.

A standalone odds proxy or external market-data service may provide evidence, but it is not a controlling probability specialist.

---

## 8. Persistence and security

Production writes must use governed server-side/service-role paths.

Required patterns:

- RLS remains enabled on governed tables
- client roles do not receive mutation authority for governed ledgers
- sensitive mutation RPCs remain privileged and fail closed
- prediction/evidence/outcome/recommendation identity is immutable where governed
- callers do not supply model probabilities, calibrated bounds, approval labels, or settlement hit flags to trusted write boundaries
- settlement truth is backend-derived from frozen identity plus official outcome evidence

Security hardening must reduce privileges; it must never broaden public/anon/authenticated mutation access.

---

## 9. V16 closeout priorities

The active closeout work is authorized to repair implementation/infrastructure blockers while preserving all model/governance gates.

Priority order:

1. prove the canonical Pick Request route is mounted on the final production entrypoint
2. establish one consistent strict terminal-ceiling reducer across production row paths without upgrading any row
3. complete prediction → official outcome → grading/calibration feedback loops
4. reconcile Render Agent Runtime topology with the declared manifest, or remove/defer the unused topology explicitly
5. normalize Supabase least-privilege grants and immutability controls
6. merge/certify fitted-model source work only when tests pass; production artifact registration remains separately gated
7. close or archive obsolete PR/runtime lines after confirming they are superseded
8. prove deployed SHA/route/schema state after merge

Forward-outcome/calibration thresholds may not be fabricated or bypassed. A closeout implementation may make infrastructure ready while leaving probability publication blocked until real evidence matures.

---

## 10. Stop conditions

Stop and request review only when the requested change cannot be resolved under the authority order above without making a substantive governance/model decision, including when:

- a change would alter probability behavior rather than repair plumbing/implementation
- a test would need to be weakened
- a missing fitted model would need to be replaced with qualitative or market-implied probability
- live execution capability could become enabled
- `can_execute` could become true
- destructive Git history rewriting appears necessary
- `main` would need a direct write
- production credentials would need to be exposed
- two current higher-precedence governance authorities materially conflict
- the correct implementation cannot be determined without choosing between different probability or calibration semantics

A historical phase label or superseded migration instruction is not, by itself, a stop condition once this contract identifies it as historical.

When a real stop condition occurs:

1. preserve repository/runtime state
2. report exact evidence
3. keep affected lanes fail-closed
4. continue unaffected lanes when allowed by active WOW governance
5. do not guess or silently relax a gate
