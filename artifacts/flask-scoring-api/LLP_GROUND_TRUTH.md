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

## What to send back when you (ChatGPT / Claude) want a change

1. The **delta** vs. this snapshot — what decision/threshold/contract you want
   different, and why.
2. Any decision made in your thread that isn't reflected here.
3. Which spec wins if yours disagrees with what's shipped above.
