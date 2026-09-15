# LLP Team-State Accuracy V1

Status: challenger architecture. Production champions remain unchanged until sport-specific replay and certification earn promotion.

This program addresses a model-accuracy gap, not a presentation/compliance gap. Recent results are evidence; recent underlying process is signal; a structurally supported change in process is stronger signal. None of the features below is a manual probability adjustment. Every probability effect must be learned by a fitted specialist and survive chronological holdout, calibration, champion/challenger comparison, and governed promotion.

## 1. Dynamic Team Form & Process
Track L3/L5/L10 win rate and scoring differential, season baseline, recent-vs-season change, process-form margin where a certified process metric exists, result/process divergence, and streak direction/length. Raw streaks never become probabilities.

## 2. Opponent/Schedule-Adjusted Form
Normalize recent differential by opponent prior strength; strong opposition raises schedule-adjusted performance and weak opposition discounts it. Rest, travel, congestion, and schedule-adjusted trend remain separate inputs.

## 3. Structural Change / Change Point
Provide a statistical change-point score/status and typed driver evidence: personnel, tactical, schedule, plausible-unconfirmed, no identified driver, or contradicted by underlying process. `STREAK_WITHOUT_DRIVER` is a diagnostic signal, not an automatic fade.

## 4. Sustainability / Regression vs Skill
Compare observed result form with sport-specific process form where available. Residual outcome variance, sustainability, and unsustainable-variance features let the fitted model learn regression without universal probability haircuts.

## 5. Roster / Lineup Continuity
Use target-game roster/lineup evidence only when it is certified pregame data. Otherwise measure continuity from prior games. Postgame identities may become history for later events but may never leak into the target event's pregame feature vector.

## 6. Matchup Interaction Features
Model cross-team interactions such as home attack vs away defense, away attack vs home defense, style edge, pace interaction, and pace mismatch. Sport adapters may supply richer interaction bases; missing values stay neutral rather than invented.

## 7. Favorite Fragility / Underdog Upside
Fragility and upside are fitted features built from sustainability, trend, continuity, congestion, change points, schedule-adjusted form, and matchup interactions. They are not stand-alone upset probabilities and do not use payout/public narrative.

## 8. Season-Regime Weighting
Encode EARLY/MID/LATE from current-season game count. The fitted model learns different behavior by regime; no hand-authored probability bonus/penalty is applied.

## 9. Feature-Level Postmortem Attribution
Immutable retros can retain the pregame home/away team-state snapshot and tags such as `STREAK_WITHOUT_DRIVER`, `RESULTS_PROCESS_DIVERGENCE`, `FORM_SCHEDULE_INFLATED`, and `FORM_SCHEDULE_SUPPRESSED`, allowing miss clusters to be studied without hindsight rewriting.

## 10. Champion/Challenger Validation
Dynamic team-state artifacts are challengers. Compare champion and challenger on the same immutable outcomes using Brier score and log loss. Better metrics are promotion evidence only; `automatic_promotion=false` and certification/replay remain mandatory.

## 11. Coverage & Data Resilience
Maintain exact run-coverage accounting and typed source fallback. Partial coverage cannot support a slate-wide `NONE_QUALIFIED` claim. Source states include OK, stale, auth failed, rate limited, schema changed, timeout, oversized payload, and conflict. A fallback can preserve a feature only when fresh and non-conflicting.

## First deployment scope
Candidate maintenance covers NFL, MLB, NBA, WNBA, NCAAF, NCAAB, and supported Soccer competitions. Individual-event sports (Tennis, PGA, MMA, Boxing) are not forced through a team-state schema; their sport-specific models remain authoritative.

## Governance invariants
- Exactly one controlling fitted specialist owns a published event probability.
- Market implied probability, ATS record, recent raw record, and generic reasoning cannot become governed probability.
- Candidate artifacts cannot self-certify or self-promote.
- Production champions are not overwritten by this deployment.
- Challenger maintenance always has `probability_publishable=false` and `can_execute=false`.
