# WOW V17 Postmortem / Retro Learning Engine — Amendment v1.1

This amendment is additive to `wow-v17-postmortem-retro-learning-engine` V1.0.
All existing preserve-first, immutable-prediction, anti-hindsight, learning-level,
regression, and `can_execute=false` rules remain controlling.

## Mandatory Autonomous Catastrophic-Miss Audit

The postmortem must not wait for the user to notice an obviously concerning
high-confidence failure.

For every losing scalar prediction that was presented pregame as HIGH,
model-qualified, or equivalent governed high-confidence status, automatically ask:

```text
Did the realized outcome land in a severe opposite-tail state that should trigger
an independent root-cause audit rather than generic MODEL_MISS/variance language?
```

When yes, run the audit before assigning the final miss taxonomy or learning level.

### MLB Pitcher Strikeout MORE — zero-K trigger

The first certified trigger is:

```text
sport = MLB
stat_type = PITCHER_STRIKEOUTS
direction = MORE
pregame = HIGH or MODEL_QUALIFIED_HOLD
settled strikeouts = 0
```

Required pregame reconstruction:

```text
exact immutable prediction ID
exact line and direction
calibrated probability
calibrated lower bound
confidence tier / terminal status
all-recent-appearance role/workload profile
starts-only fitted-history profile
recent-start K distribution
zero-K frequency
zero-or-one-K frequency
opponent K context
failure-path tags
market contradiction when exact evidence existed
pregame injury/health/mechanical/workload evidence when timestamped and available
whether each material pregame signal was numerically consumed by the fitted package
```

### Role/workload mismatch rule

A probable-pitcher listing proves event/starter identity but does not prove normal
starter workload.

If the fitted K artifact is starter-only and all-recent-appearance evidence shows
opener/short-relief shaped current usage, classify:

```text
primary_miss_class = ROLE_OR_WORKLOAD_ERROR
predictability = PREDICTABLE_BUT_OMITTED
learning_level = L3_PATCH_CANDIDATE
```

when the mismatch was knowable before lock and was not represented in the numeric
model package.

Do not hide the mismatch by reconstructing only prior starts.

### Low-K floor-collapse rule

For low-line pitcher K MORE selections, explicitly inspect the pregame 0/1-K tail.
A material tail contradiction must not be dismissed because the point estimate or
average projection clears the line.

The current reviewed V17 qualification diagnostic is:

```text
direction = MORE
line <= 3.5
eligible prior starts >= 7
0-or-1 K rate >= 30%
=> RECENT_LOW_K_TAIL_CONTRADICTION
```

If the row was still advertised as HIGH/model-qualified despite this reviewed risk
flag, or the flag should have been present from immutable evidence but was omitted,
classify the process concern as:

```text
TAIL_RISK_UNDERMODELED
PREDICTABLE_BUT_UNDERWEIGHTED
L3_PATCH_CANDIDATE
```

This diagnostic is a recommendation-qualification ceiling only. It must never be
retroactively converted into a manual probability haircut.

### Market contradiction rule

Exact two-sided market evidence may be used as a contradiction detector in the
postmortem. It may not replace the governed sporting probability or be retroactively
relabeled as model probability.

A large model-vs-market disagreement on a catastrophic miss requires a line item in
the diagnostic, but it is not by itself proof that the market was correct or that
the fitted probability was wrong.

### Pregame health / mechanics / role research

When timestamped pregame evidence reported a material injury, blister, mechanical
change, workload restriction, opener designation, or role change, the postmortem
must determine:

```text
PREGAME_SIGNAL_ABSENT
PREGAME_SIGNAL_PRESENT_AND_MODELED
PREGAME_SIGNAL_PRESENT_BUT_UNDERWEIGHTED
PREGAME_SIGNAL_PRESENT_BUT_NOT_NUMERICALLY_CONSUMED
EVIDENCE_CONFLICTED
```

A postgame explanation alone cannot establish predictability.

If a certified model input existed pregame but a material signal did not reach the
numeric package, that remains eligible for L3 under the existing V17 hard-process
rule.

## Output Requirement

Add a `HIGH-CONFIDENCE CATASTROPHIC MISS AUDIT` section whenever at least one row
triggers.

For each trigger show:

```text
Thesis
Pregame calibrated probability
Pregame lower bound
Settled value
Extreme-tail trigger
Role/workload finding
Tail-distribution finding
Opponent-context finding
Pregame health/mechanics finding
Market contradiction finding
Numeric-consumption finding
Primary miss class
Predictability
Learning level
Patch candidate
Preserve constraints
```

If no immutable pregame record exists, preserve `MODEL_ATTRIBUTION_UNAVAILABLE`.
Do not invent the missing probability. The retro may still report a separately
proven construction/data/process defect from evidence that genuinely existed
pregame.

## Preserve-First Constraint

Every resulting patch must explicitly protect:

```text
normal successful starter-model behavior
correct opponent-context suppression
calibration provenance
calibrated lower-bound semantics
exact-line identity
sporting-probability / market separation
immutable pregame records
typed failure semantics
can_execute=false
```

A single catastrophic loss does not justify a universal model haircut, a blanket
market-family ban, or replacement of fitted probability with market probability.

## Regression Requirement

The postmortem workflow is incomplete unless the reported root cause has a replay
fixture and a nearest successful counterexample. For the Sep-15 amendment, the
minimum regression pair is:

- Newcomb-like opener/short-relief role conversion vs a normal starter;
- Mize-like low-line 0/1-K tail vs a low-line candidate without that tail.

Patch promotion still requires the base skill's replay/regression and
strength-preservation checks.
