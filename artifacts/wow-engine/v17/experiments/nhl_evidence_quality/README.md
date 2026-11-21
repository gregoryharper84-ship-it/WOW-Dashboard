# NHL November 17 source-quality and loss-tail patch

**Scope:** additive Class C research-only diagnostics under Project Hub #2076, NHL
ML owner #1952, and the existing WOW_PROP_LANE. These functions do not run on
the serving path, change the incumbent fitted NHL model's feature schema, alter
calibration, or activate a prop model. They do not certify or publish any
probabilities and cannot execute wagers.

## Evidence motivating the change

November 17, 2026 research-card NHL moneyline losses included Boston 0-3 LAK,
Florida 2-5 EDM, Buffalo 3-4 PHI and San Jose 2-5 CGY. **Three of these four
losses were by three goals; Buffalo was a one-goal loss.** That suggests testing
whether the research card missed goalie mismatch, shot-quality suppression,
special teams, lineup availability, or heavy-tail scenarios. Four losses cannot
identify the cause or validate a new coefficient. Never infer a retrospective
high-margin-loss probability from an ML pick or postgame score.

November 17 NHL player-prop misses include Elias Lindholm 1+ point (0) and
Connor Bedard 1+ assist (0). A points/assists miss is not inherently evidence of
bad source data: separate role/opportunity calibration from shot conversion and
injury effects before attributing causes. Past outcomes are not historical
pregame bookmaker offers.

## Implemented checks

- `audit_nhl_ml_loss_tails` grades exact official full-game OT/SO winner
  selections with pregame receipt metadata and independently attributed final
  scores, refuses partial grading when any row has invalid timing/identity,
  and reports a three-plus-goal-loss severity count. No probability invented.
- `screen_nhl_pregame_evidence` preflights role-specific timestamps, source
  commitments, event identity, lineup and goalie status, team/opponent evidence,
  exact player identity, book, side and threshold. Returns a typed HOLD for
  missing/stale/conflicting source claims. Starter verification remains the
  responsibility of the existing **trusted independent-source registry** in
  `nhl_starter_goalie_confirmation.py`; a claimed receipt ID in an input does
  not prove that verification actually occurred.
- Skater prop requirements include TOI/PP-TOI, opportunities appropriate to
  goals/assists/points/SOG, line assignments, opponent suppression, injury,
  special-teams and exact pregame offer evidence. Saves use a different
  goalie-workload plus opponent-shot-volume contract; they must not inherit
  skater PP/TOI requirements.
- All passes are **precheck passed, not eligible for model ranking/publication**.
  Both functions always return `probability_publishable=false`,
  `can_execute=false` or its equivalent, and no certified confidence.

## Free-first source hierarchy and rights gate

| Evidence | Preferred source | Correct temporal use |
| --- | --- | --- |
| Official fixture, final scores, skater SOG/points, goalie saves and TOI | NHL official Web API gamecenter and official reports | Official settled outcomes for grading; only pregame-captured game/roster context for predictions |
| Historical five-on-five expected goals, shot danger, individual expected goals and team opponent defense | MoneyPuck game, player and shot datasets with attribution | Preserve version, rights review, snapshot time and *last completed game* cutoff; nightly updates may lag |
| Shift/role/special-teams involvement | Official NHL shifts and game reports, with verified lineup context | Prior-game rolling role only; never use postgame shift charts from the target game as a forecast |
| Starting goalies, lineup/injury updates | Club/league official posts, checked through existing independent goalie authority | Timestamped before puck drop; only registry-approved independent sources may yield `CONFIRMED` |
| Current units and line combinations | Legitimately accessible team reports and corroborated public line charts | Time-stamped research context; do not claim official confirmation from a single unofficial chart |
| Exact NHL prop line, direction, book, market settlement and price | Authenticated authorized sportsbook/market offer or verified permitted quote | Capture immutable offered line/price before puck drop; no closing-line backfill |

MoneyPuck: https://www.moneypuck.com/data.htm (credit MoneyPuck.com).
NHL public gamecenter: https://api-web.nhle.com/v1/gamecenter/{game_id}/boxscore .
Existing source-rights matrix: `artifacts/wow-engine/docs/six_sport_prop_history_source_matrix_20261029.md`.
The repository currently marks NHL official API reuse for production training
as `LICENSE_REVIEW_REQUIRED`. Public accessibility is **not** training/reuse
authorization. No paid acquisition, unsafe scraping, or bypassing access rules.

## Required follow-through before substantive model change

1. Replay a full predeclared multi-season cohort of **frozen pregame** ML
   forecasts against independent official results. Compute Brier/log loss and
   both favorite-collapse rate (loss by 3+ goals) and full goal-difference
   distribution calibration. Segment by home/away, rest, goalie-change, 5v5
   xG, special teams, opponent transition/shot suppression; report intervals.
2. Obtain rights-reviewed, timestamped **pregame** NHL player opportunity,
   usage, lineup and quote receipts. Grade goals/assists/points/SOG/saves
   separately against exact settlement; measure count MAE, CRPS, Brier,
   and subgroup reliability at the offered threshold.
3. Compare the current certified/incumbent lane (where applicable) against
   a candidate on paired untouched chronological holdouts, with ablations
   demonstrating incremental value from new inputs. No refit or promotion
   using November 17 outcomes as feature selection truth.
4. Wire the preflight into the governed source-preparation and research-card
   path *after* independent code and data-rights review. The present utility
   alone does **not** protect all existing research card generators.
5. Keep LLP_TEAM_BETTING_ENGINE as full-game owner, WOW_PROP_LANE as scalar
   prop owner, V17_TERMINAL_REDUCER as sole decision authority and
   `can_execute=false`. No automated betting or order submission.

## Tests

```bash
python -m pytest -q artifacts/wow-engine/tests/test_nhl_evidence_quality.py
```

Synthetic fixtures test failure paths and the observed November 17 **score
pattern**. Synthetic receipt strings are not authenticated records. Passing
the tests never demonstrates a live collector, full NHL model, or actual
probability improvement.
