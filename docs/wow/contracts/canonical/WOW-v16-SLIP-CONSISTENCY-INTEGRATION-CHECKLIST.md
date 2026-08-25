# WOW v16 Slip Consistency Patch — Implementation Checklist

## Required Engine Flags

```text
CORE_CARD_SIZE=2
MAX_CARD_SIZE=3
POWER_4_TO_5_LEGS=false
FLEX_MAX_LEGS=3
1IP_FINAL_CARD_ELIGIBLE=false
LOW_COUNT_DISCRETE_GATE=true
COMPOSITE_LESS_UPPER_TAIL_GATE=true
CROSS_SLIP_DUPLICATE_GATE=true
WORST_LEG_REMOVAL_BINDING=true
JOINT_PROBABILITY_REQUIRED=true
POSITIVE_NET_RETURN_PRIMARY_LEDGER_METRIC=true
```

## Recommended API/Schema Additions

### Leg record

```json
{
  "failure_path_score": null,
  "unconditional_probability": null,
  "minimum_winning_count": null,
  "p_zero": null,
  "p_one": null,
  "p_two": null,
  "minimum_clearance_dependence_share": null,
  "upper_tail_p90": null,
  "exposure_key": null,
  "thesis_key": null,
  "duplicate_adjusted_weight": 1.0,
  "test_only_lane": false
}
```

### Slip record

```json
{
  "joint_probability": null,
  "joint_probability_lower_bound": null,
  "joint_failure_probability": null,
  "weakest_leg_id": null,
  "critical_leg_index": null,
  "slip_fragility_score": null,
  "fragility_label": null,
  "positive_net_return": null,
  "process_label": null
}
```

## Finalizer Assertions

```text
assert card_size <= 3
assert weakest_leg_cycle == PASS
assert replacement_search_complete == true
assert fragility_label != FRAGILE
assert duplicate_exposure_status == PASS
assert joint_probability_status == PASS
assert test_only_lane_status == PASS
assert can_execute == false
```

## Regression Tests

1. Reject 4-pick Power.
2. Shrink 5-pick Flex to <=3.
3. Reject low-count continuous-only model.
4. Reject duplicate Wheeler-like exposure across two active slips.
5. Exclude Castillo-like 1IP TEST_ONLY leg from final card.
6. Treat 4/5 Flex with return below entry as `positive_net_return=false`.
7. Force removal when weakest leg has no replacement.
8. Reject PRA LESS without covariance and upper-tail simulation.
9. Recompute joint probability after every replacement/removal.
10. Keep all outputs dry-run-only.
