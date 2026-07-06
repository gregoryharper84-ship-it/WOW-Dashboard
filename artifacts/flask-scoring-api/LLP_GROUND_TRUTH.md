# LLP Engine — Ground Truth Sync Block

**Purpose:** Paste this at the top of any ChatGPT or Claude planning thread before
asking for changes to `artifacts/flask-scoring-api/app.py`. ChatGPT and the
external Claude thread cannot see the code — they plan against memory and drift.
This pins the *actual shipped reality* so all three agents share one source of
truth. **The code is the source of truth; this doc is a snapshot of it.**

_Snapshot of: `artifacts/flask-scoring-api/app.py` (~11,600 lines). Production:
`create-app-gregoryharper84.replit.app`._

**Active spec: WOW v16 Clean Core** (effective 2026-06-04, supersedes v15.3.0).
If a planning thread's project file still reads v15.3.0 (e.g. a stale Claude
project file), it is **out of date** — this doc + the shipped code are the
implementation ground truth and override it. Accepted v16 delta-candidates that
do not conflict with this doc stand (PrizePicks stays Model-Qualified, separate
from Market-Verified; Goblin 0.5 props route to Ceiling Scout not Core Profit;
partial L5 → Watch/Conditional not auto-reject; LESS discipline on average vs
line; exact prop-category audit PRA ≠ Pts+Rebs ≠ Points; negative DES on a
related prop triggers conflict investigation; soccer squad-identity mismatch in
international/friendly stays Gate 1 hard kill).

---

## 0. Layer model (read this first)

```
discovery  →  validation  →  approval
```

- **F5 routing is market SELECTION, not bet APPROVAL.** F5 availability may
  convert a weak full-game MLB ML into an `F5_ML` *candidate*. It NEVER
  auto-upgrades anything to BET. Final approval still requires: LLP badge
  `ANCHOR`/`BET`, positive edge, positive Kelly, verified context, and a clean
  failure-path review.
- The additive-only model invariant holds to 1e-6 and must never be broken:
  `model_win_probability − no_vig_implied_probability == sum(model_adjustments)`.

---

## 1. Badge ladder (rank order)

Higher rank wins. Ceilings only ever **lower** a badge, never raise it.

| Badge       | Rank | Meaning (raw) |
|-------------|------|---------------|
| `ANCHOR`    | 6    | BET-clean + low fragility + edge ≥ 3.5% + independent model + verified market_cause + confirmed starter/lineup + no failures |
| `BET`       | 5    | discovery + validation clean, BET/SMALL BET decision |
| `QUALIFIED` | 4    | discovery + validation clean, edge sub-bet |
| `LEAN`      | 3    | positive edge but starter/lineup unverified |
| `WAIT`      | 2    | WATCH, or actionable edge blocked on unverified market_cause/timing |
| `CANDIDATE` | 1    | discovery signal, validation incomplete (no edge yet) |
| `PASS`      | 0    | hard fail: TRAP, negative edge, negative CLV, no market |

---

## 2. Confidence tiers (by `abs(edge)`)

| Tier      | Threshold      |
|-----------|----------------|
| `STRONG`  | ≥ 0.045        |
| `MEDIUM`  | ≥ 0.025        |
| `SMALL`   | ≥ 0.012        |
| `PASS`    | < 0.012        |
| `UNKNOWN` | edge is `None` |

---

## 3. Final decision logic

`_llp_decision(edge, model_p, novig_p, upset_score, trap_flag, failures)`:

1. `trap_flag` → **TRAP**
2. `edge` or `model_p` is None → **WATCH**
3. `edge < 0` → **PASS**
4. ≥ 3 **hard** failures (advisory tags filtered out) AND tier ∈ {MEDIUM, SMALL} → **WATCH**
5. tier `STRONG` → **BET**
6. tier `MEDIUM` → **SMALL BET**
7. tier `SMALL` → **WATCH**
8. else → **PASS**

---

## 4. Badge ceiling (`_llp_apply_spec_badge_ceiling`) — only lowers

| Condition | Cap at |
|-----------|--------|
| `opening_line` is None (no movement reference) | `WAIT` |
| `clv_beat` is None (no CLV anchor) | `WAIT` |
| `kelly_stake` is None or ≤ 0 | `CANDIDATE` |
| short-fav trap: `no_vig_implied_probability` ≥ 0.55 AND `edge` < 0.04 | `LEAN` |
| any of `stale-market-not-actionable`, `candidate-promoted-too-early`, `fake-market-edge` in `failure_paths` | `CANDIDATE` |

---

## 5. F5 (MLB first-5-innings) market routing — Step 5, LIVE

`_llp_choose_mlb_target_market(rec)` returns `recommended_market`:

1. sport ≠ MLB → `ML` (passthrough; no F5 routing)
2. market ≠ h2h → market upper-cased (passthrough)
3. `bullpen_reliability` > 0.50 **OR** `edge` ≥ 0.04 → `ML` (full-game actionable)
4. `f5_available` is True → `F5_ML`
5. else → `ML_WATCH_ONLY`

`_llp_mlb_fullgame_f5_advisory(rec)` now **derives from the chooser** (single
source of truth): returns True iff chooser ≠ `ML` for an MLB h2h record.

**Rollback flag:** set env `LLP_DISABLE_MLB_F5=1` → MLB odds fetch reverts to
full-game-only (`h2h,spreads,totals`), recovering ~2x Odds API credits. F5
routing then degrades gracefully (`f5_available=False`,
`recommended_market="ML_WATCH_ONLY"`).

**Market aliases** (all normalize to canonical Odds API keys):
`ml`/`moneyline`/`h2h` → `h2h`; `spread`/`spreads` → `spreads`;
`total`/`totals`/`ou` → `totals`; `f5`/`f5_ml`/`f5_h2h`/`first5`/`first5_ml` →
`h2h_1st_5_innings`; `f5_spread`/`f5_spreads`/`first5_spread` →
`spreads_1st_5_innings`; `f5_total`/`f5_totals`/`f5_ou`/`first5_total` →
`totals_1st_5_innings`.

---

## 6. Failure-path tags

### Hard tags (7) — count toward the ≥ 3 cardinality demotion
- `missing-odds-feed`
- `missing-lineup-status`
- `missing-starter-confirmation`
- `stale-market-not-actionable`  *(also caps badge at CANDIDATE)*
- `clv-without-validation`
- `fake-market-edge`  *(also caps badge at CANDIDATE)*
- `candidate-promoted-too-early`  *(also caps badge at CANDIDATE)*

### Advisory tags (3) — informational, do NOT count toward cardinality
- `_LLP_MLB_FULLGAME_PREFERS_F5`
- `_LLP_RECOMMEND_F5_ML`
- `_LLP_ML_WATCH_ONLY_NO_F5`

---

## 7. Per-record field contract (top-level keys on every analyze record)

```
book                         bullpen_reliability         clv_beat
clv_delta_pts                confidence_tier             current_line
discovery                    discovery_clean             edge
f5_american                  f5_available                f5_book
f5_total_line                failure_paths               favorite_trap_flag
final_decision               full_game_edge_allowed      implied_probability
injury_context               injury_rest_context         kelly_stake
lineup_status                llp_badge                   lock_line
market_movement_clv_status   mlb_fullgame_prefers_f5     model_adjustments
model_win_probability        moneyline_fragility         no_vig_implied_probability
opening_line                 prop_correlation_support    recommended_market
rest_context                 starter_lineup_confirmation starter_status
team_ratings                 upset_score                 validation_clean
weather_park
```

`recommended_market` ∈ {`ML`, `F5_ML`, `ML_WATCH_ONLY`, `<MARKET>`}.
`full_game_edge_allowed` is True only when `recommended_market == "ML"`.

---

## 8. Data-source map (free stack + paid Odds API)

| Source | Base URL | Used for | Auth |
|--------|----------|----------|------|
| **The Odds API** v4 | `https://api.the-odds-api.com/v4` | Odds for all sports; MLB F5 markets pulled alongside full-game | `ODDS_API_KEY` (paid) |
| **MLB Stats API** | `https://statsapi.mlb.com/api/v1` (+ `v1.1/game/{pk}/feed/live`) | Schedule, probable pitchers, lineup confirmation, live feed | none (free) |
| **ESPN public JSON** | `site.api.espn.com` / `site.web.api.espn.com` | Injuries, team standings/ratings | none (free) |
| **NWS CLI product** | `forecast.weather.gov/product.php?site={site}&product=CLI&issuedby={issuedby}&format=txt` | Kalshi NHIGH settlement — observed daily high per verified station | none (free) |
| **NWS Forecast API** | `api.weather.gov/points/{lat},{lon}` → `/gridpoints/{id}/{x},{y}/forecast` | Kalshi NHIGH model high when CLI not yet issued | none (free) |

**Odds cache:** in-process, TTL 120s, keyed by `(sport_key, markets, regions)`
(legacy `sport_key`-only key kept warm for back-compat).

**Sport key map:** `nba→basketball_nba`, `wnba→basketball_wnba`,
`ncaab→basketball_ncaab`, `mlb→baseball_mlb`, `nfl→americanfootball_nfl`,
`ncaaf→americanfootball_ncaaf`, `nhl→icehockey_nhl`.

---

## 9. Persistence — 7 LLP Pro tables (auto-create on startup)

`odds_snapshots`, `team_context`, `lineup_status`, `injury_status`,
`model_outputs`, `clv_log`, `bet_decisions`. Postgres via `DATABASE_URL`. All
writes are best-effort — a persistence failure never breaks analysis.

---

## 10. 10-step plan status

| Step | Description | Status |
|------|-------------|--------|
| 1 | Canonical game-context object | Built internally in analyze path |
| 2 | Line-tracking tables (`odds_snapshots` + first-seen helper) | **LIVE** |
| 3 | Odds-snapshot background cron (first_seen / current / lock / close + CLV) | **LIVE** (in-process daemon; see §11) |
| 4 | Starter/lineup + injury verification (MLB Stats + ESPN fetchers) | **LIVE** |
| 5 | F5 MLB markets | **LIVE, deployed** |
| 6 | OpenAI structured reconciliation | **PENDING** |
| 7 | Both-sides expansion + record adapter | **LIVE** |
| 8 | Hard approval gates (`canApproveLLPBet`) | Ceiling enforced; thin wrapper PENDING |
| 9 | Final-card output rules | Orchestrator layer, not in this backend |
| 10 | OpenAI web-search fallback | **PENDING** |

---

## 11. Odds-snapshot cron — Step 3, LIVE

In-process daemon thread (no external scheduler), started at import so it runs
under both `python app.py` (dev) and gunicorn (prod). Polls The Odds API for
every sport in the sport map each interval and persists line snapshots.

**Snapshot kinds** (column `odds_snapshots.snapshot_kind`):

| kind | when written | cardinality |
|------|--------------|-------------|
| `first_seen` | first observation of a (game, market, side) today | once/day |
| `current` | every tick **only if** american odds OR point changed vs the last row (change-detection bounds row growth) | many |
| `lock_line` | inside the lock window `[commence − LOCK_WINDOW_MIN, commence)` | once |
| `close_line` | first tick at/after `commence_time`; also triggers a CLV write | once |

**Per-side selection:** best (most favourable) American price across all books
is recorded each time, with the originating `book`.

**Snapshot field contract** (every cron row in `odds_snapshots` carries the full
verified-data record): `source` (provider, `the-odds-api`), `book`, `sport`
(API sport_key, e.g. `basketball_nba`), `league` (title, e.g. `NBA`), `event_id`
(provider event id), `game_key` + `away_team`/`home_team` (event), `player`
(NULL for team markets), `market`, `side`, `point` (line), `american_odds`
(price), `fetched_at` (timestamp), `snapshot_kind` (stage). Outcomes with no
price are skipped (no-price ⇒ no row). Analyze-path persisted rows may leave
`league`/`event_id`/`player` NULL; cron rows always populate them.

**Approved Market-Verified sources** stay OpticOdds / OddsJam / Unabated /
SportsDataIO. TheRundown is board/current-line support only; a current-only feed
yields **prospective** CLV only — the cron cannot reconstruct CLV from before it
started.

**CLV:** on `close_line`, opens vs close are graded via the today's earliest
snapshot anchor and a row is written to `clv_log` (`opening_line`,
`closing_line`, `clv_delta` in implied-prob points, `clv_grade` ∈
{STRONG≥0.03, MEDIUM≥0.015, SMALL≥0.005, FLAT}, `clv_beat` = line moved toward
the side). `bet_line` is NULL — the cron tracks **market movement**, not a placed
bet (per-bet CLV stays in the analyze path via `opening_lines`).

**CLV failure handling (never fabricated):** if a `close_line` is captured but
there is no opening anchor (feed started after the market opened), the cron
writes an explicit `clv_grade='INCOMPLETE'` row with `opening_line`, `clv_delta`
and `clv_beat` all NULL — incompleteness is recorded, never back-filled. No
timestamp ⇒ no CLV; no price ⇒ Market-Sanity-only (Edge/Kelly N/A) in the
analyze path; no book/source ⇒ not Market-Verified.

**Env flags:**
- `LLP_SNAPSHOT_INTERVAL_SEC` (default 300)
- `LLP_LOCK_WINDOW_MIN` (default 15)
- `LLP_DISABLE_SNAPSHOT_CRON=1` to turn the cron off entirely

**Observability:** `GET /llp/snapshot-cron/status` →
`{enabled, started, interval_sec, lock_window_min, stats:{ticks, rows_written,
clv_rows, last_tick, last_error, last_sports}}`.

**API cost:** the cron reuses `_llp_fetch_odds`, so polls within the 120s odds
cache TTL are free; net new spend is one refresh per sport per interval.

---

## Game Winner Payout Discipline (WOW v16 Clean Core)

Applies **only** to the full-game **Game Winner** lane — exact `market == "h2h"`
(MLB `h2h_1st_5_innings` / F5 is excluded; spreads/totals untouched). Backend
scope only; dollar bankroll, the $2 floor when bankroll < $25, the $ stake/net
figures, and post-game "Q3 Lucky / False-Signal" labeling stay in the
**orchestrator** (this engine's `kelly_stake` is a *fraction* 0–1, no dollar
concept).

`record["decimal_odds"]` is now stored unconditionally from the chosen side's
American price. Discipline by decimal price on that side:

- **price < 1.35x → hard REJECT.** Emits `game-winner-below-min-payout`; badge
  floored to **PASS** by the spec ceiling and `final_decision` forced to `PASS`
  (dropped). Example: MIA 1.17x → REJECT.
- **1.35x ≤ price < 1.50x → approvable ONLY if ALL hold:** no-vig edge ≥ +3%
  (`edge ≥ 0.03`) AND `starter_status == "confirmed"` AND
  `lineup_status == "confirmed"` AND `model_adjustments` non-empty (Layer 4
  model synthesis) AND `kelly_stake > 0`. Otherwise emits
  `game-winner-short-price-unverified` → badge capped at **CANDIDATE**; an
  actionable `BET`/`SMALL BET` is demoted to `WATCH` (kept on the board for
  review, never in `winners_ranked` / `best_bets`).
- **price ≥ 1.50x →** clears the discipline (normal grading applies).
- **`record["inverted_stake_sizing"]`** = `True` when decimal price < 2.0x on
  the Game Winner lane (stake risked exceeds potential win). Surfaced for the
  **orchestrator** to apply its dollar stake-sizing rules; this engine does not
  size dollars.

Both new tags are **HARD** (`_LLP_PRO_FAILURE_TAGS`, count toward the ≥3
cardinality gate). The short-price tag is in `_LLP_PRO_CANDIDATE_CEILING_TAGS`.

---

## 12. Kalshi Daily High Temperature Weather Lane

**Patches shipped:** WOW-PATCH-001 (2026-06-30), WOW-PATCH-002 + WOW-PATCH-003 (2026-07-01)

Evaluates Kalshi NHIGH bracket markets for 5 verified cities.

### Station mapping (hardcoded — verified from live Kalshi contract rule text)

| City key | Kalshi series | Settlement station | NWS code | Timezone |
|----------|---------------|--------------------|----------|----------|
| `NYC` | KXHIGHNY | Central Park, New York | KNYC | America/New_York |
| `LA` | KXHIGHLAX | Los Angeles Airport, CA | KLAX | America/Los_Angeles |
| `MIA` | KXHIGHMIA | Miami International Airport | KMIA | America/New_York |
| `CHI` | KXHIGHCHI | Chicago Midway, IL | **KMDW** | America/Chicago |
| `AUS` | KXHIGHAUS | Austin Bergstrom, TX | KAUS | America/Chicago |

**Hard regression bans:** Chicago = KMDW (NOT KORD). Miami = KMIA (NOT KPBI). LA = KLAX (NOT KBUR).

### WEATHER_* internal labels (upstream only — never appear as terminal_label)

| Label | When set | Terminal resolution |
|-------|---------|---------------------|
| `WEATHER_MODEL_READY` | CLI issued for requested date (binary) OR horizon≤24h AND sigma_f<4.5 (Gaussian) | `KALSHI_PLAYABLE_LIMIT_ONLY` or `KALSHI_WATCH` (PATCH-003 gate applies) |
| `WEATHER_WATCH` | horizon 24–48h OR sigma_f≥4.5 | `KALSHI_WATCH` |
| `WEATHER_SCOUT` | horizon>48h OR no forecast data | `KALSHI_WATCH` |
| `WEATHER_REJECT_DATA` | NWS fetch failed entirely | `KALSHI_DATA_UNOBTAINABLE` |
| `WEATHER_REJECT_SETTLEMENT` | Bracket yes_prices sum outside ±0.05 of 1.00 | `KALSHI_REJECT_BAD_RULES` |
| `WEATHER_REJECT_UNCALIBRATED` | Model not calibrated for scenario | `KALSHI_REJECT_UNCALIBRATED` |

### Terminal label set (complete, WOW-PATCH-003)

`KALSHI_PLAYABLE_LIMIT_ONLY` · `KALSHI_WATCH` · `KALSHI_REJECT_NO_EDGE` ·
`KALSHI_REJECT_BAD_RULES` · `KALSHI_REJECT_THIN_BOOK` · `KALSHI_REJECT_FEE_DRAG` ·
`KALSHI_REJECT_UNCALIBRATED` · `KALSHI_DATA_UNOBTAINABLE`

### Bracket scoring (WOW-PATCH-002: Gaussian)

**Pre-settlement (CLI not yet issued for requested date):** Gaussian CDF probabilities.

```
closed [lo, hi]:  P = Φ((hi − μ) / σ) − Φ((lo − μ) / σ)
open-low  (≤ hi): P = Φ((hi − μ) / σ)
open-high (≥ lo): P = 1 − Φ((lo − μ) / σ)
```

μ = NWS gridpoint forecast high. σ_f default = 3.5°F (user-overridable via `sigma_f` field).
Full bracket set normalized so `model_prob_sum == 1.00`.

**Post-settlement (CLI issued for requested date):** Binary scorer (1.0 / 0.0).
PATCH-003 gate blocks `KALSHI_PLAYABLE_LIMIT_ONLY` on FINAL data without live orderbook.

**Date-mismatch guard:** NWS API returns the latest CLI regardless of the requested date.
The endpoint checks `cli_issuance_time[:10] == date_str`; mismatched CLI is discarded
and `report_status` is set to `NOT_YET_ISSUED`, falling through to the Gaussian path.

### Price-source and staleness gate (WOW-PATCH-003)

`KALSHI_PLAYABLE_LIMIT_ONLY` requires ALL of:
1. `weather_label == WEATHER_MODEL_READY`
2. `price_source == kalshi_live_orderbook`
3. `price_age_minutes ≤ 10`
4. `market_status == open`
5. `edge ≥ 0.10` on at least one bracket
6. DRY_RUN_ONLY never disabled (can_execute is always false)

| price_source | can_trade | can_execute | Max terminal_label |
|---|---|---|---|
| `kalshi_live_orderbook` (fresh+open) | false | false | KALSHI_PLAYABLE_LIMIT_ONLY (if gate passes) |
| `operator_supplied` | false | false | KALSHI_WATCH |
| `synthetic_test` | false | false | KALSHI_WATCH |
| `not_found` | false | false | KALSHI_DATA_UNOBTAINABLE |

**DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS** is enforced unconditionally.

### Endpoints

- `GET /wow/kalshi/weather/stations` — no auth; health-check; returns station mapping table
- `POST /wow/kalshi/weather/evaluate` — auth required

Request body:
```json
{
  "city":         "CHI",
  "date":         "YYYY-MM-DD",
  "brackets":     [{"label": "≤79", "yes_price": 0.02}, ...],
  "sigma_f":      3.5,
  "price_source": "synthetic_test"
}
```

Key response fields: `scoring_mode`, `sigma_f`, `forecast_horizon_hours`, `weather_label`,
`terminal_label`, `model_prob_sum`, `price_source`, `price_timestamp`, `market_status`,
`live_orderbook_checked`, `price_age_minutes`, `can_trade`, `can_execute`, `execution_rule`,
`trade_block_reason`, `cli_product_id`, `cli_issuance_time`.

### Settlement rules

- **Source:** NWS Climatological Report (CLI product) ONLY. AccuWeather, Google Weather, Apple Weather do NOT determine settlement.
- **Revision risk:** PRELIMINARY CLI may be revised. Revisions before contract expiration count; after expiration do not.
- **LST/DST:** NWS CLI uses Local Standard Time windows even during DST.

### No changes to core LLP engine

This lane does not touch `_llp_decision`, badge ceilings (§4), the §7 field contract, failure-path tags (§6), or the odds-snapshot cron (§11).

---

## 13. Gate 3 Proportional-Edge Classifier (WOW-PATCH-2026-07-01-GATE3)

**Shipped:** 2026-07-01. Replaces manual agent-side Gate 3 reasoning.

### Endpoint

`POST /wow/l10/gate3` (auth required)

All agents **must call this endpoint** instead of computing Gate 3 manually.
Agent-side threshold reasoning causes drift — this backend is the single source of truth.

### Core rule changes vs old absolute-gap model

| Old rule | New rule |
|----------|----------|
| `abs(avg − line) ≤ 1.5 AND hit_rate < 0.65` → KILL | `gap_pct < 0.08 AND hit_rate < 0.55` → REJECT_COINFLIP |
| 65% L5 floor (kills 3/5) | 55% marginal floor; 55–64% = DISCOVERY_ONLY band |
| Outlier > 2× median → delete game | Outlier flagged + Winsorized; raw ledger stays visible |

### Gap zones (proportional, not absolute)

| gap_pct | Zone | Minimum outcome |
|---------|------|-----------------|
| ≤ 0% | Negative | REJECT_TRUE_NO_EDGE |
| 0–8% | Coin-flip | REJECT_COINFLIP if hit_rate < 0.55 |
| 8–15% | Elevated | WATCH_ELEVATED if hit_rate ≥ 0.55 |
| ≥ 15% | Strong | GATE3_PASS if hit_rate ≥ 0.65 |

### Hit-rate bands (QA Edit 1)

| Band | Range | Ceiling | Power eligible |
|------|-------|---------|----------------|
| `QUALIFICATION_ELIGIBLE_65_PLUS` | ≥ 65% | MODEL_QUALIFIED_HOLD | No (Gate 3 never approves) |
| `DISCOVERY_ONLY_55_64` | 55–64% | **WATCH_ELEVATED max** | **Never** |
| `BELOW_FLOOR` | < 55% | REJECT_COINFLIP or lower | Never |

`DISCOVERY_ONLY_55_64` restrictions: never Power Play eligible; cannot become
MONEY_QUALIFIED from L5 data alone; requires full L10 + market comp + role
confirmation before any upgrade above WATCH_ELEVATED.

### Winsorization — winsor_cap_v1 (QA Edit 2 — deterministic)

| N | Method | Cap |
|---|--------|-----|
| N ≥ 10 | `L10_P90_MEDIAN_CAP_V1` | `min(2×median, p90_anchor)` where `p90 = 9th-highest value`; if `p90 ≤ median` use `2×median` |
| 5 ≤ N < 10 | `MEDIAN_CAP_LOW_SAMPLE` | `2×median` |
| N < 5 | `NONE_N_LT_5` | No cap — DATA_BUILD_PRIORITY/WATCH ceiling |

Market cap multipliers: `points/pra/pts_asts/pts_rebs/assists/rebounds/blocks/steals/threes` = 2.0×.
Strikeouts and pitcher fantasy score: no median-based Winsorization.

Outlier ≠ delete. Raw average, median, and Winsorized average are **all** returned.
If Winsorized average flips below line (`outlier_flip: true`) → `REJECT_OUTLIER_CONTAMINATED`.

### Gate 3 labels

`GATE3_PASS` · `WATCH_ELEVATED` · `WATCH` · `DATA_BUILD_PRIORITY` ·
`REJECT_TRUE_NO_EDGE` · `REJECT_COINFLIP` · `REJECT_RECENCY_REGRESSION` ·
`REJECT_OUTLIER_CONTAMINATED` · `REJECT_DATA_QUALITY`

### Shadow-mode logging

Every call writes to `gate3_shadow_log`. Settlement fields (`final_result`,
`closing_line`, `clv_delta`) filled in post-game via UPDATE. New thresholds
cannot produce FINAL_APPROVED until calibrated against settled outcomes.

### Approval safety (hard)

This endpoint never produces `FINAL_APPROVED` or `MONEY_QUALIFIED`. It feeds
the discovery/candidate layer only. Full approval still requires: LLP badge
`ANCHOR`/`BET`, positive edge, positive Kelly, verified market cause, confirmed
starter/lineup, no failures, and a passing final-lock refresh.

---

## §14 — WNBA Ingestion Scheduler (WOW-PATCH-011)

### Tables (all created on first run via `_ensure_wnba_schema`)

| Table | PK | Key unique constraint |
|-------|----|-----------------------|
| `wnba_schedule` | `game_id` | PRIMARY KEY |
| `wnba_player_game_logs` | `id` (bigserial) | `UNIQUE (player_id, game_id)` |
| `wnba_box_scores` | `id` (bigserial) | `UNIQUE (game_id, team)` |
| `wnba_injury_status` | `id` (bigserial) | none (inserts new row each run) |
| `wnba_transactions` | `id` (bigserial) | `UNIQUE (transaction_id)` |
| `source_audit_log` | `id` (bigserial) | `UNIQUE (run_id)` |

### Player game log fields

`player_id, player_name, team, opponent, game_date, game_id, starter,
minutes, points, rebounds, assists, steals, blocks, turnovers,
field_goal_attempts, three_point_attempts, free_throw_attempts,
personal_fouls, plus_minus, source, source_timestamp, ingestion_ts,
missing_fields[]`

### Data source

ESPN public WNBA APIs (no auth required, no API key spend):
- Scoreboard: `site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard`
- Boxscore: `site.api.espn.com/apis/site/v2/sports/basketball/wnba/summary`
- Injuries: `site.api.espn.com/apis/site/v2/sports/basketball/wnba/injuries`
- Transactions: `site.api.espn.com/apis/site/v2/sports/basketball/wnba/transactions`

### Cron design

In-process daemon thread (`wnba-ingest-cron`). Fires once per ET calendar day.
Advisory lock key: `778597211` (distinct from LLP cron `778597203`).
Disable: `WNBA_DISABLE_CRON=1`.

### Staleness flag

`source_is_stale = True` when `source_timestamp` is older than 25 hours.
Exposed on every `/wow/wnba/player-log` row and `/wow/wnba/ingestion/health`.

### Audit log behavior

Every ingestion run writes a `source_audit_log` row immediately (status=RUNNING)
and updates it on finish. No player row is written without a corresponding
audit entry. `missing_fields[]` array tracks which core stat fields were absent.

### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/wow/wnba/ingestion/health` | none | Cron status, DB reachability, last run |
| POST | `/wow/wnba/ingestion/refresh` | none | Manual full ingestion cycle |
| GET | `/wow/wnba/player-log` | none | Query player logs with staleness flags |
| GET | `/wow/wnba/schedule` | none | Schedule for a date; falls back to live ESPN |
| GET | `/wow/wnba/source-audit` | none | source_audit_log + optional row detail |

### Safety rails

- No betting approval labels created or modified
- Gate 3 classification logic unchanged
- `execution_rule: DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS` on all responses

---

## §15 — Market Data Contract Registry (WOW-PATCH-010)

### Endpoint

| Method | Path | Description |
|--------|------|-------------|
| POST | `/wow/data-contract/check` | Check a row against a sport+market contract |
| GET  | `/wow/data-contract/registry` | List all contracts and required fields |

### Request body for `check`

```json
{
  "sport":       "wnba",
  "market":      "PTS",
  "window":      "L5",
  "data":        { ... },
  "player_id":   "...",
  "player_name": "...",
  "date_from":   "YYYY-MM-DD",
  "date_to":     "YYYY-MM-DD"
}
```

`data` (inline) takes precedence for field checks. `player_id`/`player_name` drives DB lookup for row_count (window sufficiency).

### Contract status labels

| Status | Trigger | approval_ceiling | data_confidence |
|--------|---------|-----------------|-----------------|
| `DATA_CONTRACT_COMPLETE` | All core fields present + not stale | `GATE_3_ELIGIBLE` | `HIGH` |
| `DATA_CONTRACT_STALE` | All core present, source_timestamp > 25h | `WATCH` | `PARTIAL_STALE` |
| `DATA_CONTRACT_PARTIAL` | Advisory missing OR window gap | `CONDITIONAL` | `MEDIUM` |
| `DATA_BUILD_PRIORITY` | Only metadata missing (stat fields present) | `NO_APPROVAL` | `MEDIUM` |
| `DATA_CONTRACT_INCOMPLETE` | Core stat or minutes missing | `NO_APPROVAL` | `DATA_CONTRACT_INCOMPLETE` |

### Logical field aliases

- `player_id_or_name` → at least one of `player_id`, `player_name` is non-null
- `starter_or_role` → `starter` (bool) or `role` (text) is non-null
- `ingestion_ts` → `ingestion_ts` or `ingestion_timestamp`

### WNBA market contracts (v1.0.0)

Base core fields (all markets): `player_id_or_name, team, opponent, game_date, minutes, starter_or_role, source_timestamp, ingestion_ts`

| Market | Additional core | Advisory |
|--------|----------------|----------|
| PTS | `points` | — |
| REB | `rebounds` | — |
| AST | `assists` | `teammate_availability` |
| PRA | `points, rebounds, assists` | — |
| P+A | `points, assists` | — |
| P+R | `points, rebounds` | — |
| R+A | `rebounds, assists` | — |

### Window sufficiency

`window: "L5"` → requires `row_count >= 5`; `window: "L10"` → requires `row_count >= 10`. Row count from DB lookup only (not from inline data). Insufficient window → `DATA_CONTRACT_PARTIAL` + `INSUFFICIENT_{WINDOW}_ROWS:{n}/{min}` blocker.

### Safety rails

- No betting approval labels; `GATE_3_ELIGIBLE` is the highest ceiling (contract layer only)
- Gate 3 math unchanged
- `execution_rule: DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS` on all responses
- `contract_version: v1.0.0` on every response

---

---

## §16 — Confidence Envelope (WOW-PATCH-009)

### Endpoint

`POST /wow/confidence-envelope`

### Purpose

Accepts Gate 3 + PATCH-010 outputs and returns four **independent** confidence axes. Each axis answers a different question; no single label can mask whether the bottleneck is signal, data, market, or approval structure.

### Request body

```json
{
  "sport": "wnba",
  "market": "PTS",

  // Gate 3 inputs (any of):
  "gate3_result": { "label": "MODEL_QUALIFIED_HOLD", "edge_pct": 8.5 },
  "gate3_label": "MODEL_QUALIFIED_HOLD",
  "gate3_edge_pct": 8.5,
  "gate3_hit_rate_band": "HIGH_70_100",

  // Data contract inputs (any of):
  "data_contract_result": { "contract_status": "DATA_CONTRACT_COMPLETE", ... },
  "data_contract_status": "DATA_CONTRACT_COMPLETE",
  "data_stale_fields": [],
  "data_row_count": 8,
  "data_window": "L5",
  "data_window_sufficient": true,
  "advisory_codes": ["TEAMMATE_AVAILABILITY_MISSING"],

  // Market inputs (all optional):
  "market_odds_available": false,
  "market_edge_confirmed": null,
  "market_line_direction": "FLAT",
  "market_stale": false
}
```

`gate3_result` and `data_contract_result` objects are unpacked automatically. Individual fields take precedence.

### Four axes

| Axis | Values |
|------|--------|
| `signal_confidence` | HIGH \| MEDIUM \| LOW \| NEGATIVE \| UNKNOWN |
| `data_confidence` | COMPLETE_FRESH \| COMPLETE_STALE \| PARTIAL_FRESH \| PARTIAL_STALE \| LOW_SAMPLE \| DATA_CONTRACT_INCOMPLETE \| DATA_CONTRACT_PARTIAL \| UNKNOWN |
| `market_confidence` | MARKET_CONFIRMED \| MARKET_CONFLICT \| MARKET_UNVERIFIED \| MARKET_STALE \| NOT_REQUIRED_FOR_THIS_GATE |
| `approval_confidence` | MODEL_QUALIFIED_HOLD \| WATCH_ELEVATED \| WATCH \| REJECT \| NO_APPROVAL (+ orchestrator-only: FINAL_APPROVED, MONEY_QUALIFIED, MARKET_VERIFIED_HOLD) |

### Approval confidence derivation (abbreviated)

| Signal | Data | Market | advisory_codes | approval_confidence |
|--------|------|--------|----------------|---------------------|
| HIGH | COMPLETE_FRESH | confirmed/unverified/not required | empty | MODEL_QUALIFIED_HOLD |
| HIGH | COMPLETE_FRESH | confirmed/unverified/not required | present | WATCH_ELEVATED |
| HIGH | COMPLETE_STALE or PARTIAL_FRESH | any | any | WATCH_ELEVATED |
| HIGH | LOW_SAMPLE / DATA_CONTRACT_PARTIAL | any | any | WATCH |
| HIGH | any | MARKET_CONFLICT | any | WATCH |
| MEDIUM | any | any | any | WATCH |
| LOW | COMPLETE_FRESH | any | any | WATCH |
| LOW | other | any | any | NO_APPROVAL |
| NEGATIVE | any | any | any | REJECT |
| UNKNOWN | any | any | any | NO_APPROVAL |
| any | DATA_CONTRACT_INCOMPLETE | any | any | NO_APPROVAL |

### Ceiling rule

PATCH-009 max emittable = `MODEL_QUALIFIED_HOLD`. `FINAL_APPROVED`, `MONEY_QUALIFIED`, `MARKET_VERIFIED_HOLD` are reserved for multi-gate orchestrator; PATCH-009 never emits them.

### Signal confidence map (Gate 3 → axis)

```
MODEL_QUALIFIED_HOLD → HIGH
MARKET_VERIFIED_HOLD → HIGH
MONEY_QUALIFIED      → HIGH
FINAL_APPROVED       → HIGH
WATCH_ELEVATED       → MEDIUM
WATCH                → MEDIUM
DISCOVERY_ONLY       → LOW
DISCOVERY_ONLY_55_64 → LOW
NO_LABEL             → UNKNOWN
REJECT               → NEGATIVE
```

### Safety rails

- Gate 3 math unchanged
- No betting approvals created
- `execution_rule: DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS` on every response
- `envelope_version: v1.0.0`

---

## PATCH-010 amendment (CLOSED)

`approval_ceiling` enum corrected — `CONDITIONAL` removed from all outputs:

| Scenario | Old (removed) | New |
|----------|--------------|-----|
| Advisory fields missing | CONDITIONAL | GATE_3_ELIGIBLE_WITH_ADVISORY |
| Window row count insufficient | CONDITIONAL | WATCH |

`advisory_code` field added: pipe-delimited string of `{FIELD}_MISSING` codes (e.g. `TEAMMATE_AVAILABILITY_MISSING`) or `NONE`.

---

---

## §17 PATCH-013A — Role-State Ledgers (SHIPPED 2026-07-02)

Endpoint: `POST /wow/role-state/build`, `GET /wow/role-state/player`, `POST /wow/role-state/evaluate`

11 sub-ledgers per player/market:
`starter_l10`, `bench_l10`, `full_role_l10`, `reduced_role_l10`, `minutes_qualified_l10`, `post_trade_l10`, `pre_trade_l10`, `post_injury_l10`, `with_key_teammate_l10`, `without_key_teammate_l10`, `all_games_l10`

Role-change detection:
- If ideal split (starter vs bench) has < 5 rows → `ROLE_CHANGE_DETECTED`, ceiling raised to `WATCH_ELEVATED`
- `recommended_ledger_name` = ideal split if ≥5 rows, else `all_games_l10`
- Advisory codes: `KEY_TEAMMATE_CONTEXT_UNAVAILABLE`, `ROLE_CHANGE_INSUFFICIENT_SAMPLE`, `ROLE_STATE_INSUFFICIENT_ROWS`
- `approval_ceiling_override: WATCH_ELEVATED` when role-change + insufficient split rows

### Safety rails
- No approval labels created
- `execution_rule: DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS` on every response
- Version: `v1.0.0`

---

## §18 PATCH-013B — Pick Lifecycle State Machine (SHIPPED 2026-07-02)

DB tables: `wow_pick_lifecycle` (PK: `pick_id`), `wow_pick_lifecycle_log` (append-only audit)

Valid states:
`DISCOVERED`, `DATA_CONTRACT_CHECKED`, `ROLE_STATE_CHECKED`, `GATE3_CHECKED`, `MARKET_CHECKED`, `CONFIDENCE_ENVELOPED`, `TRIAGED`, `MODEL_QUALIFIED_HOLD`, `MARKET_VERIFIED_HOLD`, `MONEY_QUALIFIED`, `LOCK_PENDING`, `LOCKED_DRY_RUN`, `SETTLED_WIN`, `SETTLED_LOSS`, `SETTLED_PUSH`, `REJECTED`, `WATCH`, `WATCH_ELEVATED`, `DATA_BUILD_PRIORITY`

Terminal states: `SETTLED_WIN`, `SETTLED_LOSS`, `SETTLED_PUSH` — no further transitions allowed.

Endpoints:
- `POST /wow/pick-lifecycle/create` — create in DISCOVERED state
- `POST /wow/pick-lifecycle/transition` — move to new state with reason + optional snapshot update
- `GET /wow/pick-lifecycle/list?state=&sport=&limit=` — list all picks, no hidden cuts
- `POST /wow/pick-lifecycle/settle` — convenience wrapper for WIN/LOSS/PUSH
- `GET /wow/pick-lifecycle/pick?pick_id=` — full pick + transition_history

### Safety rails
- `can_execute` field: always `false`, enforced in DB schema (`DEFAULT FALSE`) and every endpoint
- No live execution fields present anywhere in the schema
- Every row persists (no deletes)
- Version: `v1.0.0`

---

## §19 PATCH-012 — Candidate Triage Score (SHIPPED 2026-07-02)

Endpoint: `POST /wow/candidate-triage/score`

Scoring weights (max 100):

| Component | Max pts | Key inputs |
|-----------|---------|-----------|
| Market mispricing | 20 | `has_mispricing`, `market_edge_confirmed`, `market_confidence` |
| Proportional gap | 15 | `gate3_edge_pct` |
| Data freshness | 15 | `data_confidence` axis from CE |
| Data completeness | 15 | `data_contract_status` |
| Hit-rate support | 10 | `gate3_label` or explicit hit-rate fields |
| Median support | 10 | `gate3_l5_avg` / `gate3_l10_avg` vs `line` |
| Role stability | 10 | `role_state_flags`, `role_ceiling_override` |
| Failure-path cleanliness | 5 | `failure_path_flags` |

Score bands:
- 80–100: `PRIORITY_BUILD`
- 65–79: `WATCH_ELEVATED`
- 50–64: `WATCH`
- 25–49: `SCOUT`
- 0–24: `REJECT`

Hard caps:
- `DATA_CONTRACT_INCOMPLETE` → caps raw score at 45
- `gate3_label=REJECT` → caps at 30
- `MARKET_CONFLICT` → subtracts 10

Invariants:
- `approval_confidence_unchanged`: echoes the CE output unchanged — triage never alters it
- `discovery_priority` = `score_band` (discovery label, not approval label)
- `execution_rule: DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS`
- Version: `v1.0.0`

---

## §20 PATCH-014 — Unified Model Run Orchestrator (SHIPPED 2026-07-02)

Endpoint: `POST /wow/model-run/orchestrate`

Pipeline order (inline, no HTTP self-calls):
1. Role-state evaluation (if `role_state_rows` provided)
2. Confidence Envelope (inline `_mro_run_ce_inline`)
3. Triage score (inline `_mro_run_triage_inline`)
4. Lifecycle create/upsert (if `persist_lifecycle=true`, default true)

Input:
- `run_id` (generated if absent), `slate_date`, `sport`, `persist_lifecycle`
- `picks[]` — each pick carries its own stage inputs (gate3/dc/role/market results)
- Accepts pre-computed results for every stage; computes CE + triage inline always

Output per pick:
- `confidence_envelope`, `triage`, `lifecycle_state`, `role_state_flags`, `role_ceiling_override`
- `can_execute: false` (always)
- `execution_rule: DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS`

`stage_counts`: `{total, ce_computed, triage_computed, lifecycle_created, lifecycle_errored}`

### Invariants
- Every pick in → every pick out (no hidden cuts)
- `persist_lifecycle=false` skips all DB writes
- `can_execute` is `false` on every pick and at top level
- Version: `v1.0.0`

---

---

## §21 PATCH-IMPORTED-LEDGER-PROVENANCE-AND-STATUS-ESCALATION-GATE (SHIPPED 2026-07-02)

Endpoints: `POST /wow/provenance/validate`, `POST /wow/provenance/validate-batch`

**Purpose:** Pre-score gate — prevents imported/pasted/AI-generated/screenshot-derived rows from reaching any playable label (`MODEL_QUALIFIED_HOLD`, `MONEY_QUALIFIED`, `FINAL_APPROVED`, `KALSHI_PLAYABLE`, `LLP_PLAYABLE`, `LLP_APPROVED`, `LOCK`, `LOCKED_DRY_RUN`) unless the row is source-stamped and row-level verified.

**Trusted origins** (bypass full field contract): `model_reconstructed`, `verified_api_reconstructed`

**Required fields** for unverified imported rows (21 fields + source_url OR source_path):
`player`, `team`, `opponent`, `game_date`, `market`, `line`, `side`, `source_url/source_path`, `source_timestamp`, `exact_query`, `l5_rows`, `l10_rows`, `l5_avg`, `l10_avg`, `l5_median`, `l10_median`, `l5_hit_count_at_line`, `l10_hit_count_at_line`, `player_status_timestamp`, `market_timestamp`, `final_lock_timestamp`

**Checks (in order):**

1. **Required-field contract** — any missing field → `SOURCE_LEDGER_PROVENANCE_FAIL`, ceiling `DATA_CONTRACT_FAIL`
2. **Summary-only detection** — `l5_rows` or `l10_rows` absent/empty → `SUMMARY_ONLY_NOT_MODEL_VERIFIED`, ceiling `WATCH`
3. **Pasted claim contradictions** — compares `pasted_claim_*` vs reconstructed values:
   - avg diff ≥10% → `L10_AVG_DIFFERS_10PCT`
   - median diff ≥0.5 → `L10_MEDIAN_DIFFERS_0_5`
   - L5/L10 hit count diff ≥1 → `*_HIT_COUNT_DIFFERS_1_GAME`
   - side flip → `SIDE_DIRECTION_FLIPPED` + `REJECT_DATA_QUALITY` ceiling
   - injury context omitted → `INJURY_CONTEXT_OMITTED`
   - Any contradiction → `PASTED_LEDGER_CONTRADICTION`
4. **MLB pitcher status escalation** — scans `pitcher_notes`/`injury_notes`/`status_notes` for: `tommy john`, `ucl repair`, `elbow comebacker`, `shoulder issue`, `forearm tightness`, `hand/wrist issue`, `velocity drop`, `early exit`, `skipped bullpen`, `pending bullpen`, `pitch-count restriction`, `next-start uncertainty`, `workload uncertainty`. Missing `next_start_confirmed` + `bullpen_cleared` + `role_confirmed` → `STATUS_GATE_REQUIRED`, ceiling `WATCH`. Late/pending flags → `STATUS_GATE_LATE`
5. **REASONED_NOT_MODELED** — vague phrases in `reasoning_notes`/`upgrade_reason`/`scoring_rationale` when required fields also missing: `directionally consistent`, `plausible`, `starter recheck`, `role should be stable`, `average is close`, `seems supported` → `REASONED_NOT_MODELED`, ceiling `WATCH`
6. **Playable label gate** — if `proposed_label` is a playable label and any blockers are active → `PLAYABLE_LABEL_BLOCKED_BY_PROVENANCE`, proposed label forced to `ceiling`

**New blocker/status tags** (not playable labels):
`IMPORTED_LEDGER_UNVERIFIED`, `SOURCE_LEDGER_PROVENANCE_FAIL`, `SUMMARY_ONLY_NOT_MODEL_VERIFIED`, `PASTED_LEDGER_CONTRADICTION`, `STATUS_GATE_LATE`, `STATUS_GATE_REQUIRED`, `REASONED_NOT_MODELED`

**Output fields added:**
`input_origin`, `source_provenance_status`, `ledger_rows_verified`, `summary_only_flag`, `pasted_claim_verified`, `pasted_claim_delta`, `injury_status_escalation`, `role_status_escalation`, `market_timestamp`, `final_lock_timestamp`, `terminal_failure_tag[]`, `approval_blockers[]`

**`ceiling` values:** `ELIGIBLE_FOR_SCORING` (clean pass), `WATCH` (advisory block), `DATA_CONTRACT_FAIL` (hard block), `REJECT_DATA_QUALITY` (side-flip contradiction)

### Safety rails
- `can_execute` always `false`
- Every row preserved in batch output — no hidden cuts
- `execution_rule: DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS` on every response
- Version: `v1.0.0`

---

## §22 PATCH-VALIDATION-QUEUE-CACHE (SHIPPED 2026-07-02)

Endpoints: `POST /wow/validation-queue/build`, `POST /wow/ledger-cache/upsert`, `GET /wow/ledger-cache/lookup`

**Purpose:** Amends Section 29.2 (Run-Level Output). A correct NO PLAY run must still be useful — every touched candidate is bucketed into **Approved Board / Validation Queue / Rejected Board** with a machine-readable `confidence_debt` object, missing fields, active blockers, cache status, and a specific `upgrade_path`. Validation Queue is a **display bucket**, not a new playable label — it never grants approval.

**Display bucket rule** (`/wow/validation-queue/build`):
- `terminal_label` in `REJECT_BAD_STRUCTURE` / `REJECT_NO_EDGE` / `REJECT_DATA_QUALITY` → **Rejected Board**
- `terminal_label` in `FINAL_APPROVED` / `MONEY_QUALIFIED` / `LLP_APPROVED` / `LLP_PLAYABLE` / `KALSHI_PLAYABLE` **and zero confidence debt** → **Approved Board**
- Same playable label **with any confidence debt** → forced down to **Validation Queue**, `effective_label` becomes `WATCH` (never leaks a playable label)
- Everything else (`WATCH`, `MODEL_QUALIFIED_HOLD`, `MARKET_VERIFIED_HOLD`, `DATA_CONTRACT_FAIL`, `SOURCE_CONFLICT`, imported-ledger blocker states) → **Validation Queue**

**`confidence_debt` fields** (any `true` blocks approval): `ledger_debt`, `median_debt`, `hit_count_debt`, `status_debt`, `role_debt`, `market_debt`, `payout_debt`, `failure_path_debt`, `source_provenance_debt`, `cache_debt`

**Per-candidate output:** `candidate`, `market`, `line`, `side`, `terminal_label`, `display_bucket`, `effective_label`, `confidence_debt`, `debt_count`, `missing_fields[]`, `active_blocker_tags[]`, `cache_status`, `upgrade_path` (specific/testable text per active debt, mapped from `_VQ_UPGRADE_PATH_MAP`), `edge_pct`, `language_violations[]`, `can_execute: false`

**Banned phrasing for Validation Queue rows** (scanned in `narrative`/`display_text`/`candidate_summary`/`commentary` fields, flagged as `language_violations`, never silently dropped): `model-approved`, `approved`, `lock`, `submit`, `playable`, `money-qualified`, `final`, `safe`, `high-confidence winner`

**Run-level output:** `approved_board[]`, `validation_queue[]` (sorted by lowest `debt_count` then highest `edge_pct`), `rejected_board[]`, `final_action` (`"NO PLAY / STAKE $0"` when `approved_board` is empty)

**Ledger cache** — `player_game_log_cache` / `pitcher_game_log_cache` tables (per-league game-by-game rows, keyed on `player_id`+`game_date`, `player_game_log_cache` also keyed on `league`). `GET /wow/ledger-cache/lookup` compares cached `game_date`/`ingestion_timestamp` against caller-supplied `latest_completed_game_date` / `status_changed_at` to return `cache_status: no_cache | stale | fresh`. Stale cache may be used for scouting only — `cache_debt=true` blocks approval until refreshed.

### Safety rails
- `can_execute` always `false` on every candidate and at top level
- Every row preserved in Approved/Validation Queue/Rejected output — no hidden cuts, no rows dropped for failing data contract
- Validation Queue rows can never surface a playable label, even if `terminal_label` originally was one
- `execution_rule: DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS` on every response
- Version: `v1.0.0`

---

## §23 PATCH-BINARY-EVENT-PURGE (SHIPPED 2026-07-02)

**Problem:** the July 2 scan surfaced 21 "model-qualified" rows where most were 0.5-line MLB hitter-hit LESS props — a *structurally* near-binary single-occurrence threshold ("did it happen at all this game"). `REJECT_COINFLIP` only fires on **weak** gap%/hit-rate stats, so a 0.5-line prop with a strong gap% and hit-rate could still sail straight through Gate 3 / the main scan classifier to `MODEL_QUALIFIED_HOLD`. No prior rule purged on the *line shape itself*.

**Fix — new structural cap, independent of stats, applied at three enforcement points:**
- `POST /wow/l10/gate3` — `line == 0.5` forces `gate3_label`/`approval_ceiling` to `WATCH` (never `GATE3_PASS`/`MODEL_QUALIFIED_HOLD`), `blocker_code: BE1_BINARY_LINE_0PT5`, response field `binary_event_cap: true`.
- `_jf_slate_purge()` (JF slip lane, `POST /wow/jf`) — props with `line == 0.5` are purged pre-scoring with `purge_reason: "auto-reject archetype: binary-event 0.5 line (single-occurrence threshold, structurally near-binary)"`.
- `classify_prop()` (main daily scan, `jobs/wow_daily_scan.py`, used by `POST /wow/daily-scan`) — `line == 0.5` is checked immediately after the injury hard-reject and ahead of every scoring tier: `wow_score >= 45` → forced to `"Watch"` (never `Model Qualified — PrizePicks`/`Final Approved`/`Market Verified`), otherwise `Reject`/`Data Insufficient` as appropriate, with a `binary_event_structural_cap` blocker note.

**Scope:** sport-agnostic on the numeric line value (`line == 0.5`), not a per-sport archetype allowlist — catches the reported MLB hitter-hit case and any other sport/prop combination that offers a 0.5 threshold line.

**Safety:** downgrade-only — never raises a classification, never bypasses `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS`, and is transparent (surfaced via `blocker_code`/`purge_reason`/classification blocker text, never a silent drop).

---

## §24 PATCH-BINARY-EVENT-POSTSCAN-INVARIANT (SHIPPED 2026-07-02)

**Purpose:** close the loophole completely — guarantee no output lane can ever emit a 0.5-line row as `MODEL_QUALIFIED_HOLD`/`MARKET_VERIFIED`/`FINAL_APPROVED`/playable, even if a future code path bypasses Gate 3, the JF purge, or `classify_prop()` entirely. This is a belt-and-suspenders safety net layered on top of §23, not a replacement for it.

**1. Shared `normalize_line()` / `is_binary_event_line()` helper** (`jobs/wow_daily_scan.py`):
- All three §23 enforcement points (`wow_l10_gate3`, `_jf_slate_purge`, `classify_prop`) now call the same helper instead of duplicating `float(line) == 0.5` checks.
- `normalize_line()` extracts the first numeric token from either a plain number or a string/OCR-style row — handles `0.5`, `"0.5"`, `"0.50"`, `".5"`, `"0.5 Hits"`, `"LESS 0.5"`, `"More Than 0.5"` — so future manual/OCR-entry lanes can't slip a binary-event line through on a formatting technicality. (Existing structured-JSON entry points already hard-require a strict numeric `line` upstream, so this mainly hardens future/manual-entry paths.)

**2. Post-scan invariant in `run_scan()`** (`jobs/wow_daily_scan.py`):
- Runs once per scan, immediately after all props are classified and bucketed, before the result dict is built.
- Sweeps `market_verified`, `final_approved_internal`, `model_qualified`, and `conditional` for any card whose `line` normalizes to `0.5`.
- Any match is removed from its bucket and moved into `watch`, with `final_approval_blocker: "BE1_BINARY_LINE_0PT5"`, `binary_event_cap: true`, `can_execute: false`, and a `postscan_invariant_downgraded_from` tag; an `execution_notes` entry records the downgrade.
- Counts (`counts.model_qualified`, `counts.playable_count`, etc.) reflect the buckets **after** this sweep — the invariant cannot be silently bypassed by a stale count.

**Regression coverage:**
- `classify_prop()` with an OCR-style string line (`line="0.50"`, strong stats: score 82, 80% L10 hit rate) → confirmed `"Watch"` with `binary_event_structural_cap` blocker, not `"Model Qualified — PrizePicks"`.
- Scan-level invariant simulation: a mixed bucket (`line=0.5` + `line=1.5`) run through the same downgrade logic as `run_scan()` → confirmed the 0.5-line card is moved to `watch` with `binary_event_cap: true`, the 1.5-line card is untouched, and no bucket retains a 0.5-line row.
- Live verification: `POST /wow/l10/gate3` with strong-edge data (`gap_pct: 80%`) at `line=0.5` → `WATCH`/`BE1_BINARY_LINE_0PT5`/`binary_event_cap: true`; identical data at `line=1.5` → unaffected `GATE3_PASS`/`MODEL_QUALIFIED_HOLD`.
- Full `gate_engine/tests` suite: 329/329 pass (no regression).

**Scope note (explicitly deferred, per review):** this patch does **not** extend the hard cap to 1.5/2.5-type lines — those are volatile but not structurally binary (e.g. rebounds, assists, strikeouts at 1.5) and need a separate low-line volatility tax, not a hard cap. Tracked as a future patch, not bundled here.

---

## §32 — DATA_QUALITY_HOLD Sub-Tag (WOW-PATCH-DATA-QUALITY-HOLD, SHIPPED 2026-07-06)

**Problem:** internal projections silently fell back to "average-only" support
(`l10_median` missing, only `l10_avg` available) with no distinct signal — the
resulting prop could still reach `Model Qualified`/`Final Approved` and be treated as
Power/Flex slip-eligible despite weaker support than a true median-backed projection.
User-approved ruling: this needs an official sub-tag, not a terminal label.

**Rules (sub-tag, never terminal):**
- `DATA_QUALITY_HOLD` defaults the parent label to `Watch`.
- Max parent label is `Model Qualified` and **only** with independent market/projection
  support (e.g. odds available) alongside score/projection thresholds — it can never
  reach `Final Approved`/`Market Verified` while the tag is set.
- Never overrides `THIN_MARGIN_RISK` (not yet implemented anywhere in this codebase as
  of this patch — treated as a no-op/future concern, not fabricated).
- Always sets `block_power_flex: true` when it fires — average-only support alone can
  never grant Power/Flex slip-eligibility, regardless of parent label.

**Enforcement points:**
- `classify_prop()` (`jobs/wow_daily_scan.py`, used by `POST /wow/daily-scan`) — checked
  after the injury hard-reject and the binary-event structural cap (§23/§24), before the
  Final Approval tier. Returns a 4-tuple
  `(classification, final_approval_blocker, data_quality_tag, block_power_flex)`.
- `POST /final-lock` (`app.py`) — new Gate 4b, immediately after projection resolution
  (Gate 4) and before the margin gate (Gate 5): if `used_average_only` is `True`,
  downgrades to `WATCH`/`DATA_QUALITY_HOLD` with `block_power_flex: true`. Gate 3 (L10
  data) was narrowed from "reject whenever `l10_median` is missing" to "reject only when
  there is truly no data to project from at all" (`l10_values` empty **and**
  `l10_median` **and** `recent_avg` all missing) — otherwise the average-only path was
  unreachable and this gate could never fire.

**New field contract additions (§7):**
- `used_average_only` (bool) — `True` when `l10_median` was `None` and `l10_avg` was
  used to compute the internal projection.
- `data_quality_tag` (string | null) — `"DATA_QUALITY_HOLD"` when the sub-tag fires,
  else `null`.
- `block_power_flex` (bool) — `True` whenever any sub-tag disqualifies Power/Flex
  slip-eligibility (currently only `DATA_QUALITY_HOLD`, extensible to future sub-tags).
- `live_cushion_margin` (float | null) — **live scoring only**:
  `projection_or_median_value − line`. Computed inside `compute_internal_projection()`.
- `retro_result_margin` (float | null) — **retro QA only**: `final_result − line`.
  Computed by the standalone `compute_retro_result_margin(final_result, line)` helper,
  which has no call site in any live gating path — never used for live approval
  decisions, tracking/QA use only.
- `final_result` (float | null) — the actual settled stat line, populated only at
  retro-grading time (always `None` at scan/lock time).

**DB:** `scan_results` gained 6 new columns (`used_average_only`, `data_quality_tag`,
`block_power_flex`, `live_cushion_margin`, `retro_result_margin`, `final_result`) via
non-destructive `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.

**Live-verified (2026-07-06):**
- Average-only + no odds → `WATCH`/`DATA_QUALITY_HOLD`/`block_power_flex: true`,
  `live_cushion_margin` populated.
- Median present → unaffected, reaches `FINAL APPROVED — INTERNAL PROJECTION`,
  `data_quality_tag: null`, `block_power_flex: false`.
- No l10 data at all → still rejects `L10_UNVERIFIED` (unchanged failure mode).

---

## §33 — Cross-Market Reject-Proof Output Contract + Degraded Run Gate (WOW-PATCH-2026-07-06-CROSS-MARKET-REJECT-PROOF-AND-DEGRADED-RUN-GATE, SHIPPED 2026-07-06)

**Scope:** legacy `/wow-daily-scan` route only (`jobs/wow_daily_scan.py::run_scan` +
`classify_prop()`) — a separate pipeline from `gate_engine`/`_llp_decision`, which already
has its own no-vig math, ledger, and correlation gate.

**Problem:** the legacy scanner gave no board line, no consensus line/odds, no no-vig
probability, no explicit edge math, no drift grade, and no machine-readable reason for why
a prop wasn't playable — only a free-text blocker string. Rejections for "no edge" showed
no math. An uncaught upstream fetch exception (odds/rundown/injury/pitcher service)
propagated as a bare 500 instead of a labeled degraded run.

**New module:** `jobs/market_math.py` — `american_to_prob`, `no_vig_pair`,
`pp_cash_threshold`, `compute_threshold_hit_rate`, `compute_drift_grade`,
`MARKET_CAUSE_TAGS`, `classify_market_cause`.

**Full output-row contract (every prop, every bucket):** `board_line`,
`pp_cash_threshold`, `consensus_line`, `consensus_price_more`, `consensus_price_less`,
`no_vig_probability`, `model_probability`, `adjusted_edge`, `edge_math` (literal
`"a - b = c"` string), `board_consensus_delta`, `drift_grade` (A–F, `U` if unavailable),
`market_cause`, `terminal_bucket`, `threshold_hit_rate`, `source_conflict`,
`mutex_group_id`, `preferred_candidate`.

**REJECT_NO_EDGE:** when a prop is terminally `Reject` on score/edge grounds (not the
injury hard-reject or the binary-event structural cap, §23/§24, which keep their own
blocker text), `final_approval_blocker` becomes
`"REJECT_NO_EDGE: {edge_math} (no verified positive edge vs. no-vig consensus)"`.

**9 mandatory market-cause tags** (`market_cause`, non-null for any bucket below Final
Approved): `NO_VERIFIED_MISPRICE`, `MARKET_AGAINST_SIDE`, `STALE_BOARD`,
`SOURCE_CONFLICT`, `EXACT_MARKET_UNAVAILABLE`, `ADJACENT_MARKET_ONLY`,
`ROLE_DEPLOYMENT_UNCERTAIN`, `PUBLIC_OVERREACTION_UNVERIFIED`, `PAYOUT_EV_FAIL`.

**Scoped SOURCE_CONFLICT (Layer-0 event reconciliation, item 4 — partial):**
`build_consensus_map()` groups raw props by `(player, prop)` across bookmakers; a >1.0
line disagreement sets `source_conflict: true` and caps any `Conditional`-or-above
classification down to `Watch`. This is cross-bookmaker line agreement only, **not** full
independent event/team/game_id/start_time identity reconciliation across data sources —
that remains unbuilt in this legacy job.

**Pitcher deployment (item 5 — reused, not rebuilt):** the pre-existing "not listed as
probable pitcher" MLB check is unchanged and now also feeds `ROLE_DEPLOYMENT_UNCERTAIN`
into `market_cause`. A full deployment module (opponent lineup K%, bullpen leash) was not
built for this job.

**WNBA L5/L10 (item 6):** already satisfied structurally — `services/player_logs.py`'s
`get_player_log_stats()` is sport-agnostic (ESPN core-API eventlog via the `SPORT_LEAGUE`
map, which already includes WNBA), so no MLB-specific special-casing blocks WNBA props
from this scanner's L5/L10 path. This is a distinct code path from the bbref-based
pipeline that fails for WNBA elsewhere in this codebase.

**PP whole-line threshold conversion (item 7):** `market_math.pp_cash_threshold()`
converts the display line into the real cash/push/loss threshold; `threshold_hit_rate` and
`model_probability` are computed against that threshold, not the bare display line.
`PAYOUT_EV_FAIL` fires when the threshold-adjusted hit rate is materially worse (>5pts)
than the display-line hit rate.

**Scoped mutex guard (item 8 — partial):** `assign_mutex_groups()` groups all
playable-tier cards by `(sport, player, game_date)`; 2+ candidates in a group get a shared
`mutex_group_id`, with the single highest-`wow_score` candidate marked
`preferred_candidate: true`. This is same-player collision detection only, **not** the
richer stat-family/game-script correlation logic already implemented in
`gate_engine/correlation_gate.py` for slip construction — that was not ported into this
legacy job.

**DEGRADED_ENGINE_RUN gate (item 9):** every backend fetch in `run_scan()`
(`fetch_all_props`, `fetch_backup_props`, `get_injuries`, `get_mlb_probable_pitchers`) is
wrapped in `try/except`; failures accumulate in `failed_modules` instead of raising. If
`failed_modules` is non-empty, the run's top-level `run_status` is
`"DEGRADED_ENGINE_RUN"` and every card in `market_verified`/`final_approved_internal`/
`model_qualified` is force-moved into `watch` with `degraded_run_hold: true` — a degraded
run can never silently present a Final Approved/Model Qualified pick. Clean runs report
`run_status: "COMPLETE"`.

**Deferred (documented scope decision, not built in this patch):** full Layer-0 event
reconciliation beyond scoped `SOURCE_CONFLICT`; full pitcher-deployment module beyond the
reused probable-pitcher signal; full stat-family/correlation guard beyond scoped
same-player mutex grouping; a true PrizePicks board feed (`board_line`/`consensus_line`
here are the scanner's own cross-bookmaker consensus, not a real PrizePicks snapshot).

**DB:** `scan_results` gained 17 new nullable columns (see patch doc for full list) via
non-destructive `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.

**Live-verified (2026-07-06):** `POST /wow-daily-scan` returns `run_status`/
`failed_modules` at top level; every bucket's props carry the full new field set;
`Reject` rows show `REJECT_NO_EDGE:` blocker text with populated `edge_math`.

---

## What to send back when you (ChatGPT / Claude) want a change

1. The **delta** vs. this snapshot — what decision/threshold/contract you want
   different, and why.
2. Any decision made in your thread that isn't reflected here.
3. Which spec wins if yours disagrees with what's shipped above.
