# AMRT — Five Specialist Source Agents (not deployed)

**Status:** SOURCE IMPLEMENTED / EXISTING SEVEN-ROLE ADAPTER WIRED / NOT PRODUCTION ACTIVATED. This integration branch starts from October 26 protected `main` and ports the exact five specialist implementations from source PR #1861, without old #1843 branch drift.
**Change class:** B (agent-role/trust-boundary adoption requires independent review); code is pure and does not change scorer behavior.
**Authority:** Existing WOW Ecosystem Conductor, Engineering authenticated
writer/lease, independent QA, Security and Release. No parallel scheduler.

## Callable agents

| Specialist | Entrypoint | Existing role envelope | Produces |
| --- | --- | --- | --- |
| Model Recovery Commander | `ModelRecoveryCommander.run(incident)` | `BLOCKER_SENTINEL` | Unbroken incident owner, stage routing, external closure hold |
| Model Dependency Investigator | `ModelDependencyInvestigator.run(incident, stages)` | `ROOT_CAUSE_INVESTIGATOR` | First unproven producer boundary, source attribution |
| Model Restoration Engineer | `ModelRestorationEngineer.run(incident)` | `MODEL_RECOVERY_ENGINEER` | Safe, bounded repair route; Class C transfer |
| Model Recovery Regression Specialist | `ModelRecoveryRegressionSpecialist.run(incident, observed=None)` | `CODE_CI_ENGINEER` | Deterministic reproduction/replay matrix; exact-head proof check |
| Recovery Escalation & Deadlock Breaker | `RecoveryEscalationDeadlockBreaker.run(incident, now=...)` | `CODE_CI_ENGINEER` | Same-PR rework or owned-lease bounded fallback |

Each emits a frozen `SpecialistDecision`. The `to_existing_role_receipt`
adapter creates an existing seven-role `RoleReceipt`. The callable
`v17.amrt_operational_lanes.evaluate_amrt_specialist()` now exposes the five
specialists through the **existing** seven-role interface and can feed the
**existing** `amrt_receipt_bridge` only after authenticated Engineering adoption.
Neither role mapping nor a local test result is independent approval.

## Explicitly disabled

There is **no agent process, timer, event subscription, workflow, registration
manifest mutation, GitHub write, Supabase write, CI dispatch, Render deployment,
Action call, live inference, betting, or market order** here. Existing
`can_execute=false`, `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`,
and `V17_TERMINAL_REDUCER` remain authoritative. No probability-producing
artifact, calibrated value, qualification or threshold is changed.

Model Excellence owns changes to fitted probabilities or model/calibration
semantics through Class C certification; AMRT keeps the restoration incident
OPEN with one accountable owner while that dependency is resolved.

## Test and integration

Run from `artifacts/wow-engine`:

```sh
python -m pytest -q test_v17_amrt_specialist_agents.py test_v17_amrt_specialist_integration.py test_v17_amrt_operational_lanes.py test_v17_amrt_closure_gate.py test_v17_amrt_receipt_bridge.py
```

Test gates: all five callables; exact canonical role mapping; missing event
identity; absent/failed stages; Class C refusal; no duplicate active PR;
pre/post fix replay and adjacent paths; exact-head source tests vs independent
QA; same-lease fallback, expired leases and attempt cap; immutable V17 safety.

### Planned activation (requires governed approval and live evidence)

1. Independently review this exact-head main-based integration, including the new `.agents/` trust-root definitions. An authenticated Engineering App can adopt only with a fresh valid canonical incident writer lease, retaining Conductor incident uniqueness and dispatch-conflict keys.
2. The worker passes attributed external evidence to these pure functions,
   records a canonical Conductor handoff receipt, and advances through existing
   QA/Release roles. No independently fabricated PASS.
3. Protected CI, Security, Model (if Class C), independent QA and Release must approve the exact source SHA before any authorized merge. Release and live Action/Supabase readback must then prove the deployed SHA and handoff. This source PR requests review, **not independent approval, immediate deployment or live execution**.

### First acceptance cases

- #1841 / PR #1842: source proof for NFL probability package, dynamic calibration
  and final refresh; absence remains UNPROVEN, no ranks.
- #823/#1806 / PR #1835: cross-sport canonical identity, no NFL suppression,
  canonical once-scored behavior, negative held cases.
- #1562: product acceptance umbrella, not a substitute for exact incident
  ownership or governed sporting models.

**Success metric:** independently verified production model restorations /
all required model restorations in the measured cohort. Source-only agent
creation never counts as a restored model.
