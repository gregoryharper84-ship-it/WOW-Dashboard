# WOW V17 Detailed Evidence Collection and Prediction Contract

Status: V17 controlling evidence/research contract
Scope: all supported pregame player/scalar props and team/event outcomes, with sport-specific adapters
Execution: analysis only; `can_execute=false`
Terminal authority: `V17_TERMINAL_REDUCER`

## Purpose

This contract standardizes the evidence collected before a controlling WOW/LLP specialist produces or refreshes a sporting probability package. It is an evidence and feature-hydration layer, not a standalone probability model. Research signals may alter certified model inputs, uncertainty, failure-path regimes, or calibration only when the controlling specialist supports them numerically. Narrative evidence alone never becomes governed probability.

The contract is sport-agnostic. Each sport/stat route maps these evidence families into its certified feature vector and model family. Missing unsupported evidence must be typed as unavailable or evidence-only; it must never be fabricated or substituted with sportsbook implied probability.

## 1. Team / Competitor Performance

Collect recent form over an appropriate short window, normally the most recent 5-10 comparable contests where the sport supports team form. Preserve both raw and opponent-adjusted context when certified.

Capture:
- recent record and performance trend;
- home/away, venue, surface, court, field, or side splits where applicable;
- offensive and defensive efficiency trends;
- performance against opponents with similar strength, ranking, scheme, style, handedness, surface, or tactical profile;
- sample size, date range, source, and evidence `as_of` timestamp.

Recent form is never sufficient by itself to create a probability. Recency weighting and opponent adjustment are controlled by the fitted specialist.

## 2. Head-to-Head Matchups

Collect relevant historical meetings, prioritizing recent and structurally comparable contests. Preserve:
- wins/losses or result distribution;
- scoring/output profile;
- repeat tactical or matchup patterns;
- material roster/coaching/surface/context differences versus the current event.

Head-to-head evidence is small-sample by default and must be downweighted or evidence-only unless the certified specialist explicitly consumes it.

## 3. Player Performance Metrics

For player-relevant sports and prop routes, collect an appropriate rolling window, normally up to the most recent 20 comparable appearances when available.

Possible features include:
- scoring/output production;
- assists, creation, key passes, usage, opportunities, touches, attempts, targets, possessions, innings, minutes, snaps or other workload measures;
- defensive contributions such as tackles, interceptions, clearances, blocks, steals, pressure, defensive events or equivalent sport-specific metrics;
- distributional consistency, volatility, streaks, role stability and recent workload;
- player-specific advanced metrics certified for the sport/stat.

Do not use streaks or recent hit rates as a probability substitute.

## 4. Lineups, Availability, Roles and Depth

Freeze the best available pregame lineup/roster state and label its certainty.

Capture:
- confirmed or projected lineup/status;
- injuries, suspensions, scratches, rest, rotation, minutes/usage/workload limits;
- starter, goalie, pitcher, quarterback or other high-leverage role certainty where applicable;
- replacement quality, substitute/bench/depth impact;
- expected tactical/usage consequences of absences or role changes;
- source and timestamp of each material availability fact.

Projected-lineup scenarios must remain explicit regimes until confirmed. The model may integrate these through certified failure-path/scenario probabilities; prose uncertainty may not be ignored when the model supports the input.

## 5. Tactical, Scheme and Style Matchup

Collect opponent-specific style evidence relevant to the certified model, such as:
- pace/tempo;
- possession/control versus transition/counterattack;
- pressing/blitz/pressure intensity;
- zone/man coverage or defensive shell;
- serve/return profile;
- striking/grappling style;
- platoon/handedness;
- set-piece strengths/weaknesses;
- matchup-specific vulnerabilities and strengths.

Style descriptions are explanatory unless mapped to a certified numeric feature or regime.

## 6. Match Context and Stakes

Capture material event context such as:
- playoff, knockout, elimination, tournament, title, relegation or qualification stakes;
- series state or aggregate-score state;
- expected rotation incentives;
- materially asymmetric motivation when supported by objective evidence.

Motivation narratives are never manually converted into probability. They are numeric only when a certified feature/regime exists; otherwise they remain evidence-only or uncertainty context.

## 7. External and Environmental Factors

When relevant, collect:
- weather, temperature, precipitation, humidity and wind;
- altitude;
- venue dimensions, park factors, court/surface, pitch size and condition;
- roof status or indoor/outdoor state;
- other sport-specific environmental effects.

Weather and venue evidence must be event-time appropriate and timestamped. Only certified model coefficients/transforms may convert the evidence into probability impact.

## 8. Referee, Umpire, Judge and Officiating Trends

Where the route supports officiating effects, collect:
- assigned official(s) and assignment certainty;
- foul/card/penalty rates;
- strike-zone or other umpire tendencies;
- judge/referee tendencies relevant to combat sports;
- pace or stoppage effects where supported.

Officiating evidence must be source-backed, sufficiently sampled, and certified before it becomes a numeric feature. Otherwise it is explanatory only.

## 9. Schedule, Rest, Travel and Fatigue

Collect:
- rest days;
- back-to-back or short-rest status;
- fixture/game congestion;
- innings/pitch/workload accumulation;
- travel distance, time-zone change and road-trip context when material;
- prior-event duration and turnaround for tennis/combat/tournament sports;
- schedule asymmetry between opponents.

Fatigue effects must enter through certified model features or explicit failure-path regimes rather than ad hoc probability haircuts.

## 10. Market Evidence and Line Movement — Separate Contract

Market data is downstream and must remain separate from the sporting probability evidence package.

Capture when available:
- exact platform/book/exchange;
- exact market, side/direction, line and price;
- source snapshot and `as_of` time;
- exact-line implied probability and no-vig probability when computable;
- opening/current/closing movement when the use case permits;
- price age/staleness;
- `EXACT_LINE`, `ADJACENT_LINE`, or `NO_MARKET` typing.

Rules:
- sportsbook/exchange implied probability is never governed sporting probability;
- adjacent-line evidence cannot be used as exact-line no-vig authority;
- missing price may block edge/value/risk-adjusted recommendation publication but must not erase a completed sporting probability;
- market prior weight may be used only when the certified model/calibration contract explicitly supports it.

## 11. Advanced Statistics

Use sport-specific advanced statistics where certified. Examples include, but are not limited to:

### Soccer
- xG, xGA, xA, shot quality, shot accuracy, field tilt, possession, pressing, set pieces, corners, cards, defensive errors.

### NBA / WNBA / College Basketball
- pace, offensive/defensive rating, shot profile, expected shooting, rebounding, turnover rate, free-throw rate, lineup/on-off, usage, minutes, role and matchup efficiencies.

### NFL / College Football
- EPA/play, success rate, explosive-play rate, pressure/sack rate, pass/run efficiency, early-down performance, red-zone efficiency, special teams, trench/injury matchup and weather.

### MLB
- starter and bullpen quality, FIP/xFIP/SIERA or route-certified equivalents, K/BB, batted-ball/contact quality, platoon splits, lineup quality, park/weather, rest/workload and umpire effects when certified.

### Tennis
- surface-adjusted strength/Elo where certified, hold/break percentage, first/second serve and return performance, opponent quality, fatigue/travel, injury/fitness and surface history.

### Boxing / MMA
- opponent-adjusted striking accuracy/defense, significant-strike rate, knockdown/power indicators, takedown/control/submission metrics, reach/age, pace/cardio, stance/style, opponent quality and referee/judging context where certified.

A metric's availability does not certify it for a model. The controlling specialist owns feature selection.

## Evidence Envelope Requirements

Every material evidence item should preserve:
- `source`;
- `source_type`;
- `as_of`;
- `event_id` / candidate identity where applicable;
- sample window and sample size;
- `data_quality`;
- `certainty`;
- `feature_status`: `MODEL_INPUT`, `REGIME_INPUT`, `CALIBRATION_INPUT`, `MARKET_EVIDENCE`, or `EVIDENCE_ONLY`;
- any transform/version used by the controlling specialist.

Material source conflict must be explicit. Unknown data is not zero data.

## Probability Flow

The required flow is:

`event/row identity -> evidence freeze -> specialist feature mapping -> fitted specialist inference -> V17_CERTIFIED_NUMERICAL_ENGINE -> failure-path integration when required -> independent numerical verification when required -> dynamic calibration -> calibrated interval/lower bound -> V17_TERMINAL_REDUCER -> downstream market/value/card analysis`

The following are prohibited shortcuts:
- recent hit rate -> governed probability;
- external projection -> governed probability;
- sportsbook implied probability -> governed probability;
- narrative consensus -> governed probability;
- generic Python distribution chosen without a certified specialist -> governed probability.

## Prediction Selection

For each analyzed game/event, return **up to three qualified predictions**, never a forced three.

Rules:
1. A candidate must complete its exact controlling route and satisfy the lane's publication/rank-eligibility contract before it can appear as an official ranked recommendation.
2. If fewer than three candidates qualify, publish only the qualified set and report the blocker(s) for the remainder. Never add filler.
3. Market diversity is desirable only after independent qualification. Do not choose a weaker market merely to provide variety.
4. Where multiple market families are supported, candidate discovery may include match/event winner, draw/double chance, totals, team totals, period/half markets, handicap/spread, corners/cards, player/scalar props and certified specials. Unsupported markets fail closed.
5. Exact score, first scorer, corners/cards, Asian handicap or other specialty markets require their own certified probability route. A moneyline model may not be repurposed to manufacture them.
6. Probability-only ranking uses the governed calibrated lower bound when required by the lane. Value/edge ranking occurs only downstream using fresh exact-line market evidence.

## Priority Sports and Competitions

Discovery should prioritize, without assigning artificial model weight to the priority itself:
- NBA;
- WNBA;
- NFL;
- College Football;
- College Basketball;
- MLB;
- Tennis;
- Boxing;
- MMA;
- Soccer, especially Premier League, La Liga, UEFA Champions League, UEFA Europa League, Serie A, Bundesliga, Ligue 1, Eredivisie, Liga Portugal and Saudi Pro League.

These are discovery priorities only. Probability is determined by the certified specialist and evidence, not league popularity.

## Risk Classification

Risk labels are downstream decision-support metadata, not a second sporting-probability haircut.

Classify recommendation risk using the combined state of:
- calibrated probability and lower bound;
- uncertainty interval width;
- lineup/role/starter certainty;
- evidence quality/source conflict;
- failure-path concentration;
- model disagreement;
- exact-price freshness and exact-line quality when value is being evaluated;
- portfolio/correlation/duplicate-thesis exposure.

`LOW`, `MEDIUM`, and `HIGH` risk must not be assigned from payout size alone.

## Bankroll Decision Support

WOW may provide educational bankroll sizing or unit/fractional-Kelly analysis only when a valid governed probability package and fresh exact price exist. It must:
- remain analysis/advice only;
- preserve `can_execute=false`;
- never place, route, modify, approve or cancel a wager/order;
- avoid sizing from ungoverned or stale probabilities/prices;
- account for correlation and duplicate-thesis exposure at portfolio level.

## Prematch vs Live

Prematch is the default analysis mode. Live recommendations require a separately certified live route with live event state, live evidence freshness, and live calibration. Pregame probabilities must not be casually transformed into live probabilities. If no certified live route exists, return the appropriate live-model unavailable/unsupported state rather than improvising.

## Explanation Standard

For each qualified prediction, explain:
- controlling specialist/model family;
- the most load-bearing certified inputs;
- material uncertainty/failure paths;
- calibrated probability/lower bound when publishable;
- exact market evidence and edge only if separately valid;
- risk classification rationale.

Explanations must distinguish model inputs from evidence-only context.

## Postmatch Evaluation

Grade only immutable pregame predictions with the exact recorded event/participant/market/line/side/direction and decision timestamp. Track model-selection quality separately from card/slip construction and realized variance.

Where supported, record:
- official result and settlement source/time;
- closing market probability/price;
- observed failure path;
- Brier score;
- log loss;
- calibration/ECE and bias;
- calibrated-lower-bound reliability;
- CLV or market comparison when exact comparable market data exists;
- process classification.

Postmatch analysis may improve future certified models/calibration but may not rewrite the original prediction.

## External Research Sources

Research may use reputable official or high-quality third-party sources appropriate to the sport. For soccer, examples may include official league/team sources plus WhoScored, SofaScore and Transfermarkt where their data is suitable and current. Third-party resources are evidence sources only; they do not become terminal authority or governed model probability.

## Governance Invariants

- Exactly one controlling sporting specialist per candidate.
- Evidence/research never substitutes for fitted inference.
- Market evidence remains separate from sporting probability.
- No forced top-three quota and no filler.
- No narrative/manual probability haircuts when a certified numeric pathway exists.
- Unsupported markets and missing model artifacts fail closed.
- `V17_TERMINAL_REDUCER` remains sole terminal authority.
- `can_execute=false` always.
