# V17 reliability follow-up — first implementation increment (2026-11-28)

- status: IMPLEMENTED_PENDING_INDEPENDENT_REVIEW
- scope: reliability decision contracts only
- runtime_generation: V17_ACTIVE
- terminal_authority: V17_TERMINAL_REDUCER
- can_execute: false

## Why these three

The strategic follow-up roadmap ranks engineering reliability first, then
governed model availability, then predictive quality. Before writing anything I
audited the repository so this work extends the ecosystem instead of rebuilding
it. The following capabilities the roadmap asks for **already exist** and were
not duplicated:

| Roadmap capability | Existing implementation |
| --- | --- |
| Authenticated writer admission | `v17/engineering_provider_worker_admission.py` |
| Provider circuit breakers / failover | `v17/engineering_provider_failover.py` (`circuit_breaker_active`, `resident_provider_admission`) |
| Bounded provider retries | `v17/amrt_closure_gate.py` (`provider_attempt`, `MAX_PROVIDER_ATTEMPTS`) |
| Stale-lease re-entry decision | `v17/amrt_closure_gate.py` (`lease_reentry`) |
| Dropped dispatcher-slot recovery | `v17/engineering_dispatch_slot_recovery.py` |
| QA/Release HOLD repair budgeting | `v17/engineering_recovery_planner.py` |
| Closure / exact-SHA receipt gate | `v17/amrt_closure_gate.py` (`closure_gate`) |
| Model-restoration acceptance | `v17/product_outcome_acceptance.py` |
| Per-sport failure taxonomy | `v17/mlb_prospective_failure_taxonomy.py`, `docs`-declared `MODEL_*` semantics |
| All-sport recovery proof preflight | `v17/all_sport_recovery_proof_matrix.py` |

Three gaps were real, and each is a new file with no shared edit surface, so they
can land and be reviewed independently.

## 1. Merge-starvation prevention (#2)

`v17/pr_integration_reservation.py`

Grants exactly one integration window to the oldest eligible pull request, so
"behind main" becomes a refresh task with a reserved slot instead of a repeated
notice. The decision names whether the branch must be refreshed and whether
acceptance must be re-obtained.

Key rule: required-check success recorded against an **older** head never selects
a pull request. A stale green check is not evidence about the current head, which
is exactly how completed work kept re-entering the queue.

The controller still owns refresh, checks, merge and deployment; this module
returns `can_execute: false` and no merge authorization. Unreadable or ambiguous
evidence (unknown `behind_by`, duplicate PR numbers, malformed reservation,
reservation for a moved head) fails closed with an exact blocker.

## 2. Verified Decision Availability SLO (#5, deadline half of #7)

`v17/verified_decision_availability.py`

Measures whether eligible candidates actually received a valid, publishable,
calibrated output before their event began, using the six roadmap metrics:
action completion, model-ready coverage, publishable probability rate, deadline
completion, repeated-blocker rate and user-facing availability.

Honesty rules: an unattested scope, missing event start, untimestamped scored
row, or a publishable claim without a scored output yields `None` plus a finding —
never `0`, never a pass. `evaluate_availability_targets` reports `UNKNOWN` for an
absent target or unmeasured metric and never infers a threshold.

`failure_classes` preserves the six native causes (`CAPABILITY_ABSENT`,
`INPUTS_MISSING`, `REGISTRY_FAILURE`, `SCORING_FAILURE`, `ACTION_FAILURE`,
`CERTIFICATION_HOLD`) so a blocked candidate is attributable to an owner instead
of being collapsed into model unavailability (#6).

## 3. Deadline-aware recovery priority (#7)

`v17/deadline_aware_recovery_priority.py`

Ranks recovery tasks by (1) whether repair can still finish before the earliest
blocked event, (2) blocked-candidate count, (3) shared-producer status, (4)
nearest deadline, (5) stable task identity. Ordering is lexicographic over
attested evidence rather than a synthetic score, so every rank is explainable.

A task whose events have all started is reported as `MISSED_DEADLINE` and listed
in `expired_tasks`; it is never silently dropped or falsely closed. Malformed
tasks are reported under `blocked`, not skipped.

## 4. Dispatcher wiring (added after independent review of the selector)

`.github/workflows/wow-v17-engineering-provider-dispatcher.yml`

The selector is now callable from the dispatcher behind an explicit flag, so it
is used rather than archived:

| Flag | Behaviour |
| --- | --- |
| `integration_selector: "off"` (default) | No GitHub evidence read; a `DISABLED` receipt is written so the artifact is always complete. Routing is byte-for-byte the previous path. |
| repo variable `WOW_INTEGRATION_RESERVATION_ENABLED=true` | Scheduled runs evaluate in `report` mode: reservation receipt, no provider spend. |
| `integration_selector: report` | Manual evaluation, no provider spend. |
| `integration_selector: dispatch` | Evaluation plus a scoped dispatch to the reserved pull **when it needs a code repair**, using the workers' existing `repair_pr` / `repair_head_sha` inputs. A pure refresh is never dispatched. |

Why this is additive rather than a second controller:

- The step is `continue-on-error: true`, so a selector fault degrades to today's
  routing and never blocks provider admission. No gate reads its outcome.
- The existing OpenAI and Anthropic dispatch steps keep every original gate and
  only gain `&& steps.integration.outputs.dispatch_repair != 'true'`, so exactly
  one worker starts per run. Gemini, Cursor and SURVIVAL lanes are untouched.
- Repair dispatch reuses the sanctioned exact-head same-branch repair inputs; it
  does not merge, push, update branches or bypass branch protection.
- The single-writer mutex (`wow-v17-engineering-implementation-single-writer`) is
  unchanged, as is `can_execute: false` in every receipt.
- Receipt validation only tightens: an enabled receipt must be governance-clean
  (`can_execute == false`, `V17_TERMINAL_REDUCER`, boolean decision flags), and a
  claimed repair dispatch must have succeeded.

### Overlap with `wow-v17-engineering-pr-keep-current`

`wow-v17-engineering-pr-keep-current` already keeps open, non-draft
`wow-engineering[bot]` PRs current by calling `PUT /pulls/{n}/update-branch` with
`expected_head_sha` and a wow-engineering App token, precisely because an App-token
update starts the normal PR checks while a `GITHUB_TOKEN` update starts none. Its
`select_prs_to_update` is the single source of truth for that selection, and it
never retries a real conflict — a 409/422 becomes a typed `PR_UPDATE_CONFLICT`
comment routed to a human or the recovery planner.

Its selection is narrower than "behind main": it resolves candidates with the
exact login `wow-engineering[bot]` (`ENGINEERING_BOT_LOGIN`), open and non-draft,
reported `mergeable_state == "behind"`. An ordinary-author PR is never read by
that sweep, so the receipt must not credit it there.

The selector imports `ENGINEERING_BOT_LOGIN` from `engineering_pr_keep_current.py`
rather than re-declaring the login, so the credited owner and the lane that actually
selects the pull cannot drift apart.

Therefore **dispatching a paid provider worker merely to make a branch current
would duplicate an existing lane**. Dispatch mode is narrowed accordingly, and the
refresh owner is claimed only where the sweep would actually see the pull:

| Pull state | Author | Owner | Paid worker? |
| --- | --- | --- | --- |
| Behind main, no conflict | `wow-engineering[bot]` | `wow-v17-engineering-pr-keep-current` (App token → normal CI) | **No.** `refresh_owner` set, `refresh_owner_reason: KEEP_CURRENT_SELECTS_THIS_PULL` |
| Behind main, no conflict | any other author | **no automatic refresh owner** | **No.** `refresh_owner: null`, `refresh_owner_reason: NO_AUTOMATIC_REFRESH_OWNER_FOR_AUTHOR` |
| Real merge conflict (`DIRTY`) | any | Recovery / code repair | **Yes**, exact head via `repair_pr` / `repair_head_sha` |
| Required checks failing on current head | any | Recovery / code repair | **Yes**, exact head |
| Otherwise ready | any | Authorized integration | No dispatch |

If the author evidence is absent, the owner stays unclaimed
(`refresh_owner_reason: AUTHOR_UNATTESTED`), and a behind pull whose merge state is
not attested as `behind` is likewise not credited
(`MERGE_STATE_NOT_ATTESTED_AS_BEHIND`).

So `requires_code_repair` — not `requires_refresh`, and not
`requires_fresh_acceptance` — is the only thing that can set `dispatch_repair`.
Refresh-only reservations are still selected and reported, because starvation is a
priority question; they simply never spend provider credits, and an ordinary-author
behind pull is surfaced for a human/recovery decision rather than auto-refreshed.

Fail-closed evidence rules: `BEHIND` and `DIRTY` read `compare` for `behind_by`; a
non-comparable merge state (`UNKNOWN`, absent) is unattested and cannot select the
pull; an unrecognised merge-state string blocks the inventory. `dispatch_repair`
is only ever true in `dispatch` mode, for a pull with attested code-repair work,
with a writing provider (`openai`/`anthropic`).

### Governance trust roots (CODEOWNERS)

Because the selector credits `wow-v17-engineering-pr-keep-current`, it imports
`ENGINEERING_BOT_LOGIN` from `engineering_pr_keep_current.py` instead of restating
the login. That import is real: `governance_trust_roots()` walks each governance
workflow's file references and their local import closure, so both
`pr_integration_reservation.py` (referenced by the dispatcher) and, through it,
`engineering_pr_keep_current.py` are now owner-approval trust roots. Both are listed
in `.github/CODEOWNERS` and `v17/agent_identity/CODEOWNERS.proposed`; the drift tests
in `test_v17_agent_identity_policy.py` fail if either entry is removed. This adds a
reviewer to those files and weakens no gate.

## Invariants held

- `can_execute=false` in every return value and receipt; no live execution path.
- No probability, calibration, ranking or publication behavior is touched.
- No model absence is relabelled as an input problem, or the reverse.
- The Python modules perform no network, merge, push, branch-update or deploy side
  effect; GitHub reads and the optional worker dispatch live only in the workflow
  step, and dispatch reuses the workers' existing exact-head repair inputs.
- Missing, unattested or ambiguous evidence fails closed with an exact reason.

## Verification

All runs are from `artifacts/wow-engine` with `PYTHONPATH=.`.

**Targeted — the files this follow-up owns or changes (101 passed):**

```
test_v17_pr_integration_reservation.py
test_v17_pr_integration_reservation_wiring.py
test_v17_dispatcher_integration_selector.py
test_v17_verified_decision_availability.py
test_v17_deadline_aware_recovery_priority.py
test_v17_engineering_pr_keep_current.py   # shared ENGINEERING_BOT_LOGIN identity
-> 101 passed
```

The last file is in this set because the selector no longer restates the bot login;
it imports `ENGINEERING_BOT_LOGIN`, so keep-current's own contracts must stay green
for the owner credit to remain truthful.

**Governance — agent identity, trust roots and CODEOWNERS drift (217 passed):**

```
test_v17_agent_identity_policy.py
test_v17_claude_execution_outcome_diagnostic.py
test_v17_claude_native_bootstrap.py
test_v17_global_engineering_app_adoption.py
-> 217 passed
```

**Combined governance + reliability (the four governance files above plus
`test_v17_pr_integration_reservation.py`, `..._wiring.py`,
`test_v17_dispatcher_integration_selector.py` and
`test_v17_engineering_pr_keep_current.py`) — 295 passed.** The SLO and recovery
priority suites are covered by the 101-passed targeted run instead.

**Broad — every workflow, dispatch and concurrency contract in the engine (33
files matching `test_*workflow*.py`, `test_*dispatch*.py`, `test_*concurrency*.py`):**

```
-> 385 passed, 22 subtests passed
```

**Adjacent contracts outside those globs (70 passed):**

```
test_v17_multi_agent_engineering_workflows.py   # also inside the 33 above
test_v17_engineering_ai_resource_resilience.py
test_v17_24h_completion_loop.py
test_v17_exact_p1_bootstrap.py
-> 70 passed
```

**No syntax or static breakage:** the dispatcher workflow parses as YAML and all 14
of its `run:` blocks pass `bash -n`; the three new modules pass `py_compile`.

**Both import paths resolve:** a package import yields
`pr_integration_reservation.ENGINEERING_BOT_LOGIN is
engineering_pr_keep_current.ENGINEERING_BOT_LOGIN`, and the workflow's exact
invocation (`PYTHONPATH=` empty, `python
artifacts/wow-engine/v17/pr_integration_reservation.py select --mode off`) exits 0.
The `except ImportError` fallback is load-bearing: a relative import alone under
direct execution raises `attempted relative import with no known parent package`.

**CODEOWNERS coverage now includes both files.** Importing the login makes
`engineering_pr_keep_current.py` part of the governance trust-root closure, and the
selector was already one as a workflow reference. `.github/CODEOWNERS` and
`v17/agent_identity/CODEOWNERS.proposed` both list
`/artifacts/wow-engine/v17/pr_integration_reservation.py` and
`/artifacts/wow-engine/v17/engineering_pr_keep_current.py @gregoryharper84-ship-it`;
the drift tests in `test_v17_agent_identity_policy.py` (inside the 217) fail if
either entry is removed. Adding coverage assigns an extra required reviewer and
weakens no gate.

## Not done (deliberately)

- No live workflow dispatch, deployment, route readback or production receipt:
  the flag is off by default and nothing here proves a scheduled run selects a
  real PR until an authorized run exists.
- `WOW_INTEGRATION_RESERVATION_ENABLED` is not set by this change; enabling it is
  an owner decision.
- No model fitting, calibration change, merge or deploy of the outstanding PO
  lanes (#2900, #2666/#2663, #2859, #2864, #2891).

## Next ranked work

1. Run one authorized `report` dispatch on protected main, confirm the receipt
   matches live PR state, then decide whether to enable the repository variable.
2. Feed the availability SLO from the real Action/ledger inventory in
   `product_outcome_acceptance` rather than a separate caller-supplied list.
3. Wire `prioritize_recovery_tasks` into the hourly recovery cadence so
   `MISSED_DEADLINE` work stops consuming slots.
