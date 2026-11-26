# WOW V17 — 13-Sport Capability Inventory & Specialist Expansion Roadmap
**Unified Engineering Execution Order — November 16, 2026**
**Identity:** GRAVITY / ANTIGRAVITY (NCAAF / Spreads / Model Factory / Coverage)
**Terminal Authority:** `V17_TERMINAL_REDUCER`
**Execution Restriction:** `can_execute=false` (immutable)

---

## 1. Catalog Inventory Summary (All 13 Named Sports)

Every sport × market is explicitly accounted for. Unsupported routes remain strictly blocked with typed failure codes; coverage is never fabricated.

| Sport | Moneyline (ML) / Outright | Spread / Run Line / Puck Line | Props (Player / Scalar) | Totals (Over / Under) | Active Owner |
|---|---|---|---|---|---|
| **MLB** | CERTIFIED (`MLB_GAME_WIN_PROBABILITY_EXPERT`) | CANDIDATE (Run Line -1.5/+1.5, append-only market ledger) | 5 CERTIFIED (SO, Outs, Strikes, Balls, PA); 1 HELD (1IP); 2 CANDIDATE (DFS) | CANDIDATE (Game Totals) | CURSOR / CORE (Off-season) |
| **NFL** | CANDIDATE (`nfl_event_model_v17`, 107 forward grades PASS) | CANDIDATE (PR #2633 spread registry + P2 features) | 4 CERTIFIED (Pass/Rush/Rec Yds, ATD); 1 CANDIDATE (DFS); 5 BUILD_REQ | CANDIDATE (Game Totals) | CODEX (#2490, #2422) |
| **NBA** | CANDIDATE (PR #2560 rebase, basketball specialist) | CANDIDATE (PR #2633 spread registry) | 8 CANDIDATE (Pts, Reb, Ast, PRA, P+R, P+A, R+A, DFS); 1 BUILD_REQ (3PM) | CANDIDATE (Game Totals) | CURSOR (#2560) |
| **WNBA** | CANDIDATE (Bradley-Terry numerical bridge; fitted candidate) | CANDIDATE (PR #2633 spread registry) | 4 SUPPORTED_HOLD (Pts, Reb, Ast, 3PM); 5 CANDIDATE (PRA, P+R, P+A, R+A, DFS) | CANDIDATE (Game Totals) | CURSOR / MODEL FACTORY |
| **NHL** | CANDIDATE (PR #2637 fitted specialist, Elo/goalie/rest) | CANDIDATE (Puck Line -1.5/+1.5, Regime Mixture totals) | 5 BUILD_REQ (Goals, Assists, Points, SOG, Saves) | CANDIDATE (PR #1952 / 12-season fixture verified) | CURSOR (#2637) |
| **NCAAF** | WORKING_NOT_COMPLETE (PR #2678 clock authority green, 3/3 CI) | CANDIDATE (PR #2633 spread registry) | 8 BUILD_REQ (Pass/Rush/Rec Yds, TDs, Attempts, Completions) | CANDIDATE (Game Totals) | ANTIGRAVITY (#2678) |
| **NCAAB** | CANDIDATE (SportsDataverse prior-form logistic candidate) | CANDIDATE (PR #2633 spread registry) | 5 BUILD_REQ (Pts, Reb, Ast, 3PM, PRA) | CANDIDATE (Game Totals) | ANTIGRAVITY |
| **SOCCER** | MODEL_BUILD_REQ (1X2 numerical bridge, multinomial candidate) | BUILD_REQ (Asian Handicap / Goal Line) | 5 BUILD_REQ (Shots, SOT, Goals, Assists, Saves) | BUILD_REQ (Match Totals 2.5) | ANTIGRAVITY (Expansion P1) |
| **TENNIS** | MODEL_BUILD_REQ (Tour/surface Elo hierarchy, retirement rules) | BUILD_REQ (Game Handicap, Set Handicap) | 4 BUILD_REQ (Aces, Double Faults, Games Won, Sets Won) | BUILD_REQ (Total Games Over/Under) | ANTIGRAVITY (Expansion P2) |
| **MMA / UFC** | MODEL_BUILD_REQ (Chronological fight-ledger Elo) | BUILD_REQ (N/A — Round Spreads) | 3 BUILD_REQ (Sig Strikes, Takedowns, Fight Time) | BUILD_REQ (Round Totals Over/Under) | ANTIGRAVITY (Expansion P3) |
| **GOLF (PGA)** | BUILD_REQ (No calibrated field or 2-ball/3-ball model) | BUILD_REQ (Matchup Spreads) | 3 BUILD_REQ (Birdies, Round Score, GIR) | BUILD_REQ (Finishing Position / Cut Line) | ANTIGRAVITY (Expansion P4) |
| **BOXING** | BUILD_REQ (No fight-winner specialist or adapter) | BUILD_REQ (N/A) | 2 BUILD_REQ (Punches Landed, Fight Time) | BUILD_REQ (Round Totals Over/Under) | ANTIGRAVITY (Expansion P5) |
| **CRICKET** | BUILD_REQ (No match-winner specialist or adapter) | BUILD_REQ (Run Line / Inning Handicaps) | 2 BUILD_REQ (Batter Runs, Bowler Wickets) | BUILD_REQ (Match / Innings Totals) | ANTIGRAVITY (Expansion P6) |

---

## 2. Priority Expansion Tasks (Unsupported Families)

Each unsupported family must progress through the governed 8-stage lifecycle:
`LEGAL USABLE SOURCE HASH` → `PREGAME POINT-IN-TIME FEATURES` → `GENUINE FITTED CANDIDATE` → `WALK-FORWARD HOLDOUT` → `CONSERVATIVE CALIBRATED BOUNDS` → `IMMUTABLE EVIDENCE RECEIPT` → `INDEPENDENT QA/RELEASE APPROVAL` → `LIVE USER PATH VERIFICATION`.

### Priority 1: SOCCER (3-Way 1X2 & Goal Lines)
- **Specialist Family:** `SOCCER_1X2_WIN_PROBABILITY_EXPERT_V1`
- **Required Inputs:** `official_event_id`, `home_team`, `away_team`, `competition_league`, `table_position_points`, `expected_goals_xg_prior`, `rest_days_travel`, `lineup_confirmed_status`, `settlement_basis`.
- **Settlement Rule:** Regulation time (90 min + injury time) settlement. Extra time and penalty shootouts explicitly excluded unless listed under separate tournament qualifier market.
- **Distribution:** 3-way multinomial ($P(\text{Home}) + P(\text{Draw}) + P(\text{Away}) = 1.0$) with Dirichlet-calibrated lower bounds.
- **Stage Blocker:** Competition-scoped multi-season corpus freeze and 3-way calibration holdout.

### Priority 2: TENNIS (Match Winner & Game Totals)
- **Specialist Family:** `TENNIS_MATCH_WIN_PROBABILITY_EXPERT_V1`
- **Required Inputs:** `official_event_id`, `player_one`, `player_two`, `tour` (ATP/WTA), `surface` (HARD/CLAY/GRASS), `player_surface_elo`, `rest_days`, `recent_form`, `head_to_head_record`, `settlement_basis`.
- **Settlement Rule:** Official tour settlement; walkovers/retirements handled fail-closed (`TENNIS_MATCH_VOID_RETIREMENT` unless minimum set completed per rule).
- **Distribution:** Binary winner probability with surface-conditioned logistic calibrator.
- **Stage Blocker:** Surface-specific retirement-censored historical dataset freeze.

### Priority 3: MMA / UFC (Fight Winner & Round Totals)
- **Specialist Family:** `MMA_FIGHT_WIN_PROBABILITY_EXPERT_V1`
- **Required Inputs:** `official_event_id`, `fighter_one`, `fighter_two`, `weight_class`, `scheduled_rounds` (3 or 5), `reach_inches`, `stance`, `takedown_defense_pct`, `significant_strike_diff`, `settlement_basis`.
- **Settlement Rule:** Official Commission decision/stoppage. Draws and No Contests settle void/push.
- **Distribution:** 3-way probability (Fighter 1, Fighter 2, Draw) + Round duration survival distribution.
- **Stage Blocker:** Historical fight-ledger chronological replay with weight-class normalization.

### Priority 4: GOLF / PGA (Matchups & Field Outrights)
- **Specialist Family:** `PGA_TOURNAMENT_FIELD_EXPERT_V1`
- **Required Inputs:** `official_event_id`, `player_id`, `tournament_course_sg_profile`, `strokes_gained_tee_to_green`, `strokes_gained_putting`, `course_fit_index`, `recent_cuts_made`, `settlement_basis`.
- **Settlement Rule:** Official PGA Tour stroke play; 72 holes or shortened official tournament result. Disqualifications/withdrawals handled per rule.
- **Distribution:** Plackett-Luce or Extreme Value field rank distribution; pairwise logistic for 2-ball/3-ball matchups.
- **Stage Blocker:** Course-level strokes-gained calibration package build.

### Priority 5: BOXING (Fight Winner & Round Totals)
- **Specialist Family:** `BOXING_FIGHT_WINNER_EXPERT_V1`
- **Required Inputs:** `official_event_id`, `boxer_one`, `boxer_two`, `weight_division`, `scheduled_rounds` (10 or 12), `reach_cm`, `ko_percentage`, `ring_rust_days`, `settlement_basis`.
- **Settlement Rule:** Official sanctioning body decision/KO/TKO. Technical draws/decisions handled per rule.
- **Distribution:** 3-way probability (Boxer 1, Boxer 2, Draw) with Platt-calibrated bounds.
- **Stage Blocker:** Sanctioning body record reconciliation and weight-class historical corpus.

### Priority 6: CRICKET (Match Winner & Innings Totals)
- **Specialist Family:** `CRICKET_MATCH_WINNER_EXPERT_V1`
- **Required Inputs:** `official_event_id`, `team_one`, `team_two`, `match_format` (T20, ODI, TEST), `toss_decision`, `venue_first_innings_avg`, `pitch_condition`, `dl_par_ready`, `settlement_basis`.
- **Settlement Rule:** Official ICC result; DLS calculation for rain-shortened matches; super over/bowl-out counted per match rules.
- **Distribution:** Binary winner probability (T20/ODI) or 3-way (Test Match with Draw).
- **Stage Blocker:** Format-specific Cricsheet frozen corpus ingestion and DLS weather-reduction adapter.

---

## 3. Governance Invariants (Immutable)

1. `can_execute = false`: No wager placement, order routing, approval or modification.
2. `V17_TERMINAL_REDUCER`: Sole terminal reducer authority across all 13 sports.
3. `No Fabricated Coverage`: If a sport, market or line does not possess a verified fitted specialist, it must terminate in a typed `SAFE_HOLD` or `UNSUPPORTED` status code.
4. `Independent Verification`: SIRT (#1563) and Release Authority (#1550) remain distinct and independent from engineering writers.

## 4. Complete 73-Route Prop Subroute Reconciliation Inventory

| Sport | Stat Type | Lane Status | Publication Allowed | Controlling Specialist | Stage Blocker |
|---|---|---|---|---|---|
| BOXING | PUNCHES_LANDED | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| BOXING | FIGHT_TIME | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| CRICKET | BATTER_RUNS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| CRICKET | BOWLER_WICKETS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| MLB | PITCHER_STRIKEOUTS | CERTIFIED_PRODUCTION | YES | `wow.mlb-pitcher-failure-path-expert` | None (Certified) |
| MLB | PITCHING_OUTS | CERTIFIED_PRODUCTION | YES | `wow.mlb-pitcher-outs-workload-expert` | None (Certified) |
| MLB | STRIKES_THROWN | CERTIFIED_PRODUCTION | YES | `wow.mlb-pitcher-pitch-composition-expert` | None (Certified) |
| MLB | BALLS_THROWN | CERTIFIED_PRODUCTION | YES | `wow.mlb-pitcher-pitch-composition-expert` | None (Certified) |
| MLB | PLATE_APPEARANCES | CERTIFIED_PRODUCTION | YES | `wow.mlb-batter-plate-appearances-expert` | None (Certified) |
| MLB | 1ST_INNING_PITCHES_THROWN | SUPPORTED_HOLD_ONLY | NO | `wow.mlb-first-inning-pitch-count-expert` | `MLB_1IP_PUBLICATION_HELD` |
| MLB | HITTER_FANTASY_SCORE | CANDIDATE_ONLY | NO | `wow.mlb-hitter-fantasy-score-expert` | `FANTASY_SCORE_CANDIDATE_NOT_PROMOTED` |
| MLB | PITCHER_FANTASY_SCORE | CANDIDATE_ONLY | NO | `wow.mlb-pitcher-fantasy-score-expert` | `FANTASY_SCORE_CANDIDATE_NOT_PROMOTED` |
| MMA | SIGNIFICANT_STRIKES | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| MMA | TAKEDOWNS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| MMA | FIGHT_TIME | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| NBA | POINTS | CANDIDATE_ONLY | NO | `wow.nba-player-prop-probability-expert` | `NBA_SCALAR_CANDIDATE_NOT_PROMOTED` |
| NBA | REBOUNDS | CANDIDATE_ONLY | NO | `wow.nba-player-prop-probability-expert` | `NBA_SCALAR_CANDIDATE_NOT_PROMOTED` |
| NBA | ASSISTS | CANDIDATE_ONLY | NO | `wow.nba-player-prop-probability-expert` | `NBA_SCALAR_CANDIDATE_NOT_PROMOTED` |
| NBA | PRA | CANDIDATE_ONLY | NO | `wow.nba-player-prop-probability-expert` | `NBA_SCALAR_CANDIDATE_NOT_PROMOTED` |
| NBA | POINTS_REBOUNDS | CANDIDATE_ONLY | NO | `wow.nba-player-prop-probability-expert` | `NBA_SCALAR_CANDIDATE_NOT_PROMOTED` |
| NBA | POINTS_ASSISTS | CANDIDATE_ONLY | NO | `wow.nba-player-prop-probability-expert` | `NBA_SCALAR_CANDIDATE_NOT_PROMOTED` |
| NBA | REBOUNDS_ASSISTS | CANDIDATE_ONLY | NO | `wow.nba-player-prop-probability-expert` | `NBA_SCALAR_CANDIDATE_NOT_PROMOTED` |
| NBA | FANTASY_SCORE | CANDIDATE_ONLY | NO | `wow.nba-dfs-fantasy-score-expert` | `FANTASY_SCORE_CANDIDATE_NOT_PROMOTED` |
| NBA | THREE_POINTERS_MADE | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| NCAAB | POINTS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| NCAAB | REBOUNDS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| NCAAB | ASSISTS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| NCAAB | THREE_POINTERS_MADE | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| NCAAB | PRA | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| NCAAF | PASSING_YARDS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| NCAAF | PASSING_TOUCHDOWNS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| NCAAF | PASS_ATTEMPTS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| NCAAF | COMPLETIONS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| NCAAF | RUSHING_YARDS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| NCAAF | RUSH_ATTEMPTS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| NCAAF | RECEIVING_YARDS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| NCAAF | RECEPTIONS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| NFL | PASSING_YARDS | CERTIFIED_PRODUCTION | YES | `wow.nfl-player-prop-probability-expert` | None (Certified) |
| NFL | RUSHING_YARDS | CERTIFIED_PRODUCTION | YES | `wow.nfl-player-prop-probability-expert` | None (Certified) |
| NFL | RECEIVING_YARDS | CERTIFIED_PRODUCTION | YES | `wow.nfl-player-prop-probability-expert` | None (Certified) |
| NFL | ANYTIME_TD | CERTIFIED_PRODUCTION | YES | `wow.nfl-player-prop-probability-expert` | None (Certified) |
| NFL | FANTASY_SCORE | CANDIDATE_ONLY | NO | `wow.nfl-dfs-fantasy-score-expert` | `FANTASY_SCORE_CANDIDATE_NOT_PROMOTED` |
| NFL | PASSING_TOUCHDOWNS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| NFL | PASS_ATTEMPTS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| NFL | COMPLETIONS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| NFL | RUSH_ATTEMPTS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| NFL | RECEPTIONS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| NHL | GOALS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| NHL | ASSISTS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| NHL | POINTS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| NHL | SHOTS_ON_GOAL | BUILD_REQUIRED | NO | None | `NHL_SOG_REAL_MULTI_SEASON_CORPUS_REQUIRED` |
| NHL | SAVES | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| PGA | BIRDIES | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| PGA | ROUND_SCORE | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| PGA | GREENS_IN_REGULATION | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| SOCCER | SHOTS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| SOCCER | SHOTS_ON_TARGET | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| SOCCER | GOALS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| SOCCER | ASSISTS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| SOCCER | SAVES | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| TENNIS | ACES | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| TENNIS | DOUBLE_FAULTS | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| TENNIS | GAMES_WON | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| TENNIS | SETS_WON | BUILD_REQUIRED | NO | None | `PROP_FITTED_SPECIALIST_BUILD_REQUIRED` |
| WNBA | POINTS | SUPPORTED_HOLD_ONLY | NO | `wow.wnba-player-prop-probability-expert` | `WNBA_PROP_PROSPECTIVE_NOT_PUBLISHABLE` |
| WNBA | REBOUNDS | SUPPORTED_HOLD_ONLY | NO | `wow.wnba-player-prop-probability-expert` | `WNBA_PROP_PROSPECTIVE_NOT_PUBLISHABLE` |
| WNBA | ASSISTS | SUPPORTED_HOLD_ONLY | NO | `wow.wnba-player-prop-probability-expert` | `WNBA_PROP_PROSPECTIVE_NOT_PUBLISHABLE` |
| WNBA | THREE_POINTERS_MADE | SUPPORTED_HOLD_ONLY | NO | `wow.wnba-player-prop-probability-expert` | `WNBA_PROP_PROSPECTIVE_NOT_PUBLISHABLE` |
| WNBA | PRA | CANDIDATE_ONLY | NO | `wow.wnba-composite-prop-expert` | `WNBA_COMPOSITE_CANDIDATE_NOT_PROMOTED` |
| WNBA | POINTS_REBOUNDS | CANDIDATE_ONLY | NO | `wow.wnba-composite-prop-expert` | `WNBA_COMPOSITE_CANDIDATE_NOT_PROMOTED` |
| WNBA | POINTS_ASSISTS | CANDIDATE_ONLY | NO | `wow.wnba-composite-prop-expert` | `WNBA_COMPOSITE_CANDIDATE_NOT_PROMOTED` |
| WNBA | REBOUNDS_ASSISTS | CANDIDATE_ONLY | NO | `wow.wnba-composite-prop-expert` | `WNBA_COMPOSITE_CANDIDATE_NOT_PROMOTED` |
| WNBA | FANTASY_SCORE | CANDIDATE_ONLY | NO | `wow.wnba-dfs-fantasy-score-expert` | `FANTASY_SCORE_CANDIDATE_NOT_PROMOTED` |
