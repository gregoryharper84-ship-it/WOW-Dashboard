# WOW V17 Season-Aware Continuous Improvement

Status: `ACTIVE_ON_MERGE`
Identity: `WOW_V17_SEASON_AWARE_CONTINUOUS_IMPROVEMENT`
Runtime generation: `V17_ACTIVE`
Terminal authority: `V17_TERMINAL_REDUCER`
`can_execute=false`

## Purpose

Make engineering and Model Factory continuous improvement aware of the actual sports calendar so WOW and LLP prepare before opening day/Week 1 and before postseason transitions instead of reacting after the slate is live.

This skill controls engineering priority and readiness only. It does **not** produce a sporting probability, change a fitted artifact, alter calibration, change qualification thresholds, or authorize execution.

Canonical machine-readable controller:

```text
artifacts/wow-engine/v17/season_readiness.py
artifacts/wow-engine/v17/season-readiness-registry.json
```

Generate the current board with:

```bash
cd artifacts/wow-engine
python v17/season_readiness.py validate
python v17/season_readiness.py board --date YYYY-MM-DD
```

## Season lifecycle

For bounded leagues, classify the current phase as one of:

```text
OFFSEASON
PRESEASON
OPENING_RAMP
REGULAR_SEASON
STRETCH_RUN
POSTSEASON_PREP
POSTSEASON
CHAMPIONSHIP
TRANSITION
CALENDAR_REFRESH_REQUIRED
```

For sports without one global season (for example soccer competitions, tennis tours, MMA events, and golf tours), use `COMPETITION_DRIVEN` and require the configured competition/event calendar adapter. Never pretend a single global season boundary exists when it does not.

## Readiness milestones

For bounded leagues, treat these as the standard preparation checkpoints unless the registry is stricter:

```text
T-90
T-60
T-30
T-14
T-7
T-3
T-1
```

At each crossed milestone, inspect evidence rather than assuming readiness. A sport approaching opening day with unresolved required gates outranks discretionary long-horizon Model Factory work for sports farther from competition.

## Week 1 / Opening Day readiness gates

Before the first meaningful regular-season slate, prove:

- calendar verified from primary league/competition sources;
- controlling specialist registration for every market intended for publication;
- required fitted artifacts exist and load correctly;
- calibration/certification evidence is current and valid;
- data-source hydration is live against current-season schemas;
- team/player identities, rosters, rookies, trades, expansion/rule changes are reconciled;
- injury/status/lineup/starter/depth-chart inputs required by the sport are available;
- market and settlement contracts are current;
- source freshness rules are current;
- final-refresh behavior works;
- immutable persistence and receipt reconciliation work;
- typed failure paths are tested;
- production acceptance passes end to end;
- observability is sufficient to detect degradation;
- `can_execute=false` and `V17_TERMINAL_REDUCER` authority remain intact.

Missing evidence is `UNKNOWN`/not verified, never an implicit PASS.

## Stretch run and postseason preparation

As the regular season approaches its end, increase priority for:

- clinching/elimination and incentive context;
- rest, rotation and minutes/workload changes;
- injury-management changes;
- opponent-quality and distribution shifts;
- postseason rules/format changes;
- postseason source freshness and acceptance;
- repeated matchup/series state where the controlling specialist is legitimately designed to consume it.

Before postseason publication, additionally prove the postseason readiness gates emitted by the controller.

Do not apply a generic playoff probability haircut or boost. If evidence suggests probability behavior should change, create a Class C challenger and use:

```text
Discover -> Hypothesis -> Challenger -> Historical Replay -> Counterexample Review -> Holdout/Forward Validation -> Regression -> Recommendation
```

No Class C challenger is production authority until governed promotion is complete.

## Offseason and transition

After a season ends:

1. run the season postmortem;
2. preserve demonstrated strengths;
3. identify recurring independent miss patterns;
4. build challenger hypotheses from evidence, not isolated wins/losses;
5. upgrade sources, adapters, tests, observability, and coverage gaps;
6. refresh the next season calendar as soon as authoritative dates are available;
7. begin T-90 readiness before the next season starts.

A stale or expired bounded calendar must become `CALENDAR_REFRESH_REQUIRED`; do not guess the next phase from last year's dates.

## Priority integration

Production R0/R1 incidents remain first priority. After the active reliability lane is terminal, use the season board to choose continuous-improvement work in this order:

1. `SEASON_GATE_CRITICAL`
2. `SEASON_GATE_HIGH`
3. current-season `SEASON_MONITOR` evidence-backed weaknesses
4. offseason `MODEL_FACTORY` challenger work

Within a priority band, prefer the sport with the nearest meaningful competition transition and the clearest evidence-backed gap.

Do not open many parallel experiments. Complete one meaningful milestone before starting another unless a critical season-readiness gate requires parallel action.

## WOW and LLP ownership

The board covers both `WOW_PROP_ENGINE` and `LLP_TEAM_EVENT_ENGINE`. Keep specialist/model ownership separate. Shared infrastructure can be repaired once when truly shared, but do not substitute a WOW probability for an LLP event probability or vice versa.

## Required reporting

Morning/continuous-improvement receipts should include:

```text
SPORT
SEASON
PHASE
NEXT_TRANSITION / DAYS_TO_START where available
ACTIVE_READINESS_MILESTONE
READINESS_STATUS
BLOCKING_OR_UNVERIFIED_GATES
CONTINUOUS_IMPROVEMENT_FOCUS
EXPERIMENT_OR_PR
PROMOTION_STATUS
```

Distinguish `ENGINEERING_COMPLETE` from `EMPIRICALLY_SHARP_CERTIFIED`.
