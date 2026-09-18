# WOW V17 — MLB K Role/Tail + Autonomous Postmortem Guard

Date: 2026-09-15  
Status: PATCH CANDIDATE / REGRESSION REQUIRED  
Runtime generation: V17_ACTIVE  
can_execute: false  
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS: true

## Problem

A settled retro exposed two qualitatively different high-confidence pitcher-strikeout failures:

- a Newcomb-like case in which the target pitcher can be listed as the probable pitcher while his current usage is opener/short-relief shaped, yet the fitted strikeout history is built only from prior starts;
- a Mize-like case in which a low strikeout line can still be advertised as HIGH/model-qualified even though the recent-start distribution contains a material 0/1-K tail.

The defect is not that a high-probability selection can lose. The defect is that V17 must autonomously detect pregame evidence showing that a HIGH/model-qualified label may be structurally overconfident, and the postmortem must surface that failure path without waiting for a user to point it out.

## Root-cause boundaries

### 1. Current-role blind spot

The certified MLB K route historically built its fitted history from official starts (`gamesStarted >= 1`). That remains appropriate for the current starter-only fitted artifact, but it is insufficient to prove that the pitcher is *currently functioning as a normal starter*.

An official probable-pitcher listing is event identity evidence. It is not, by itself, proof of normal starter workload.

### 2. Low-K floor-collapse blind spot

The fitted K model already models normal and shortened-outing regimes and numerically consumes opponent K suppression. Those strengths are preserved.

However, a low-line MORE can still have a material recent 0/1-K tail. Until that tail has a prospectively certified fitted coefficient/model revision, V17 must not invent a probability haircut. Instead, the tail acts as a narrow recommendation-qualification contradiction.

### 3. Postmortem discovery gap

A postmortem cannot stop at `LOSS`, `MODEL_MISS`, or generic variance when a HIGH/model-qualified scalar selection lands in an extreme opposite tail. It must trace the immutable pregame evidence and ask which failure path was knowable, modeled, numerically consumed, or omitted.

## Patch

### A. Current role/workload gate before starter K scoring

`mlb_pitcher_k_risk_guard.build_recent_role_profile` inspects the most recent official pitching appearances, including relief/opener appearances. It computes:

- recent appearance count;
- start share;
- short-appearance share;
- mean/median outs;
- mean batters faced;
- mean pitches when available.

`prop_auto_hydration` now runs this audit before invoking the starter-only fitted K model.

If recent official usage is affirmatively incompatible with a normal starter workload, the row fails closed as:

```text
MLB_STARTER_ROLE_WORKLOAD_MISMATCH
=> MODEL_INPUTS_INSUFFICIENT
```

It is never rewritten as `MODEL_UNAVAILABLE`.

A thin current-role sample is not automatically rejected; the system does not invent a role conclusion from insufficient evidence.

### B. Low-line MORE tail-risk qualification ceiling

For MLB `PITCHER_STRIKEOUTS` only, a reviewed diagnostic flags:

```text
direction = MORE
line <= 3.5
eligible recent starts >= 7
share of recent starts with 0 or 1 K >= 30%
```

as:

```text
RECENT_LOW_K_TAIL_CONTRADICTION
```

This flag:

- does not change raw model probability;
- does not change calibrated probability;
- does not change calibrated lower/upper bounds;
- does not become an unfitted strikeout coefficient;
- does not use market probability as a model substitute.

If the row would otherwise be `MODEL_QUALIFIED_HOLD`, the flag imposes:

```text
terminal_label = RESEARCH_INTEREST
confidence_tier = RISK_CEILING
rank_eligible = false
model_supported = true
model_qualified = false
downstream_money_evaluation_allowed = false
```

The sporting probability remains completed and auditable.

### C. Autonomous high-confidence catastrophic-miss audit

`v17/postmortem_catastrophic_miss_audit.py` adds a deterministic post-settlement diagnostic.

For the current certified MLB hook, it triggers when:

```text
sport = MLB
stat = PITCHER_STRIKEOUTS
direction = MORE
pregame classification = HIGH / MODEL_QUALIFIED_HOLD
settled strikeouts = 0
```

The audit automatically checks:

1. current-role/workload evidence from *all recent appearances*;
2. whether the starter-only model was applied to an incompatible opener/relief regime;
3. the pregame 0/1-K tail frequency at the exact line/direction;
4. whether the tail contradiction was absent from or bypassed by qualification;
5. exact market contradiction if already stored, as contradiction evidence only;
6. whether the failure was predictable and modeled, underweighted, or omitted.

Required classifications include:

```text
ROLE_OR_WORKLOAD_ERROR
TAIL_RISK_UNDERMODELED
MODEL_INPUT_ERROR
PREDICTABLE_BUT_OMITTED
PREDICTABLE_BUT_UNDERWEIGHTED
```

An affirmative role mismatch, missing current-role audit, omitted qualifying tail contradiction, or bypassed risk ceiling may become `L3_PATCH_CANDIDATE` from one confirmed occurrence because it is a process-contract violation, not because a single bet lost.

## Mandatory postmortem behavior

For every HIGH/model-qualified losing scalar prop, the retro must independently perform a catastrophic-miss review before assigning ordinary variance.

For pitcher K MORE selections that settle at 0 Ks, the retro must explicitly report:

```text
pregame_probability
pregame_lower_bound
exact_line
direction
current_role_profile
recent_all_appearance_workload
recent_start_K_distribution
zero_K_rate
zero_or_one_K_rate
opponent_K_context
failure_path_tags
market_contradiction_if_available
whether_each_material_signal_was_numerically_consumed
primary_miss_class
predictability_class
learning_level
patch_candidate
```

If immutable pregame probability attribution is unavailable, the retro must say so. It may still diagnose a proven construction/process defect from immutable pregame evidence, but it may not backfill a probability or claim calibration error.

## Preserve constraints

This patch must not break:

- normal-starter MLB K scoring;
- opponent K-rate suppression already fitted into the distribution;
- the single-application rule for opponent suppression;
- exact-line identity;
- calibrated probability/lower-bound provenance;
- sporting-probability vs market/value separation;
- typed failure semantics;
- immutable pregame predictions;
- `V17_TERMINAL_REDUCER` authority;
- `can_execute=false`.

No universal probability haircut is introduced.

## Regression requirements

Required fixtures:

1. Newcomb-like recent opener/relief usage plus older full starts => starter K scoring fails closed before inference.
2. Normal recent starter usage => existing K model path remains available.
3. Thin recent role sample => no invented opener conclusion.
4. Mize-like recent K sequence `[1, 6, 3, 0, 1, 4, 7]`, MORE 2.5 => `RECENT_LOW_K_TAIL_CONTRADICTION`.
5. Same history, LESS 2.5 => no MORE-tail ceiling.
6. Same history, MORE 4.5 => low-line guard does not fire.
7. HIGH p/lower-bound + tail flag => probability remains valid but qualification becomes `RESEARCH_INTEREST / RISK_CEILING`.
8. Same probability package without flag => existing HIGH qualification is preserved.
9. `MLB_STARTER_ROLE_WORKLOAD_MISMATCH` => `MODEL_INPUTS_INSUFFICIENT`, never `MODEL_UNAVAILABLE`.
10. Autonomous postmortem maps opener mismatch to `ROLE_OR_WORKLOAD_ERROR / PREDICTABLE_BUT_OMITTED / L3_PATCH_CANDIDATE`.
11. Autonomous postmortem maps unconsumed Mize-like low-K tail to `TAIL_RISK_UNDERMODELED / PREDICTABLE_BUT_UNDERWEIGHTED / L3_PATCH_CANDIDATE`.
12. Market contradiction remains diagnostic only and never replaces governed probability.
13. `can_execute=false` remains invariant.

## Promotion rule

This patch changes qualification/process behavior, not fitted model coefficients. It may merge only after the required V17 regressions pass.

A future model revision that makes projected workload/batters-faced numerically load-bearing must be separately trained, calibrated, validated on holdout/forward data, and prospectively certified. This patch deliberately does not smuggle such a coefficient into production.
