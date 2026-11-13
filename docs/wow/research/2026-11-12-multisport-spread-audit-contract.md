# LLP Exact-Line Multisport Spread Research — Adoption & Acceptance

**Status:** SOURCE / RESEARCH ONLY. No live Daily route, Action, model promotion, deployed proof, or editor sync is established by this document.

## Scope

The new `v17.llp_spread_research_card.audit_spread_research_card` accepts:
- `official_slate`: current externally authenticated list with sport, official_event_id, home_team, away_team, event_start_time_utc, event_state, market_type and settlement_basis.
- `picks`: one pregame selected_side HOME/AWAY, exact `signed_home_line`, immutable pregame pick receipt, verified-at-source flags, `frozen_at_utc`, `available_to_user_at_utc`, a nonempty stable `quote_source_id` on both pick and its matching `quote`, and quote `observed_at_utc`, source availability and provider receipt. The `quote_source_id` is the caller's exact provider/feed identifier (e.g., `OFFICIAL_PROVIDER_FEED_A`) and must match verbatim; it is not source authentication by itself. The quote must be available to users at or before the pick becomes visible. All timestamps timezone-aware and strictly pregame. Quote freshness is checked **at freeze**, where `frozen_at_utc - observed_at_utc` must not exceed 60 minutes. This does not measure quote age at presentation and is not a fitted model training threshold.
- `finals` (optional): independent event-matched result with settlement receipt/source/status, `settled_at_utc` and appropriate units: `home_score/away_score` for sports with game scores, `home_games/away_games` for tennis game handicap, `home_sets/away_sets` for tennis set handicap.
- explicit `official_slate_verified=True` and `official_slate_receipt_id` only after external authentication; function does not authenticate the league or provider itself.

The output preserves all playable scheduled events with typed `VALID`/ `HELD`/ `PENDING` statuses, count of unmatched picks/finals, exact side/line, separate full WIN/LOSS/PUSH/HALF_WIN/HALF_LOSS and sport breakdown. A postponed or cancelled event is visible but excluded from the playable denominator. Extra pick IDs are visible HELD, not hidden.

**Metrics boundary:** `headline_full_win_rate` appears only when the complete attested selected cohort is independently settled, excludes pushes, and has no half-outcome observations. It is descriptive realized accuracy, not a calibrated model sporting probability or an expected-value edge. Mixed quarter-line half outcomes are preserved as their own settlement counts. No Brier/log loss or rank promotion without genuinely calibrated fitted probabilities from the controlling model.

## Examples and required controls

| Sport | Exact research market | Settlement |
|---|---|---|
| NFL / NBA / WNBA / NCAAF / NCAAB | POINT_SPREAD | Full game including overtime; exact integer pushes |
| NHL | PUCK_LINE | Full game including shootout; official final score basis |
| MLB | RUN_LINE | Full game including extra innings |
| Soccer | ASIAN_HANDICAP | Regulation; quarter handicap split into two neighboring half-lines |
| Tennis | GAME_HANDICAP / SET_HANDICAP | Completed match, games vs sets separately; retirement HELD |

A signed home line `-3.5` means **home must win by at least 4**; the selected-away line is `+3.5`. Opposite sides, swapped teams, different market type, regulation-only versus full-game quotes, duplicate IDs, stale timestamps and mismatched settlement records fail closed. The market line is a threshold; no market odds or moneyline percentage is a sporting model probability.

## What remains to reach genuine accurate spread intelligence

1. Adopt this pure function through the existing LLP user-facing Daily/card path *only after* authenticated official manifest, exact frozen price/line feed and pregame receipt issuer exist; keep user-visible research distinct from governed fitted probabilities.
2. Reuse `v17.spread_margin_challenger`, `spread_forward_shadow_leagues`, `spread_exact_line_replay`, `spread_card_admission`, and `spread_certification_decision` for NFL/NBA/WNBA/NCAAF/NCAAB. Repair source coverage/official identity gaps instead of swapping in synthetic line grids or reconstructed historical quotes.
3. NHL puck-line: build goalie-aware goal-difference predictive distribution from independently authorized historical data; do not reuse basketball point-margin residuals. MLB run line: preserve independent run-distribution fit and extra-inning settlement. Soccer Asian: scoreline distribution with draw mass, regulation rules and quarter-line split probability; tennis: match/set/game distributions, surface and retirement rules. Fit only after licensed/free pregame data is actually available and independently time-valid.
4. Chronological train/calibration/untouched holdout, prospective immutable forward receipts, exact-line p_cover/p_push/p_not_cover and lower-bound certification. Benchmark exact same-event/time spreads against vig-adjusted reference price and 50% non-push baseline; track three-way Brier, conditional non-push Brier/log, calibration bias/ECE, margin MAE/RMSE, subgroup tail reliability and paired confidence intervals.
5. SIRT #1563 independently reviews accuracy/coverage, QA #1521 verifies relevant NFL Pick'em effects, and existing protected checks/Release Authority apply to the exact head. Do not auto-merge or bypass authorization. Require Render web/worker deployed SHA readback and live GPT editor sync before claiming serving.

**Authority:** `LLP_TEAM_BETTING_ENGINE` owns team/event spreads; `V17_TERMINAL_REDUCER` owns terminal decisions. No new serving registry, scheduled task, paid feed or bets. `can_execute=false` at every stage.
