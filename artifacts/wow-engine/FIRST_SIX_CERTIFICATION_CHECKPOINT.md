# V17 First-Six Team/Event Certification Checkpoint

Scope: NCAAF, NBA, WNBA, NCAAB, SOCCER, TENNIS.

This checkpoint is a governed model-development program, not a blanket certification flag. Each lane must preserve `can_execute=false`, keep sporting probability independent from market price, and earn numerical authority only from an exact controlling specialist plus a fitted artifact, deterministic calibration/OOS evidence, source provenance, and route-specific settlement semantics.

## Route ownership

- NCAAF — `wow.ncaaf-game-win-probability-expert`
- NBA — `wow.nba-game-win-probability-expert`
- WNBA — `wow.wnba-game-win-probability-expert`
- NCAAB — `wow.ncaab-game-win-probability-expert`
- SOCCER — `wow.soccer-match-win-probability-expert`
- TENNIS — `wow.tennis-match-win-probability-expert`

Ownership does not create model capability. Runtime capability remains `UNAVAILABLE` until the exact lane earns certification.

## Model-development lanes

### NCAAF
The rich `NCAAF_FEATURES_V1` route remains preferred. When genuine historical QB/injury/OL/weather evidence is unavailable, V17 does not synthesize it. The separately identified `NCAAF_RESULT_FORM_PRIOR_V1` candidate reconstructs only prior settled team results, point differential, workload/rest, and neutral-site context. It is candidate evidence only.

### NBA / WNBA
Existing basketball maintenance hydrates current settled team games, performs deterministic provenance replay, fits league-isolated logistic specialists, and applies chronological Platt calibration. A successful maintenance run persists SHADOW evidence; explicit replay/promotion remains required.

### NCAAB
`NCAAB_TEAM_FORM_PRIOR_V1` uses SportsDataverse men's college basketball team-box release assets under CC BY 4.0. Current-game box statistics are outcomes; all features are reconstructed strictly from prior games. Market prices are not features.

### SOCCER
`SOCCER_RESULT_FORM_1X2_PRIOR_V1` uses OpenFootball CC0 results and a true HOME/DRAW/AWAY multinomial model. Major competitions are fit independently. Market prices are not features.

### TENNIS
`TENNIS_FORM_SURFACE_PRIOR_V1` uses Valuebetennis ATP/WTA open data under CC BY 4.0. ATP and WTA are separate candidates. Odds columns are explicitly ignored. Retirements/walkovers are excluded from training outcomes; serving still requires explicit retirement/settlement controls.

## Lifecycle

All new lanes follow chronological training -> calibration -> untouched test evaluation. Candidate artifacts cannot self-certify or self-promote. Research-screen failure remains a typed model-quality blocker. Passing a research screen means only that the lane is eligible for deterministic replay/lifecycle review.

No workflow, migration, candidate, specialist, or route may set `can_execute=true` or place/modify/approve/cancel a wager.

## Production run trigger

Prepared after merge `ab6eb8cc911802ab82423db2ebf569fb59cad119`. This one-line checkpoint mutation exists only to trigger the governed protected-main first-six maintenance workflow after the exact implementation is confirmed live on Render. It does not alter model semantics, certification thresholds, publication authority, or `can_execute=false`.

Calibration challenger V2 is live at `c7cdb33e9e9f5c0c5aeae9b10371920bccffe913`; rerun all first-six maintenance lanes so Tennis and every binary team-state candidate are regenerated under the forward-only calibrator-selection contract before any certification review.
