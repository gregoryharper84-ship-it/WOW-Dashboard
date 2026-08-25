# WOW Patch: Sports Intelligence Command Center — Phase 1

**Patch ID:** WOW-CC-PHASE1-2026-08-06
**Date:** 2026-08-06
**Status:** IMPLEMENTED — 61/61 regression tests passing
**Author:** Replit Agent (Build Mode)
**Approval required before activation:** ChatGPT Step 3 review

---

## Summary

Implements the WOW Sports Intelligence Command Center (CC), a federated
orchestration and governance layer that sits **above** the four existing
WOW engines without modifying any of them:

| Engine family | Controlling module |
|---|---|
| PROP (PrizePicks) | `gate_engine` — `run_pipeline()` |
| LLP (Team/Moneyline) | `/wow/llp/*` HTTP routes |
| KALSHI_SPORTS | `kalshi_engine/sports_gate.py` |
| KALSHI_WEATHER | `kalshi_engine/portfolio_governor.py` |

---

## Governance Invariants (unconditional, enforced at every return point)

| Constant | Value |
|---|---|
| `can_execute` | `False` |
| `dry_run_only` | `True` |
| `KALSHI_RECOVERY_MODE` | `"ACTIVE"` |

No engine call and no downstream pass can change these values.

---

## New files

```
gate_engine/command_center/
  __init__.py             — Public API: run_intake(), run_command_center()
  cc_labels.py            — Namespaced CC: labels, CEILING_ORDER, ceiling_rank()
  candidate_intake.py     — make_envelope(), validate_batch()
  market_router.py        — route_candidate(), route_batch()
  ceiling_resolver.py     — apply_ceiling_to_row(), enforce_batch_ceilings()
  shared_services.py      — run_all() — 7 cross-cutting services
  kalshi_isolation.py     — apply_recovery_mode_caps(), check_cross_contamination()
  reconciliation.py       — reconcile_row(), reconcile_batch() — rules R-01..R-10
  orchestrator.py         — run_intake(), run_command_center()

gate_engine/tests/
  test_command_center.py  — 61 acceptance tests (11 UNIT, 7 INTAKE, 7 ROUTER,
                            7 CEILING, 8 SERVICE, 5 KALSHI, 6 RECON, 10 ORCH)
```

### New Flask routes (added to app.py)

| Method | Route | Role |
|---|---|---|
| GET | `/wow/cc/health` | Module health check — no I/O |
| POST | `/wow/cc/intake` | Phase A: validate + route candidates |
| POST | `/wow/cc/run` | Phase A+B: full orchestration |
| GET | `/wow/cc/status/<run_id>` | Run status (stateless in Phase 1) |
| POST | `/wow/cc/reconcile` | Explicit row reconciliation |

---

## Protocol

### Phase A — POST /wow/cc/intake
1. Intake validation → canonical candidate envelopes
2. Market routing → each candidate → exactly one engine family
Returns: routing manifest (no engine calls)

### Phase B — POST /wow/cc/run
Accepts: routing manifest + engine_results dict (keyed by candidate_id)
3. Engine result attachment → engine_label + engine_blockers stamped
4. Kalshi Recovery Mode isolation (portfolio_governor caps applied)
5. Shared services: slate integrity, cross-platform exposure, calibration,
   failure-path audit, exact-line audit, final refresh, row completeness
6. Monotonic ceiling enforcement (ceiling can only move to more restrictive)
7. Row reconciliation (rules R-01..R-10)
8. Unified output envelope

---

## Routing rules

Each candidate declares `market_family` in its payload.  The CC router
validates this declaration with a structural consistency check:
- PROP: `platform` must not be "KALSHI"
- LLP: `platform` must not be "KALSHI"
- KALSHI_SPORTS: `category` must be "sports_winner" or empty
- KALSHI_WEATHER: `category` must be "weather" or empty

**Hard conflict** (platform mismatch) → `CC:ROUTING_CONFLICT` blocker
**No valid family** → `CC:ROUTING_UNRESOLVABLE` blocker
One candidate → exactly one family: always.

---

## Monotonic ceiling

`CEILING_ORDER` defines ~70 labels from most permissive (index 0) to
most restrictive (highest index). The ceiling can only move RIGHT.

- `apply_ceiling_to_row(row, proposed)`:  if proposed is LESS restrictive
  than `row["cc_ceiling"]`, the proposed label is dropped and
  `CC:UPSTREAM_BLOCKER_PRESERVED` is stamped.
- `enforce_batch_ceilings(rows)`: resolves `final_label` as the most
  restrictive of `engine_label` and `cc_ceiling`.
- `check_no_upstream_erasure(rows)`: detects any row where
  `final_label` rank < `cc_ceiling` rank (should always be empty).

---

## Kalshi isolation

`KALSHI_RECOVERY_MODE = "ACTIVE"` is a module constant.  The Kalshi
pool (sports + weather sub-pools) is completely isolated from Prop/LLP.

- `apply_recovery_mode_caps()` calls `kalshi_engine.portfolio_governor.run()`
  (with a local fallback if the import fails: max 2 survivors total)
- `check_cross_contamination()` verifies no Kalshi-exclusive labels appear
  on Prop/LLP candidates and vice versa. Violations → `CC:KALSHI_*` blockers.

---

## Reconciliation rules (R-01..R-10)

| Rule | Check |
|---|---|
| R-01 | Every row has `final_label` |
| R-02 | `can_execute=False` on every row |
| R-03 | `final_label` rank ≥ `cc_ceiling` rank (monotonic) |
| R-04 | `intake_valid=False` rows have CC:INTAKE_* blocker |
| R-05 | Kalshi candidates have `kalshi_recovery_caps_applied=True` |
| R-06 | Routing-failed rows have CC:ROUTING_* blocker |
| R-07 | (combined with R-06) |
| R-08 | Routed candidates without engine result have CC:ENGINE_RESULT_MISSING |
| R-09 | All label strings ≤ 120 chars |
| R-10 | No duplicate candidate_ids in batch |

---

## Test coverage: 61 tests / 61 passing

- **TestUnit** (11): constants, ceiling primitives, label taxonomy
- **TestIntake** (7): valid/invalid candidates, all families, batch split
- **TestRouter** (7): family routing, conflict detection, batch grouping
- **TestCeiling** (7): monotonic enforcement, erasure checks
- **TestSharedServices** (8): slate, exposure, freshness, exact-line, can_execute
- **TestKalshiIsolation** (5): recovery mode, max-2 cap, contamination
- **TestReconciliation** (6): R-01..R-06, batch summary
- **TestOrchestration** (10): full pipeline, invariants, blocker preservation

---

## Phase 2 scope (not in this patch)

- Database-backed run log (run_id → Postgres row) — `/wow/cc/status` becomes stateful
- Async engine dispatch (CC calls engines directly rather than receiving results)
- Prediction ledger integration (Tasks #102, #103)
