---
name: PP Promotion Gate & Same-Game Fragility patch
description: WOW-PATCH-2026-08-15 architecture, gate design, and two critical implementation pitfalls.
---

## Rule
HIGH_PROBABILITY ≠ QUALIFIED_PAID_CARD.  The gate caps `terminal_label` at `MARKET_VERIFIED_HOLD` but never touches probability fields or leaderboard rank.

## Modules
- `gate_engine/pp_promotion_gate.py` — break-even+safety_buffer lower-bound gate, two-way no-vig (3-level fallback: explicit field → computed from American odds → cal_prob proxy), recency-shock LOO (|full−loo| ≥ 0.030 blocks)
- `gate_engine/pp_pregame_snapshot.py` — immutable write-once snapshot; write failure preserves research output but caps paid-card labels
- `gate_engine/pp_final_refresh.py` — binding 7-category material-change detector (not a warning)
- `gate_engine/prediction_ledger.py::PostmortemClassification` — 9 canonical postmortem classifications

## Governance
Patch #25, precedence 104; governance hash changed; patch count now 25.

## Pitfall 1: fatal-rejected-leg gate inside run_hard_gates()
`_gate_fatal_rejected_leg` must receive `pre_existing_reject_ids` (row_ids that had REJECT labels BEFORE the call) when invoked from `run_hard_gates()`.  Otherwise it fires on REJECT labels that sibling gates (same_event, direction_conc) just set, overwriting them with FATAL_REJECTED_LEG_IN_CARD.

**Fix applied**: `run_hard_gates()` snapshots pre-existing reject row_ids before running g1–g5a, passes them to `_gate_fatal_rejected_leg`.

## Pitfall 2: fatal gate must require at least one qualifying row
When ALL rows in the batch are rejected (single-row SLATE_PURGE, etc.), there is no card being constructed.  The gate must check `qualifying_count > 0` before firing.

**Fix applied**: gate counts non-reject rows; skips if `qualifying_count == 0`.

## Pitfall 3: float precision at threshold boundary
`BREAK_EVEN["POWER"] + DEFAULT_SAFETY_BUFFER = 0.556 + 0.020 = 0.5760000000000001` (not 0.576).  Any test asserting a boundary pass must use 0.577, not 0.576.

**Why:** Standard IEEE 754 float addition; affects any test checking exact break-even+buffer.

## Tests
87 new tests (TC-01..TC-12) in `gate_engine/tests/test_pp_promotion_gate.py`; 4494 total pass, 1 pre-existing failure unchanged.
