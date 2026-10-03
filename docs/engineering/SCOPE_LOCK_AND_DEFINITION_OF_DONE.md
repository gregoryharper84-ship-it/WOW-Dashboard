# Scope-Locked Epics and Definition of Done

## Purpose

WOW engineering work must terminate in a verifiable state. Open-ended strategic goals must not be used as completion contracts for individual engineering epics.

A production-critical core and speculative/optimization work MUST be separated into independently closable units.

## Operating rule

When the primary shippable objective is complete, the epic closes.

New ideas discovered during implementation do not extend the original epic unless they are required for:
- correctness,
- security,
- governance invariants,
- explicit acceptance criteria,
- or production safety.

Everything else becomes a separate backlog ticket or follow-on epic.

## Definition of Done

An epic or task is DONE only when all applicable items below are satisfied.

### Engineering and code
- [ ] The agreed implementation is merged to `main` or the governed release branch.
- [ ] Required peer review is complete.
- [ ] Relevant unit, integration, workflow, and regression checks pass.
- [ ] No unresolved blocker remains against the explicit acceptance criteria.

### Scope lock
- [ ] Every acceptance criterion from kickoff is verified.
- [ ] Mid-flight enhancements that are not required for core stability/security are split into separate tickets.
- [ ] Follow-ons have an explicit terminal triage: new epic, backlog, duplicate, deferred, or dropped.
- [ ] The original epic is not held open by optional Phase-2 work.

### Operational readiness
- [ ] The change is active in the intended environment when deployment is part of the acceptance contract.
- [ ] Required observability, logs, receipts, and runbooks exist for the primary workflow.
- [ ] Production verification is tied to the exact deployed artifact/SHA when applicable.

### Closeout
- [ ] The primary objective is verified against the original scope.
- [ ] The issue/epic is marked DONE/CLOSED.
- [ ] The terminal engineering disposition is recorded.

Allowed engineering terminal dispositions:
- `FIXED_AND_VERIFIED`
- `PR_CREATED`
- `EXPERIMENT_CREATED`
- `DUPLICATE`
- `NOT_REPRODUCIBLE`
- `BLOCKED_WITH_EXACT_REASON`
- `DEFERRED_WITH_JUSTIFICATION`

## Scope-Locked Epic Contract

Every new epic should define:

1. **Business goal** — one or two sentences describing the shippable outcome.
2. **Change class** — A, B, or C under WOW V17 governance.
3. **In-scope Phase 1 deliverables** — only the minimum functional set required to achieve the goal.
4. **Explicit non-goals** — work that will not be used to hold this epic open.
5. **Acceptance criteria** — objective, testable conditions known at kickoff.
6. **Mid-flight scope split rule** — how newly discovered work is triaged.
7. **Closure criteria** — the exact conditions for 100% COMPLETE.

## Mid-Flight Scope Split Protocol

When new work is discovered, ask:

**Is this required for the Phase-1 acceptance criteria, core stability, security, or a binding V17 governance invariant?**

- **YES:** it may be added to the Phase-1 acceptance criteria with an explicit reason.
- **NO:** create a separate ticket or follow-on epic. Do not extend the original completion contract.

## Feature Cutoff / 80-20 Triage

Once the primary functionality is live and verified:
- high-ROI follow-ons become independent Phase-2 epics;
- low-ROI or speculative items move to backlog or are closed as not planned;
- neither category keeps the original epic open.

## V17 governance invariants

Scope splitting never weakens WOW governance.

Always preserve:
- `custom_gpt_identity=WOW_BETTING_ENGINE`
- `runtime_generation=V17_ACTIVE`
- `terminal_authority=V17_TERMINAL_REDUCER`
- `can_execute=false`
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`
- exactly one controlling fitted specialist per sporting probability row
- typed V17 failure semantics

Class C sporting-model changes remain challenger-only until governed review, replay/backtesting, calibration/regression review, and explicit promotion.
