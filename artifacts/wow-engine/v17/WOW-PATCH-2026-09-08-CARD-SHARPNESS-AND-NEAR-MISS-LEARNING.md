# WOW-PATCH-2026-09-08-CARD-SHARPNESS-AND-NEAR-MISS-LEARNING

## Status

```text
status=ACTIVE_PROJECT_CONTRACT_PENDING_BACKEND_REGRESSION
patch_priority=HIGH
runtime_generation=V17_ACTIVE
terminal_authority=V17_TERMINAL_REDUCER
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```

## Purpose

Preserve the strong sporting-probability behavior observed in the September 7 postmortem while reducing avoidable card losses caused by repeated theses, marginal all-or-nothing hinges, discrete zero-event fragility, and insufficient post-settlement learning metadata.

This patch follows the WOW retrospective rule:

```text
preserve strengths -> refine specific failure modes -> regression-check
```

It does not broadly tighten sporting models and does not retroactively convert close losses into wins.

## Incident Findings

The reviewed September 7 entries showed:

- strong MLB pitcher-prop and 1IP performance;
- repeated exact/directional theses amplifying a small number of misses across multiple cards;
- all-or-nothing Power structures suffering more damage than the underlying leg selection quality implied;
- a discrete `LESS 0.5` tiebreak thesis failing on exactly one tiebreak;
- scalar misses landing near the exact threshold;
- moneyline/upset candidates being structurally different from high-hit mandatory Power hinges.

The patch therefore targets card construction and learning attribution rather than imposing a second probability haircut.

## Non-Negotiable V17 Invariants

```text
duplicate_thesis_exposure != sporting_probability_penalty
adjacent_line_exposure != exact_line_identity
close_miss != win
model_qualified != automatically_power_admissible
one_specialist_per_row=true
V17_TERMINAL_REDUCER_is_sole_global_terminal_authority=true
can_execute=false
```

Never modify any of these solely because a thesis appears on more than one card:

```text
model_probability
unconditional_probability
calibrated_probability
calibrated_lower_bound
```

A duplicated or adjacent-line thesis may be removed, replaced, or blocked at the structure/exposure layer while its sporting probability remains immutable.

## PATCH-1 — Power Critical-Leg Gate

Every all-or-nothing/Power card must receive a structural critical-leg audit after duplicate/exposure cleanup and before final card qualification.

Required per-leg diagnostics:

```text
marginal_joint_failure_contribution
critical_leg_failure_share
critical_leg_rank
```

Required card diagnostics:

```text
critical_row_id
critical_leg_share
critical_leg_method
max_power_critical_failure_share
```

Current implementation uses a structural marginal approximation from already-governed calibrated lower bounds. It is a structure diagnostic only and must never be published as a new joint sporting probability.

Initial default concentration ceiling:

```text
max_power_critical_failure_share=0.60
```

This is runtime-configurable and must be calibration-reviewed. It is intentionally not a universal sporting-probability threshold.

Decision cycle:

```text
critical share <= active ceiling
=> retain

critical share > active ceiling
=> search strictly stronger independent governed replacement

superior independent replacement exists
=> replace -> rebuild -> rescore

no superior independent replacement
=> remove critical hinge -> shrink -> rebuild -> rescore

card below platform minimum
=> HOLD / insufficient legs
```

Requested leg count never justifies filler.

## PATCH-2 — Session Thesis Exposure V2

Maintain two distinct identities:

### Exact immutable thesis

```text
event + participant + market + period + exact line + direction + settlement identity
```

### Exposure family

```text
same underlying event + participant + stat/market + direction
```

An explicitly supplied `exposure_family_key` takes priority when a controlling specialist has a stronger canonical family definition.

Rules:

- exact duplicates remain duplicate session exposure;
- adjacent thresholds in the same directional family are exposure-related;
- adjacent thresholds are never relabeled as one exact prediction;
- component/composite overlap remains separately governed;
- a repeated family hinge is replaced only by a strictly stronger independent candidate;
- otherwise the later card shrinks;
- probability fields remain unchanged.

## PATCH-3 — High-Hit Power Admission Is Separate From Model Qualification

A sporting row may be model-qualified yet still be an unsuitable mandatory all-or-nothing hinge.

The structure layer may receive a stricter runtime field:

```text
power_admission_lower_bound_floor
```

If supplied, a Power leg below that floor must be replaced by a stronger independent candidate or removed. The structure decision does not revoke or rewrite the row's sporting model qualification.

No universal Power floor is hardcoded by this patch. The active runtime/calibration policy owns the numerical floor.

## PATCH-4 — Discrete Zero-Event Fragility

For explicitly typed discrete zero-event markets such as a governed `LESS 0.5` event-count thesis, Power admission requires the controlling specialist to supply:

```text
discrete_zero_event_required=true
p_zero_events
p_one_plus_events
zero_event_tail_drivers
```

Required audit:

```text
0 <= P(0) <= 1
0 <= P(1+) <= 1
P(0) + P(1+) = 1
material tail/failure path documented
```

Raw recent zero counts, sportsbook price, or narrative judgment cannot stand in for this event distribution.

Missing/malformed event distribution or missing tail path causes replacement-or-shrink at the Power structure layer. It does not create `MODEL_UNAVAILABLE` when the controlling sporting model otherwise exists.

## PATCH-5 — Near-Miss Calibration Ledger

Settled outcomes remain immutable.

Added postmortem fields:

```text
actual_value
signed_distance_to_threshold
close_miss_flag
close_miss_tolerance
duplicate_thesis_count
cards_killed
critical_leg_rank
```

### Signed distance convention

For scalar props:

```text
MORE/OVER: actual - line
LESS/UNDER: line - actual
```

Interpretation:

```text
positive = selected side cleared threshold
negative = selected side missed threshold
zero = numerical boundary
```

Moneylines and other non-scalar markets must not receive a fabricated scalar distance.

### Close-miss rule

`close_miss_flag` is diagnostic metadata only.

A market/lane-specific `close_miss_tolerance` must be supplied. No universal tolerance is permitted because one unit has different meaning across strikeouts, pitches, games, points, yards, and other markets.

A recorded loss remains a loss even when `close_miss_flag=true`.

## Workflow Placement

```text
controlling sporting specialist
-> dynamic calibration / lower bound
-> market objective where applicable
-> duplicate/exposure-family audit
-> zero-event Power audit where applicable
-> Power admission floor where configured
-> Power critical-leg replacement/shrink cycle
-> final refresh
-> V17 terminal reduction
-> immutable pregame write
-> official settlement
-> near-miss/process learning append
```

## Regression Requirements

1. Same exact thesis across two cards is not treated as two independent opportunities.
2. Adjacent thresholds on the same directional thesis are exposure-related but retain distinct exact identities.
3. Exposure-family governance never changes sporting probability fields.
4. A concentrated Power hinge is replaced by a strictly stronger independent candidate when available.
5. With no superior replacement, the Power card shrinks rather than adding filler.
6. A model-qualified row can remain model-qualified while failing a stricter Power admission rule.
7. Explicit zero-event Power hinges cannot pass with missing `P(0)` / `P(1+)`.
8. A valid zero-event distribution plus tail path can pass the zero-event structure gate.
9. Close scalar losses record signed threshold distance without becoming wins.
10. Duplicated losing theses can attribute multiple killed cards without rewriting settlement.
11. Moneyline losses do not receive fabricated scalar near-miss distance.
12. `can_execute=false` remains invariant.

## Files

```text
v17/slip_portfolio_optimizer.py
v17/postmortem_learning_ledger.py
v17/prediction-outcome-schema.json
v17/test_card_sharpness_near_miss.py
```

## Definition of Done

This patch is production-ready only after:

```text
focused_regression=PASS
existing_slip_portfolio_regression=PASS
required_three_regression=PASS
governed_probability_backend=PASS
additional_required_regression=PASS
V17_terminal_reducer_regression=PASS
probability_field_immutability=PASS
can_execute=false
```

Until those checks pass, repository implementation status is `PENDING_BACKEND_REGRESSION` rather than production certification.
