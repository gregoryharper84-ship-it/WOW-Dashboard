# V17 MLB Game Winner DB Shadow Runner

Status: `RESEARCH_SHADOW_ONLY`

This runner exists to answer one question with real immutable evidence: does the
new MLB Game Winner challenger predict sporting outcomes better than the current
champion?

It does **not** change Game Winner admission, `NO_PICK` policy, payout/value
logic, the cash-single gate, portfolio rules, final refresh, or the
`V17_TERMINAL_REDUCER`.

## Evidence contracts

- Historical features: `wow_mlb_v2a_run_features_2024`
- Historical outcomes: `wow_mlb_team_games_2024`
- Forward events: `wow_mlb_forward_shadow_events`
- Forward frozen features: `wow_mlb_forward_feature_snapshots`
- Forward incumbent scores: `wow_mlb_forward_score_snapshots`
- Forward immutable grades: `wow_mlb_forward_shadow_grades`

Historical HOME/AWAY rows must form one exact pair per `game_key`. Forward grades
must bind one exact score snapshot, its exact HOME/AWAY feature snapshot IDs, and
the exact shadow event. Feature/model/prediction timestamps must all precede the
event start; outcome timestamps must not precede it.

## Model policy

- sporting features only
- no sportsbook / implied-probability / no-vig / payout / CLV inputs
- chronological train -> calibration -> untouched holdout
- pristine forward comparison against the incumbent
- Brier, log loss, calibration slope/intercept, and ECE
- `market_prior_weight = 0.0`
- `automatic_promotion = false`
- `probability_publishable = false`
- `can_execute = false`

A challenger that wins the research comparison still requires governed human/
repository promotion review. This runner cannot promote or tighten the lane.

## Production one-shot

`WOW_MLB_GAME_WINNER_SHADOW_EVAL_ON_START=1` schedules a one-time background
research run on the existing Render service using its already-managed database
connection. The flag defaults off. The task emits aggregate evaluation JSON to
logs and cannot take the serving process down if research evaluation fails.
