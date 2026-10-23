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
