# WOW-PATCH-2026-08-04-OUTS-MORE-MISSING-SURVIVAL-DATA

## Status

```text
ACTIVE
patch_priority=HIGH
framework=WOW_v16_CLEAN_CORE
label=v16 active
```

## Purpose

Postmortem on a settled 5-pick PrizePicks Flex, 2026-08-04:

```text
Sean Manaea      Ks MORE 3.5   → 7    HIT
Grant Holmes     Outs MORE 14.5 → 18  HIT
Logan Henderson  Outs MORE 14.5 → 18  HIT
Jared Jones      Outs MORE 14.5 → 12  MISS (pulled after 4.0 IP, one out short)
```

The postmortem could not determine, from chat alone, whether Jones's loss
was legitimate variance (a correctly-computed survival probability that
simply missed) or a model error (the workload-survival gate never ran).
Auditing `gate_engine/mlb_directional_firewall.py` found a real gap.

## The gap (now closed)

`_apply_outs_more_gate()` reads `required_out_survival_lower_bound` and
runs three rules against it. All three were conditioned on the value being
present. When the field was `None`, none of the three rules fired, and the
row fell through to the same `MLB_OUTS_MORE_HOLD` → `MODEL_QUALIFIED_HOLD`
ceiling as a row whose survival probability was actually computed and
cleared the 0.65 floor. Missing data and passing data produced an
identical, silent outcome.

## Fix applied

Rule 0 added to `_apply_outs_more_gate()`, checked before Rule 1:

```text
p_survival_lb is None
→ terminal_label = REJECT_DATA_QUALITY
→ blockers += ["MLB_OUTS_MORE_SURVIVAL_DATA_MISSING"]
→ directional_forward_test_status = "MLB_OUTS_MORE_SURVIVAL_DATA_MISSING"
→ return (fail closed)
```

Non-numeric values (e.g. a string) are treated the same as None via the
existing `try: float(...)` parse in `_apply_outs_more_gate()`.

Rules 1, 2, 3 and the clean-passing path are untouched.

## Non-Negotiable Governance

```text
WOW_VERSION=WOW_v16_CLEAN_CORE
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
can_execute=false
```

`can_execute` is not referenced by this patch.

## Tests

13 regression tests in `gate_engine/tests/test_mlb_directional_firewall.py`
(module previously had zero test coverage). All pass.

Key tests:
- `test_missing_survival_data_fails_closed` — the core fix
- `test_missing_survival_data_distinct_from_passing_case` — the regression
- `test_non_numeric_survival_value_treated_as_missing`
- `test_can_execute_always_false`

## One-Line Definition

**This patch closes a silent fail-open gap in the MLB Outs-MORE
workload-survival gate: a prop is no longer promoted to
MODEL_QUALIFIED_HOLD when its required survival probability was never
computed — it now fails closed to REJECT_DATA_QUALITY instead.**
