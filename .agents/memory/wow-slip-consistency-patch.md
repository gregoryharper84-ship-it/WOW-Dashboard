---
name: WOW v16.1 Slip Consistency & Fragility Patch Architecture
description: PATCH-014–020 slip card enforcement — engine flags, DB schema, helper functions, 4 new endpoints, 10 regression tests
---

## The Rule
PATCH-014–020 are a unified slip governance layer that runs BEFORE any card is presented. `can_execute=False` is unconditional for all slip endpoints. `positive_net_return` (not platform badge color) is the primary economic metric (PATCH-017).

**Why:** Prior engine allowed 4- and 5-leg Power cards, skipped joint probability modeling, and had no test-only quarantine or cross-slip exposure tracking. The patch closes all of these gaps.

**How to apply:**
- Any new slip-building code must import these 11 flags from the top of app.py (they are constants, not DB-driven).
- Never add a "can_execute=True" path — the lane ceiling is MODEL_QUALIFIED_HOLD.
- New slip endpoints follow the same pipeline order as `cm_slip_optimizer`.

## Engine Flags (11 constants)
```python
CM_SLIP_CORE_CARD_SIZE = 2          # minimum viable card
CM_SLIP_MAX_CARD_SIZE  = 3          # hard ceiling — no 4/5-leg Power
CM_SLIP_POWER_4_TO_5_LEGS = False   # 4-5 leg Power disabled
CM_SLIP_FLEX_MAX_LEGS  = 3          # Flex ceiling matches Power ceiling
CM_SLIP_1IP_FINAL_CARD_ELIGIBLE = False  # MLB 1IP is TEST_ONLY
CM_SLIP_LOW_COUNT_DISCRETE_GATE = True
CM_SLIP_COMPOSITE_LESS_UPPER_TAIL_GATE = True
CM_SLIP_CROSS_SLIP_DUPLICATE_GATE = True
CM_SLIP_WORST_LEG_REMOVAL_BINDING = True
CM_SLIP_JOINT_PROBABILITY_REQUIRED = True
CM_SLIP_POSITIVE_NET_RETURN_PRIMARY = True
```

## DB Schema
- `cm_daily_exposure` — tracks legs across slips on a given date (PATCH-016)
- `cm_settled_slips` — PATCH-017 ledger with `positive_net_return`, `net_profit`, `process_label`
- `cm_slips` gained 23 new columns via `_CM_SLIP_MIGRATE_DDL` (ALTER TABLE ADD COLUMN IF NOT EXISTS)

## Helper Function Pipeline Order
1. `_cm_parse_hit_rate` / `_cm_calibrated_lower_bound` — stat normalization
2. `_cm_exposure_key` / `_cm_thesis_key` — dedup keying
3. `_cm_is_test_only_lane` → `_cm_enrich_leg` — per-leg quarantine (PATCH-019)
4. `_cm_discrete_low_count_audit` — 0.5/1.5 line discrete modeling (PATCH-015)
5. `_cm_composite_less_upper_tail_audit` — PRA/composite LESS upper tail (PATCH-018)
6. `_cm_joint_probability` — independence/team-game/same-game correlation (PATCH-014)
7. `_cm_fragility_label` — BALANCED / CONCENTRATED / FRAGILE classification
8. `_cm_weakest_leg_finalizer` — binding removal; FRAGILE cards rejected (PATCH-020)
9. `_cm_check_daily_exposure` / `_cm_register_daily_exposure` — exposure governor (PATCH-016)

## Endpoints
| Endpoint | Method | Purpose |
|---|---|---|
| `/build-slips` | POST | Board-based slip builder (PATCH-014–020 rewrite) |
| `/wow/patch-flags` | GET | All 11 engine flags + governance block |
| `/wow/slip-optimizer` | POST | Standalone optimizer (no board_id required) |
| `/wow/settle-slip` | POST | PATCH-017 ledger settlement |
| `/wow/slip/regression-tests` | GET/POST | 10 acceptance tests from integration checklist |

## Regression Test Results (as of 2026-07-23)
10/10 PASS. All 8 finalizer assertions TRUE:
- assert card_size <= 3 ✓
- assert weakest_leg_cycle == PASS ✓
- assert replacement_search_complete ✓
- assert fragility_label != FRAGILE ✓
- assert duplicate_exposure_status == PASS ✓
- assert joint_probability_status == PASS ✓
- assert test_only_lane_status == PASS ✓
- assert can_execute == false ✓
