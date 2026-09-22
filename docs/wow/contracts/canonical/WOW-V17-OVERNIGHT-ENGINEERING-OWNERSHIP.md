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
