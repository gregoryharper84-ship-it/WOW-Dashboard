# WOW-PATCH-2026-07-06-CROSS-MARKET-REJECT-PROOF-AND-DEGRADED-RUN-GATE

## Patch ID
`WOW-PATCH-2026-07-06-CROSS-MARKET-REJECT-PROOF-AND-DEGRADED-RUN-GATE`

## Author / date
User (spec) + Replit agent — 2026-07-06

## Status
`SHIPPED` (scoped — see "Deferred" below)

---

## 1. Problem statement

The **live** `/wow-daily-scan` route (`app.py` → `jobs/wow_daily_scan.py::run_scan`) is a
legacy pipeline that predates `gate_engine/` v16. It does not use `gate_engine`'s no-vig
math, L5/L10 ledger, or correlation/mutex guard at all — it has its own independent
`classify_prop()` classifier with none of the cross-market reject-proofing that v16 has.
Concretely, before this patch, a rejected/watched/conditional row from `/wow-daily-scan`
gave no board line, no consensus line/odds, no no-vig probability, no explicit edge math,
no drift grade, and no machine-readable reason ("market cause") for why it wasn't playable
— only a free-text `message`/`final_approval_blocker` string. Rejections for "no edge"
did not show their math. A raw upstream fetch exception (odds/rundown/injury/pitcher
service) propagated uncaught out of `run_scan()`, surfacing as a bare 500 instead of a
labeled degraded run.

## 2. Affected spec sections

| Section | Change type | Description |
|---------|-------------|-------------|
| §33 (new) — Cross-Market Reject-Proof Output Contract + Degraded Run Gate | ADD | Full output-row contract, REJECT_NO_EDGE math, 9 market-cause tags, scoped SOURCE_CONFLICT, scoped same-player mutex, PP whole-line threshold conversion, DEGRADED_ENGINE_RUN gate |

## 3. Exact delta

### New file — `jobs/market_math.py`
Dependency-free helper module (no DB/network) shared math for this patch:
- `american_to_prob(price)` / `no_vig_pair(price_more, price_less)` — American odds →
  implied probability, de-vigged to sum to 1.0.
- `pp_cash_threshold(side, line)` — converts a display line into the actual PrizePicks
  cash/push/loss threshold (whole-number lines support a push at the exact line;
  fractional lines do not) — item 7.
- `compute_threshold_hit_rate(raw_games, side, threshold)` — empirical hit-rate against
  the cash threshold (not the bare display line), from the same `raw_l5`/`raw_l10` rows
  already fetched for the audit-valid check.
- `compute_drift_grade(adjusted_edge)` — A–F letter grade of `model_probability -
  no_vig_probability`; `U` when unavailable.
- `MARKET_CAUSE_TAGS` — the 9 mandatory tags: `NO_VERIFIED_MISPRICE`,
  `MARKET_AGAINST_SIDE`, `STALE_BOARD`, `SOURCE_CONFLICT`, `EXACT_MARKET_UNAVAILABLE`,
  `ADJACENT_MARKET_ONLY`, `ROLE_DEPLOYMENT_UNCERTAIN`, `PUBLIC_OVERREACTION_UNVERIFIED`,
  `PAYOUT_EV_FAIL`.
- `classify_market_cause(...)` — deterministic precedence mapping of run signals onto one
  of the 9 tags; returns `None` for the two fully-approved tiers (a market cause is a
  reason for NOT playing, not a badge on a play).

### `jobs/wow_daily_scan.py`
- **Item 9 — DEGRADED_ENGINE_RUN gate.** Every backend fetch call in `run_scan()`
  (`fetch_all_props`, `fetch_backup_props`, `get_injuries`, `get_mlb_probable_pitchers`)
  is now wrapped in `try/except`; any exception is recorded into a new `failed_modules`
  list instead of propagating. After scoring, if `failed_modules` is non-empty the run is
  labeled `run_status: "DEGRADED_ENGINE_RUN"` and every card in `market_verified`,
  `final_approved_internal`, and `model_qualified` is moved into `watch` with
  `degraded_run_hold: true` and an explicit blocker — a degraded run can never silently
  present a FINAL_APPROVED/MODEL_QUALIFIED pick. Clean runs return `run_status:
  "COMPLETE"`.
- **Items 1/3/7 — full output-row contract.** New per-prop fields on both `result_row`
  (persisted) and `card` (in-memory bucket): `board_line`, `pp_cash_threshold` (JSON of
  the `pp_cash_threshold()` dict), `consensus_line`, `consensus_price_more`,
  `consensus_price_less`, `no_vig_probability`, `model_probability` (threshold-adjusted
  hit rate, falling back to `l10_hit_rate`/`l5_hit_rate`), `adjusted_edge`, `edge_math`
  (literal `"a - b = c"` string), `board_consensus_delta`, `drift_grade`, `market_cause`,
  `terminal_bucket` (mirrors `classification` at write time), `threshold_hit_rate`,
  `source_conflict`.
- **Item 2 — REJECT_NO_EDGE explicit no-vig math.** When the terminal bucket is `Reject`
  purely on score/edge grounds (not injury or the binary-event structural cap, which
  already produce their own explicit blocker text), `final_approval_blocker` is replaced
  with `"REJECT_NO_EDGE: {edge_math} (no verified positive edge vs. no-vig consensus)"`.
- **Item 4 (scoped) — `build_consensus_map(props)`.** Groups the *raw*, pre-dedup props by
  `(player, prop)` across all bookmakers/sides to compute a cross-book consensus line and
  price, and a `conflict` flag when bookmakers disagree on the line by more than 1.0. When
  `conflict` is true, any classification at `Conditional` or above is capped to `Watch`
  with an explicit `SOURCE_CONFLICT` blocker, ahead of `market_cause` classification. Full
  Layer-0 event reconciliation (independent team/opponent/game_id/start_time identity
  matching across sources) is **not** implemented — see "Deferred" below.
- **Item 5 (reused, not rebuilt) — pitcher deployment signal.** The pre-existing "not
  listed as probable pitcher" check (`sport == "MLB" and player not in mlb_pitchers` →
  `Data Insufficient`) is unchanged and continues to gate MLB pitcher props. It also now
  feeds `role_deployment_uncertain` into `classify_market_cause()` →
  `ROLE_DEPLOYMENT_UNCERTAIN`. A full pitcher-deployment module (opponent lineup K%,
  bullpen leash) is **not** implemented — see "Deferred" below.
- **Item 6 — WNBA L5/L10.** No code change was needed: `services/player_logs.py`'s
  `get_player_log_stats()` is already sport-agnostic (ESPN core-API eventlog, driven by
  the `SPORT_LEAGUE` map which already includes `"WNBA": ("basketball", "wnba")`), so
  WNBA props already flow through the same L5/L10/raw-row/audit-valid path as every other
  sport in this legacy pipeline. Verified this is a distinct code path from the
  `gate_engine`/bbref pipeline referenced in `.agents/memory/mlb-pitcher-data-sources.md`
  — this scanner never calls basketball-reference.
- **Item 7 — PP whole-line threshold conversion.** Implemented via
  `market_math.pp_cash_threshold()`; `threshold_hit_rate` and `model_probability` are now
  computed against the real cash/push threshold, not the bare display line. Surfaced as
  `PAYOUT_EV_FAIL` in `market_cause` when the threshold-adjusted hit rate is materially
  worse (>5pts) than the display-line hit rate.
- **Item 8 (scoped) — `assign_mutex_groups(cards)`.** Groups all playable-tier cards
  (`market_verified` + `final_approved_internal` + `model_qualified` + `conditional`) by
  `(sport, player, game_date)`; when a group has 2+ candidates, tags them with a shared
  `mutex_group_id` and marks exactly one (highest `wow_score`) `preferred_candidate:
  true`, the rest `false`. Full stat-family / same-game-script correlation (as already
  implemented for slip construction in `gate_engine/correlation_gate.py`) is **not**
  ported into this legacy job — see "Deferred" below.
- `run_scan()` return dict gains top-level `run_status` and `failed_modules`.

### `app.py`
- `_compact_prop()` — surfaces all new WOW-PATCH-2026-07-06 fields (`board_line`,
  `pp_cash_threshold`, `consensus_line`, `consensus_price_more`, `consensus_price_less`,
  `no_vig_probability`, `adjusted_edge`, `edge_math`, `board_consensus_delta`,
  `drift_grade`, `market_cause`, `terminal_bucket`, `threshold_hit_rate`,
  `source_conflict`, `mutex_group_id`, `preferred_candidate`). Used by both
  `POST /wow-daily-scan` and `GET /scan-results/summary` (same helper, both call sites).
- `POST /wow-daily-scan` — response now includes top-level `run_status` and
  `failed_modules` (item 9), pulled directly from `run_scan()`'s return value.

### DB — `scan_results` table
```sql
ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS board_line numeric(10,4);
ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS pp_cash_threshold text;
ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS consensus_line numeric(10,4);
ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS consensus_price_more integer;
ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS consensus_price_less integer;
ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS no_vig_probability numeric(6,4);
ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS model_probability numeric(6,4);
ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS adjusted_edge numeric(6,4);
ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS edge_math text;
ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS board_consensus_delta numeric(10,4);
ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS drift_grade text;
ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS market_cause text;
ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS terminal_bucket text;
ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS threshold_hit_rate numeric(6,4);
ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS source_conflict boolean DEFAULT false;
ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS mutex_group_id text;
ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS preferred_candidate boolean;
```
Applied live via `psql "$DATABASE_URL"` — non-destructive, all 17 columns nullable.
`storage/results.py`: `save_scan_result()` INSERT + params updated to include all 17 new
fields; `get_compact_scan_rows()` SELECT updated (`get_scan_results()` already used
`SELECT *`, so it picks the new columns up automatically).

## 4. Deferred (documented, not built in this patch)

- **Full Layer-0 event reconciliation** (item 4) — independent identity matching of the
  same real-world event across sources (team/opponent/game_id/start_time cross-check).
  Implemented instead: scoped cross-bookmaker `SOURCE_CONFLICT` detection on
  player/prop/line only.
- **Full pitcher deployment module** (item 5) — opponent lineup K%, bullpen leash,
  handedness splits. Implemented instead: reuse of the existing "not listed as probable
  pitcher" signal, now also feeding `ROLE_DEPLOYMENT_UNCERTAIN` into `market_cause`.
- **Full stat-family/game-script correlation guard** (item 8) — `gate_engine/
  correlation_gate.py`'s richer correlation logic. Implemented instead: scoped
  same-`(sport, player, game_date)` mutex grouping with a single preferred candidate.
- **True PrizePicks board feed** — this scanner has no dedicated PrizePicks board
  ingestion; `board_line` is the scanner's own deduped sportsbook line, and
  `consensus_line`/`no_vig_probability` come from cross-bookmaker averaging
  (`build_consensus_map`), not a real PrizePicks board snapshot. `board_consensus_delta`
  should be read as "this book's line vs. the cross-book average", not "PrizePicks vs.
  sportsbook consensus."

These four items were flagged to the user as a documented scope decision before
implementation began; the note above stands as that record.

## 5. Test case

```bash
curl -s -X POST "http://localhost:80/wow-daily-scan" \
  -H "X-API-Key: $SCORING_API_KEY" -H "Content-Type: application/json" \
  -d '{"sports": ["NBA"], "limit_per_sport": 5}' | python3 -m json.tool
```
Expected: top-level `run_status` (`"COMPLETE"` or `"DEGRADED_ENGINE_RUN"`) and
`failed_modules` (`[]` on a clean run); every prop in every bucket carries
`board_line`, `consensus_line`, `no_vig_probability`, `adjusted_edge`, `edge_math`,
`drift_grade`, `market_cause` (non-null for any bucket below Final Approved),
`terminal_bucket`. A `Reject` row's `final_approval_blocker` starts with
`"REJECT_NO_EDGE:"` whenever `edge_math` is available.

## 6. Conflict check

| Question | Answer |
|----------|--------|
| Does this change any existing badge/ceiling rule? | Adds one new cap: `SOURCE_CONFLICT` forces `Conditional`-or-above down to `Watch`. Does not change the injury reject, binary-event structural cap, or DATA_QUALITY_HOLD ceiling rules. |
| Does this add, rename, or remove a top-level field from the field contract? | ADD 17 new fields (listed in §3/DB migration). No existing field renamed or removed. |
| Does this change the set of hard vs. advisory failure-path tags? | Adds `market_cause` as a new advisory tag (one of the 9 enum values) alongside the existing `final_approval_blocker`/`data_quality_tag`; existing tags unchanged. |
| Does this alter `_llp_decision` logic or its input thresholds? | No — this patch is scoped to the legacy `jobs/wow_daily_scan.py`/`/wow-daily-scan` path, not `gate_engine`/`_llp_decision`. |
| Does this change any Odds API market alias or sport-key mapping? | No. |
| Does this affect the odds-snapshot cron, snapshot kinds, or CLV grading? | No. |
| Does this require a DB migration? | YES — 17 new nullable columns on `scan_results`, applied via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, non-destructive (see §3). |
| Does this add a new route that the Express proxy must forward? | No — reuses the existing `/wow-daily-scan` and `/scan-results/summary` routes. |
| Could gunicorn's 2-worker setup cause a race condition on any shared state this adds? | No — `build_consensus_map`/`assign_mutex_groups` operate on per-request local lists only; no shared in-process or cross-worker state introduced. |

## 7. Ground-truth doc update

Added `## §33 — Cross-Market Reject-Proof Output Contract + Degraded Run Gate` to
`LLP_GROUND_TRUTH.md`.
