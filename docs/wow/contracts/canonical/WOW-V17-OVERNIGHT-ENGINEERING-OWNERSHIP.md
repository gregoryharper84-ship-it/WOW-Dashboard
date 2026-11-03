# WOW V17 Overnight Engineering Ownership

**Status:** ACTIVE GOVERNANCE CONTRACT  
**Owner:** WOW V17 Engineering Team  
**Runtime generation:** `V17_ACTIVE`  
**Terminal authority:** `V17_TERMINAL_REDUCER`  
**Execution:** `can_execute=false`  
**Trading:** `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`

## Purpose

The WOW V17 overnight cycle is an engineering-owned operating process. It is not an unattended batch that may be left incomplete for Greg to diagnose the next morning.

The engineering team owns the overnight cycle from pre-run inspection through reconciliation and morning receipt.

## Overnight ownership contract

The engineering team SHALL:

1. inspect relevant open incidents, CI, scheduled workflows, Render deployment/runtime health, Supabase persistence/receipt health, model/capability registries, data-source freshness, and unresolved prior-run state before the overnight run;
2. run or observe the scheduled overnight engineering diagnostics before downstream Scout discovery;
3. detect incomplete, failed, ambiguous, duplicated, stalled, or partially reconciled engineering work;
4. reproduce and root-cause safely repairable failures when evidence and tooling permit;
5. implement the smallest safe repair, run the narrow reproduction, then relevant regression coverage;
6. complete the permitted PR/merge/deploy/production-verification lifecycle when authority and protected-branch governance allow it;
7. preserve exact-once and immutable prediction/persistence semantics; never manufacture completion where receipts or reconciliation cannot prove it;
8. leave every discovered issue in an explicit engineering terminal state:
   - `FIXED_AND_VERIFIED`
   - `PR_CREATED`
   - `EXPERIMENT_CREATED`
   - `DUPLICATE`
   - `NOT_REPRODUCIBLE`
   - `BLOCKED_WITH_EXACT_REASON`
   - `DEFERRED_WITH_JUSTIFICATION`;
9. target `UNFINISHED=0` for every safely repairable overnight incident;
10. produce the morning report as a receipt of completed engineering activity rather than as a substitute for the engineering work.

## Separation of authority

Engineering ownership of the overnight cycle does **not** make the engineering team a competing probability model.

The following remain invariant:

- exactly one controlling fitted specialist owns each sporting probability row;
- Scout and Research gather evidence only;
- sportsbook implied probability, public projections, recent results, or generic LLM reasoning may not substitute for a governed fitted-model probability;
- typed V17 failure semantics must be preserved;
- `V17_TERMINAL_REDUCER` remains the sole global terminal authority;
- production sporting probability behavior may not be silently changed;
- Class C probability-producing changes require challenger/replay/counterexample/validation/regression/governed review before promotion;
- `can_execute=false`;
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`.

## Overnight sequencing

The existing separation remains intentional:

1. overnight engineering diagnostics/repair run first;
2. V17 Nightly Multi-Scout runs afterward for discovery/evidence collection;
3. Scout failures remain source/discovery failures and are not relabeled as model failures;
4. engineering repairs must clear the applicable protected verification path before downstream consumption.

Ownership spans the full overnight operating cycle while preserving these lane boundaries.

## Required morning receipt

Every overnight engineering cycle reports:

- `OPEN_AT_START`
- `NEW_INCIDENTS`
- `FIXED_VERIFIED`
- `HARD_BLOCKED`
- `UNFINISHED`
- `PRs_CREATED`
- `PRs_MERGED`
- `DEPLOYS_VERIFIED`
- `REGRESSIONS`
- `IMPROVEMENT_EXPERIMENTS`

For safely repairable incidents, the target is:

`UNFINISHED=0`

Any nonzero unfinished count must identify the exact remaining blocker or governed decision rather than a vague follow-up note.

## Independent-provider fallback candidate (#2000)

Cursor is `CURSOR_NOT_CONFIGURED`: its current REST contract and this account's
API/repository entitlements have not been verified. No Cursor request is made.
An editor subscription is not an API credential, quota, or spending authorization.

The existing provider dispatcher retains the #1998 OpenAI persistent quota/auth
breaker and failed-Claude suppression. Its optional fallback uses GitHub's
[documented existing-issue Copilot assignment API](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/use-cloud-agent-via-the-api),
not the OpenAI API key or Engineering App installation token. Copilot authors
under its own `copilot-swe-agent` app principal (GitHub bot ID `198982749`);
assignment acceptance is not source delivery, review, or approval. Copilot uses
its own account entitlement/credits and is not asserted to be free.

Activation is owner-controlled and disabled by default. Before enabling, configure
a separately scoped **user** credential `WOW_COPILOT_ASSIGNMENT_TOKEN` restricted
to this repository, with the permissions required by GitHub's API documentation.
Do not substitute an Actions/Engineering App installation token. Verify the
account's Copilot entitlement and configure an owner-approved GitHub spending
budget with usage stopped at its limit. Only then set repository variables:

- `WOW_COPILOT_FALLBACK_ENABLED=true`
- `WOW_COPILOT_FALLBACK_REPOSITORY=gregoryharper84-ship-it/WOW-Dashboard`
- `WOW_COPILOT_FALLBACK_BUDGET_APPROVED=true`
- `WOW_COPILOT_FALLBACK_MAX_ASSIGNMENTS=1`

These flags attest owner setup; they do not create or verify a billing cap.
This patch does not configure secrets, budgets, permissions, or launch agents.

The default budget is **zero additional API spend**. GitHub-native scheduled
detection and deterministic SURVIVAL checks continue while OpenAI/Claude are at
quota limits; they do not require enabling Copilot or configuring Cursor. There
is no automatic recharge or paid remote invocation in this default path.
The existing Render resident dispatcher heartbeat is a separate liveness signal,
not repair acceptance, a provider entitlement, or authorization to spend. Its
dispatches still pass through the same quota breaker and opt-in admission gates.

### Resident pre-dispatch admission (#2001 follow-through)

The resident reads the existing protected-main provider-route artifact and
protected worker completion evidence through GitHub only; it never probes a
paid provider or assumes that configured budget flags prove provider health.
Known persistent quota/auth blockage with no admitted fallback returns
`PROVIDER_QUOTA_BLOCKED` before GitHub dispatch and before attempt accounting.
Malformed, missing, expired-artifact or unreadable evidence fails closed.
Transient throttles remain distinct: expiry of their cooldown is not proof of
recovery. Positive admission must be fresh (15 minutes) on current main; a fresh
successful protected worker, including its exact parent-linked `NO_ACTION`
acknowledgment, can supersede an older blocker.

Remote reservations and existing heartbeat leases are checked before dispatch.
The existing Redis resident lease reserves a persistent pending handoff before
the GitHub POST. Only an accepted POST, or the exact correlated workflow run
found after an ambiguous response, increments the existing bounded attempt
counter. Lua compare-and-set makes accounting exactly once. The pending handoff
does not expire automatically; unknown, absent or duplicate run evidence holds
for reconciliation, never a resend. A completed correlated run clears only the
handoff, not the counter. Provider holds are persisted as `DEGRADED` outcomes by
the existing resident heartbeat path.

### Provider-canary parity for resident admission

A fresh `DISPATCH` receipt carrying one of the dispatcher's owner-attested
Gemini canary reasons (`GEMINI_EXPLICIT_AUTO_ROUTING_WITH_HEALTHY_CANARY`,
`GEMINI_ONE_SHOT_AFTER_REVIEWED_FIX`) is admitted exactly like the
`openai`/`anthropic` routes: the same 15-minute freshness bound, the same
`can_execute=false` and terminal-authority checks, and the same rule that a
later failed or unclassified run of that provider's own worker invalidates it.
A manual `GEMINI_OPERATOR_CANARY` receipt never grants unattended admission,
because one human-run probe attests neither standing auto-routing configuration
nor an approved spend cap. This parity grants no provider quota, budget,
probability authority or execution authority.

### Malformed worker inventory is scoped per provider

Admission previously failed closed globally whenever *any* registered worker's
run inventory was malformed or unclassifiable. That is stricter than the
evidence requires, and it obscured real provider state: one unreadable Gemini
run could revoke a fresh, fully verified OpenAI or Anthropic route. The rule is
now scoped to the route's own provider.

- For an `openai`, `anthropic` or `gemini` route, another provider's malformed
  or unclassifiable run inventory is skipped instead of revoking the route.
- The route's own provider still fails closed on malformed, missing or
  unclassifiable evidence, returning `PROVIDER_STATUS_UNVERIFIED`.
- Every other route shape (`NO_ACTION`, `cursor`, provider `none`, or an
  unrecognized provider) keeps the previous global fail-closed behaviour.

This is a deliberate, reviewed narrowing of a condition that was global
fail-closed, and it is recorded here rather than only in a code comment for that
reason. It grants no admission by itself: a fresh route receipt, the 15-minute
freshness bound, the `can_execute` and terminal-authority checks, the current
`main` SHA match, the same-provider failure veto and the one-writer fence all
still apply. Provider run histories are independent evidence, so an unreadable
unrelated history is missing evidence about that provider, not about this route.
Covered by `test_unrelated_unclassified_worker_does_not_revoke_valid_gemini_route`.

The three-attempt ceiling and its existing retention remain unchanged. Previously
exhausted counters are not reset, increased or automatically forgiven by this
repair. After genuinely admitted provider recovery, those incidents still require
their existing accountable owner to triage the exhausted attempt history.
If only opt-in Copilot is configured but no affirmative admission is proven, keep
the resident held: the existing protected dispatcher/independent-provider owner
must supply capability and delivery evidence through its existing lane. Do not
create a parallel resolver, infer readiness from flags, or launch a paid recovery
probe to clear the hold.

Admission requires an exact open issue already in the dispatch manifest, its
matching domain lease, protected `main`, a concrete current main SHA, no existing
assignment/linked repair PR, and a successful user-context repository capability
probe. No free-text incident body, environment, Render/Supabase key, or betting
Action secret is copied into the assignment prompt. GitHub's assignment API takes
a branch rather than an immutable commit; the prompt binds the observed SHA and
requires stopping if it changes, and source acceptance verifies the exact base.

The existing implementation-single-writer Actions lock serializes admission.
Before assignment, a typed reservation is persisted in the existing #1138
heartbeat ledger, keyed by incident. Both workers and dispatcher fence unresolved
remote holds, including after timeout or workflow completion. Assignment has
**zero automatic retries**: 401/402/429/5xx, malformed output and timeouts retain
the hold because remote launch may already have occurred. Subsequent survival
cycles reconcile the same reservation, never assign again. Receipts distinguish
`REMOTE_QUEUED_NOT_SOURCE_ACCEPTED` from
`SOURCE_PR_AWAITING_INDEPENDENT_REVIEW`; neither means fixed or verified.

An unresolved reservation intentionally has no automatic expiry. The accountable
owner must first prove remote termination, reconcile the PR/source SHA and obtain
independent review, then explicitly change that reservation's `hold` to `false`
in the #1138 ledger. Preserve the receipt and incident marker: replay remains
blocked even after release. Never delete a reservation merely to retry. Missing
or malformed ledger evidence fails closed; no new scheduler or incident store
is introduced.

This remains `SOURCE_CANDIDATE_NOT_DEPLOYED`. Exact-head protected CI and
independent review are required. Operational acceptance still requires two real,
owner-budget-authorized unattended cycles yielding independently reviewed repair
PRs. No merge, deployment, sporting probability authority or wager execution is
authorized here; `can_execute=false` and `V17_TERMINAL_REDUCER` remain unchanged.


## Owner-controlled Gemini recovery runbook (#1021; PR #2408)

**Default is no additional provider spending.** This is an operational procedure
for an already-approved, limited provider canary, not blanket spend permission,
a release waiver or a request to start a job. The owner controls any billing cap
and the decision to invoke a potentially metered Gemini run. Repository variables
attest policy state; they **do not** create, verify or enforce a provider billing
limit. Do not enable Gemini automatically to clear `PROVIDER_QUOTA_BLOCKED`.

### A. Zero-spend preflight (read-only)

1. Confirm the admission PR has **independent exact-head acceptance** and has
   been merged through protected `main`. A feature-branch test or local pass
   cannot activate the deployed resident; check actual Render web/worker source
   parity and the deployed resident implementation independently.
2. On the existing GitHub repository, inspect the latest provider-dispatcher and
   Gemini-worker workflow runs, their *immutable* run IDs, conclusions, source
   SHAs and applicable receipt artifacts. Check the #1138 remote writer ledger
   for effective authenticated releases; an immutable old `hold=true` record
   can have a separate signed release receipt. Never rerun a successful finalizer.
3. Confirm the existing single-writer lease, no unresolved dispatch reservation,
   and an exact eligible incident/lease pair from
   `artifacts/wow-engine/v17/engineering_dispatch_manifest.json`.
   Do not create a second scheduler or use an arbitrary incident to force a
   provider canary. Inspect current `PROVIDER_QUOTA_BLOCKED`/breaker evidence.
4. Inspect **names and non-secret flags only**; never print secret values:

   ```bash
   REPO=gregoryharper84-ship-it/WOW-Dashboard
   gh pr view 2408 -R "$REPO" --json state,headRefOid,mergeStateStatus
   gh run list -R "$REPO" --workflow wow-v17-engineering-provider-dispatcher.yml --limit 5
   gh run list -R "$REPO" --workflow wow-v17-gemini-engineering-worker.yml --limit 5
   gh variable list -R "$REPO"
   gh secret list -R "$REPO"
   ```

   A listed `GEMINI_API_KEY` secret does **not** prove that it works or carries
   quota. Verify owner-provider entitlement, budget exhaustion behavior and the
   actual stop-spend control out of band; do not use trial-and-error paid probes.

### B. Owner-attested one-shot manual canary (metered; **not automatic**)

**Prerequisites:** approved available Gemini entitlement and credential;
separately established provider budget/stop-spend policy; no conflicting writer
or unresolved handoff; a selected open issue with its manifest-owned lease;
the merged protected-main workflow; one specifically authorized canary only.

The existing `wow-v17-engineering-provider-dispatcher.yml` workflow accepts a
manual `force_provider=gemini` dispatch and launches the existing
`wow-v17-gemini-engineering-worker.yml` in manual mode. The worker requires
`GEMINI_ENGINEERING_ENABLED=true`, `GEMINI_SPEND_CAP_CONFIRMED=true` and a
configured `GEMINI_API_KEY` secret. Keep `GEMINI_AUTO_ROUTING_ENABLED=false`
until the manual result and durable provider history are reviewed. Only the
owner may attest/change these flags after independently confirming the actual
provider budget. **No automation, this documentation, or ChatGPT response
should flip them.**

Invoke via the existing dispatcher using a manifest-selected target and lease;
do **not** run with placeholders or repeatedly retry ambiguous results:

```bash
# METERS PROVIDER USAGE. OWNER ACTION ONLY AFTER ALL PREREQUISITES.
# Replace both placeholders with the same manifest-approved incident/lease.
gh workflow run wow-v17-engineering-provider-dispatcher.yml \
  -R gregoryharper84-ship-it/WOW-Dashboard --ref main \
  -f force_provider=gemini \
  -f reason=GEMINI_OPERATOR_CANARY \
  -f target_incident=MANIFEST_INCIDENT_NUMBER \
  -f lease_group=MANIFEST_LEASE_GROUP
```

The resulting `GEMINI_OPERATOR_CANARY` can establish bounded manual health
evidence, **never resident automatic admission**. Record actual parent/child run
IDs, run attempts, tested source SHA, typed model/patch/regression receipts,
verified provider outcome, cost/quota observation and any negative case. Do not
confuse `workflow_dispatch` acceptance with completed code or a reviewed PR.

### C. Admit auto-routing only after durable evidence and an owner decision

Only if the operator canary is valid, provider capacity and stop-spend controls
remain confirmed, and review has established safe single-writer history, the
owner may consider `GEMINI_AUTO_ROUTING_ENABLED=true`. The protected dispatcher
must **independently** emit a fresh `DISPATCH/gemini` route with one of:

- `GEMINI_EXPLICIT_AUTO_ROUTING_WITH_HEALTHY_CANARY`
- `GEMINI_ONE_SHOT_AFTER_REVIEWED_FIX`

Those reasons must be backed by its own config/history tests, not manually
asserted in JSON or a forced reason string. The resident re-checks receipt
integrity, ≤15-minute freshness, current `main` SHA and later same-provider
worker failures before admission. It also retains the existing provider cooldown,
attempt limits, pending handoff accounting and the one-writer fence.
A recent manual success does not override a cooldown, and waiting does not cure
persistent quota or authentication failures.

The schedule, not the canary, is what turns an attested route into admission.
The dispatcher's automatic routing runs on the hourly `17 * * * *` cron, and the
resident consumes only the **latest completed** dispatcher receipt while it is at
most 15 minutes old. After the owner sets `GEMINI_AUTO_ROUTING_ENABLED=true`,
admission therefore becomes possible only inside the window between that hour's
`:17` dispatcher run and 15 minutes later; the resident cycle that observes the
receipt inside that window is the one that admits. A canary, a manual probe, a
forced reason string, a `SURVIVAL` receipt or a dispatcher run older than 15
minutes all resolve to `PROVIDER_RECOVERY_UNVERIFIED` or
`PROVIDER_STATUS_UNVERIFIED`, and each later dispatcher run replaces the earlier
receipt rather than stacking with it. Expect at most one admission opportunity
per hour, not continuous admission, and do not read a quiet hour as a new
blocker.

### D. Stop, reconcile and independently accept

On `GEMINI_API_QUOTA_EXHAUSTED`, bad credential, missing receipts, stale or
unclassified history, provider error, ambiguous dispatch or any new writer
conflict: **stop additional provider invocations**, disable owner-controlled
automatic Gemini routing, retain exact native failure codes and reconcile the
existing run/reservation. Do not increase attempt ceilings, clear persistent
breakers by elapsed time, relabel errors or retry by creating another controller.
The owner may use the previously documented **manual `force_provider=openai`
recovery probe** only when that credential and quota are confirmed healthy and
an individual paid probe is explicitly authorized.

`PROVIDER_QUOTA_BLOCKED` is cleared by an attested route, never by the operator
canary. The required evidence is a fresh `DISPATCH/gemini` receipt carrying
`GEMINI_EXPLICIT_AUTO_ROUTING_WITH_HEALTHY_CANARY` or
`GEMINI_ONE_SHOT_AFTER_REVIEWED_FIX` that the resident consumes inside its
15-minute window and resolves to `PROVIDER_ADMITTED`, followed by
`DISPATCHED_TO_PROTECTED_ENGINEERING_WORKFLOW` against a fresh eligible incident.
A `GEMINI_OPERATOR_CANARY` receipt is phase-C health evidence and nothing more:
it neither clears the breaker nor keeps an admission opportunity open. If the
only fresh receipt available is a canary, `SURVIVAL` or an expired one, the
correct state is the existing hold, not a relaxed gate.

Do not call this incident COMPLETE until a new exact incident has a successful,
authenticated *resident-generated* provider run and code SHA, a proposed PR with
independent exact-head security/QA acceptance, a protected merge and live
web+worker SHA verification, and a successful real user-function canary with
appropriate read-only Action/receipt evidence. Report each separately.
No sporting model is certified by an engineering recovery canary.
`can_execute=false`; terminal authority stays `V17_TERMINAL_REDUCER`.

### E. Primary-provider (OpenAI) persistent-breaker recovery

The primary route carries the mirror-image trap: a persistent failure never ages
out. `OPENAI_API_BILLING_REQUIRED`, `OPENAI_API_QUOTA_EXCEEDED`,
`OPENAI_API_KEY_MISSING` and `OPENAI_API_AUTH_FAILED` stay active in the circuit
breaker regardless of `age_minutes`; the 120-minute cooldown applies only to the
transient codes `OPENAI_API_RATE_LIMITED` and `OPENAI_API_UPSTREAM_UNAVAILABLE`.
Funding, re-keying or rotating the credential therefore changes nothing by
itself: the hourly route still reads the **latest completed** ChatGPT worker
run, still finds that persistent code, and still emits
`CIRCUIT_BREAKER:<code>:SECONDARY_UNAVAILABLE_OR_FAILED` with
`independent_fallback_allowed=true`, which the resident holds as
`PROVIDER_QUOTA_BLOCKED`. Waiting is not recovery.

The only legitimate clearing step is one deliberate operator probe, made after
the credential and its quota are confirmed healthy out of band:

```bash
# METERS PROVIDER USAGE. OWNER ACTION ONLY AFTER CREDENTIAL/QUOTA CONFIRMATION.
gh workflow run wow-v17-engineering-provider-dispatcher.yml \
  -R gregoryharper84-ship-it/WOW-Dashboard --ref main \
  -f force_provider=openai \
  -f reason=OPENAI_OPERATOR_RECOVERY_PROBE
```

The manual path is the only route that dispatches `openai` while the breaker is
active; automatic and resident-initiated dispatches never do. One probe only:
401/402/429/5xx, malformed output, a timeout, an ambiguous response or a failed
child run all keep the hold, and a retry loop is precisely what the breaker
exists to prevent. Record the typed code from the failed run, reconcile the
run/reservation and stop; do not widen spend, attempts or authority to force a
different result.

Once one probe completes **successfully**, the persistent code is no longer the
latest completed primary evidence: the hourly route resolves to
`DISPATCH/openai` (`OPENAI_PRIMARY_AVAILABLE`) and the resident admits inside the
same 15-minute window described in phase C. The separately documented
parent-linked `NO_ACTION` acknowledgment remains an independent way for a fresh
successful protected worker to supersede an older blocker.

The secondary route mirrors this shape: `secondary_usable()` refuses Anthropic
while the latest Claude worker run ended in failure or cancellation, even with a
valid key or `CLAUDE_CODE_OAUTH_TOKEN`. The same single-probe rule applies via
`force_provider=anthropic`, and it is an alternative when that capacity is
genuinely available, never a way to skip primary recovery.

Never edit a provider receipt, breaker artifact or attempt counter to clear this
state, and never raise the three-attempt ceiling to make a hold disappear. A
cleared breaker is evidence of one successful authenticated run and nothing
more.
