# Game Winner Shadow Evaluation Acceptance

The DB-backed runner is accepted only when all of the following are true:

1. Historical feature rows form exact one-HOME/one-AWAY pairs for every evaluated `game_key`.
2. Historical outcomes are internally consistent and joined without inferred identity.
3. Forward grades bind exact score snapshot IDs and exact HOME/AWAY feature snapshot IDs.
4. Forward feature, model, score, and prediction timestamps are strictly pregame.
5. Forward outcome timestamps are not pregame.
6. Historical and forward HOME/AWAY rows share the exact persisted 38-feature run-side schema.
7. No sportsbook, no-vig, payout, CLV, or postgame outcome field enters the sporting feature vector.
8. Retrospective evaluation is chronological train -> calibration -> untouched holdout.
9. Forward evaluation uses immutable graded events only and compares incumbent vs challenger using Brier, log loss, calibration slope/intercept, and ECE.
10. Evaluation output is research-only: `automatic_promotion=false`, `probability_publishable=false`, `can_execute=false`.
11. No Game Winner admission, `NO_PICK`, cash-single, portfolio, final-refresh, or terminal-reducer rule is modified.
12. A challenger win can only produce `SHADOW_REVIEW_REQUIRED`; it never auto-promotes.
