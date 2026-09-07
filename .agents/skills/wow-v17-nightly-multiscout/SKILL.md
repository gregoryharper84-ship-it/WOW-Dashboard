# WOW V17 Nightly Multi-Scout Discovery Team

Status: ACTIVE_ON_MERGE
Identity: `WOW_V17_NIGHTLY_MULTISCOUT`
Lane: discovery/evidence only
Terminal authority: `V17_TERMINAL_REDUCER`
`can_execute=false`

## Mission

Autonomously scan the upcoming slate every night, across every active sport and every bookmaker exposed by the configured public sportsbook acquisition feeds, then create a typed discovery handoff for the existing V17 Parallel Discovery Router and controlling models.

This skill broadens the top of funnel. It does not make the probability models stricter and does not modify model qualification thresholds.

## Scout team

1. `BOARD_SCOUT` — sportsbook/event/market inventory and exact board evidence.
2. `CROSS_SPORT_OPPORTUNITY_SCOUT` — exhaustive active-sport coverage, including NBA, NCAAMB, WNBA and other supported sports without requiring a user nomination.
3. `MATCHUP_AND_GAME_SCRIPT_SCOUT` — enumerates plausible sport-specific game regimes and matchup paths.
4. `ROLE_NEWS_STATUS_SCOUT` — injuries, starters, scratches, rotations, workloads, weather and status changes when supported evidence is available.
5. `MARKET_ALTERNATE_LINE_SCOUT` — exact-line evidence, alternate lines, disagreement and market-shape context. Adjacent lines never become exact-line authority.
6. `CONTRARIAN_RED_TEAM_SCOUT` — searches for contradiction evidence and failure paths against promising candidates.

The existing `wow.parallel-discovery-router` remains the Scout Coordinator. Scanner agreement can raise research priority only; it cannot become governed model probability.

## Sportsbook coverage

The nightly runner must request every active sport returned by the configured public odds feed and every bookmaker returned for the configured regions. Current default regions are `us,us2,uk,eu,au`.

“All public sportsbooks” means all bookmakers actually exposed by configured public/authorized acquisition feeds. The system must never claim coverage for a sportsbook the feed does not expose. Coverage telemetry must list bookmakers seen and source failures so omission is never silent.

No credential bypass, scraping behind authentication, or wager execution is permitted.

## Required sport coverage

Coverage telemetry must explicitly report at least:
- NBA
- NCAAMB
- WNBA
- CFB/NCAAF
- NFL
- MLB
- NHL

The dynamic active-sports inventory may add tennis, soccer, golf, combat sports and other supported sports automatically.

Inactive/offseason leagues are reported as `NOT_ACTIVE_OR_NOT_OFFERED`; they are not silently removed.

## Upset discovery

Every eligible team/event candidate sets `upset_evaluation_requested=true` and routes to `LLP_TEAM_BETTING_ENGINE`.

The Scout does not declare `UPSET_ALERT_MODEL_FLIP`, `UPSET_ALERT_UNCERTAINTY_OVERLAP`, or any governed upset probability. Those require a valid LLP governed probability package under the installed Cross-Sport Favorite Upset Alert contract.

Market odds classify favorite/underdog status and provide market evidence only.

## Game-script enumeration

For every event, enumerate the complete applicable scenario-family library, including at minimum:
- favorite control
- underdog control/upset
- close game
- favorite blowout
- underdog blowout
- favorite comeback
- underdog comeback
- high-pace/high-scoring
- low-pace/low-scoring
- overtime/extra-period extension where applicable
- early exit/limitation
- role/rotation change
- fatigue/travel/rest disadvantage

Then add sport-specific regimes (for example basketball pace/foul/three-point scripts, football pass/run/trailing/leading scripts, baseball starter/bullpen/K-contact scripts, soccer red-card/draw scripts, tennis straight-set/deciding-set/serve scripts).

The atomic scenario families are composable; the controlling fitted model determines which features are numerically supported. Scout game scripts are evidence/hypotheses only and must never apply an extra manual probability penalty or bonus after a certified model has consumed the same factor.

## Nightly handoff

The scheduled workflow `.github/workflows/wow-v17-nightly-multiscout.yml` runs `artifacts/wow-engine/v17/nightly_multiscout.py` and uploads:
- `model-handoff.json`
- `summary.md`

The handoff separates:
- `prop_candidates` -> `WOW_PROP_LANE`
- `team_event_candidates` -> `LLP_TEAM_BETTING_ENGINE`

Every handoff row remains `DISCOVERY_ONLY` with ceiling `RESEARCH_INTEREST` until the existing V17 chain performs canonicalization, slate integrity, identity validation, controlling-specialist assignment, fitted inference, failure-path modeling, calibration and downstream market/card governance.

## Reconciliation invariants

- Canonicalize/dedupe before specialist scoring.
- Syndicated copies do not count as independent evidence families.
- No row can be silently dropped.
- Every candidate routes to exactly one controlling specialist or explicit `NO_SPECIALIST_COVERAGE`.
- `NOT_CALLED` is prohibited.
- Started/final/postponed/canceled events are removed by Slate Integrity / Final Refresh.
- Sportsbook implied probability is never relabeled as governed probability.
- Scout consensus is never relabeled as governed probability.
- V17 Terminal Reducer remains sole global terminal authority.
- `can_execute=false` always.
