# WOW-PATCH-DATA-QUALITY-HOLD — DATA_QUALITY_HOLD Sub-Tag (Section 32)

## Patch ID
`WOW-PATCH-DATA-QUALITY-HOLD`

## Author / date
User (ruling approved) + legacy platform agent — 2026-07-05 / 2026-07-06

## Status
`SHIPPED`

---

## 1. Problem statement

Internal projections silently fell back to "average-only" support (L10 median missing,
only L10 avg available) with no distinct signal — the resulting prop could still reach
`Model Qualified`/`Final Approved` and be treated as Power/Flex slip-eligible even though
its support was weaker than a true median-backed projection. The user ruled that this
condition needs an official Section 32 **sub-tag**, `DATA_QUALITY_HOLD`: it must never be
a terminal label, defaults the parent label to `Watch`, can ceiling at `Model Qualified`
only with independent market/projection support, must never override
`THIN_MARGIN_RISK` (not yet implemented elsewhere), and must always block Power/Flex
slip-eligibility on its own. Separately, `live_cushion_margin` (live scoring:
projection/median − line) and `retro_result_margin` (retro QA only: final_result − line)
were conflated under ad hoc margin fields and needed to become distinct, clearly
documented fields.

## 2. Affected spec sections

| Section | Change type | Description |
|---------|-------------|-------------|
| §32 (new) — DATA_QUALITY_HOLD sub-tag | ADD | New sub-tag rules, default/ceiling behavior, field contract additions |
| §7 — Field contract | ADD | `used_average_only`, `data_quality_tag`, `block_power_flex`, `live_cushion_margin`, `retro_result_margin` (retro only), `final_result` |

## 3. Exact delta

### `jobs/wow_daily_scan.py`
- `compute_internal_projection()` now also returns `used_average_only` (bool — True when
  `l10_median` is `None` and `l10_avg` was used) and `live_cushion_margin`
  (`projection_value − line`).
- New standalone `compute_retro_result_margin(final_result, line)` — retro QA only,
  never called from any live gating path.
- `classify_prop()` return signature changed from a 2-tuple to a 4-tuple:
  `(classification, final_approval_blocker, data_quality_tag, block_power_flex)`.
  DATA_QUALITY_HOLD cap logic runs after the injury hard-reject and the binary-event
  structural cap (§23/§24), but before the Final Approval tier: defaults to `"Watch"`;
  ceilings at `"Model Qualified — PrizePicks"` only when odds are available and
  proj/score thresholds are independently met; `block_power_flex` is always `True`
  whenever `used_average_only` is `True`.
- `run_scan()` updated to unpack the 4-tuple and persist the new fields on both
  `result_row` and `card`.

### `app.py` — `POST /final-lock`
- Gate 3 (L10 data) previously hard-rejected (`L10_UNVERIFIED`) whenever `l10_median`
  was missing, regardless of whether `l10_values`/`recent_avg` were present — this made
  the average-only path unreachable. Narrowed to only reject when there is truly no data
  to project from at all (`l10_values` empty **and** `l10_median` **and** `recent_avg`
  both missing).
- New Gate 4b, immediately after projection resolution: if `used_average_only` is
  `True`, downgrades to `WATCH` / `DATA_QUALITY_HOLD` with `block_power_flex: true`,
  short-circuiting before the margin/market-sanity gates.
- `_reject`/`_downgrade` closures and both success-path payloads (save + response) now
  include `used_average_only`, `data_quality_tag`, `block_power_flex`, and
  `live_cushion_margin`.

### DB — `scan_results` table
```sql
ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS used_average_only   BOOLEAN;
ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS data_quality_tag    TEXT;
ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS block_power_flex    BOOLEAN;
ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS live_cushion_margin NUMERIC;
ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS retro_result_margin NUMERIC;
ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS final_result        NUMERIC;
```
`storage/results.py`: `save_scan_result()` INSERT + params updated to include all 6
new fields; `get_compact_scan_rows()` SELECT updated (`get_scan_results()` already
used `SELECT *`, so it picked the new columns up automatically).

## 4. Test case

```bash
# Average-only fallback -> capped at WATCH / DATA_QUALITY_HOLD, Power/Flex blocked
curl -X POST http://localhost:80/final-lock \
  -H "X-API-Key: $SCORING_API_KEY" -H "Content-Type: application/json" \
  -d '{
    "player": "Test", "sport": "NBA", "prop": "points", "side": "MORE", "line": 20.0,
    "status_confirmed": true, "line_verified": true, "market_sanity": true,
    "l5_values": [30,28,27,29,31],
    "l10_values": [30,28,27,29,31,24,22,26],
    "l10_median": null, "recent_avg": 25.0
  }'
# Actual response (live-verified 2026-07-06):
# {"classification":"WATCH","data_quality_tag":"DATA_QUALITY_HOLD","block_power_flex":true,
#  "used_average_only":true,"live_cushion_margin":5.52,"final_approval_blocker":"DATA_QUALITY_HOLD", ...}

# Median present -> unaffected, reaches Final Approved
# ... l10_median: 26.0 -> {"classification":"FINAL APPROVED — INTERNAL PROJECTION",
#     "data_quality_tag":null,"block_power_flex":false,"used_average_only":false, ...}

# No data at all -> still rejects (unchanged failure mode, message updated)
# ... no l10_values/l10_median/recent_avg -> {"blocker_code":"L10_UNVERIFIED", ...}
```

Also ad-hoc verified directly against `jobs/wow_daily_scan.py`'s `classify_prop()` /
`compute_internal_projection()` / `compute_retro_result_margin()` (average-only + no
odds → `Watch`/`DATA_QUALITY_HOLD`/`block_power_flex=True`; average-only + odds
available + strong score → `Model Qualified — PrizePicks`/`DATA_QUALITY_HOLD`/
`block_power_flex=True`; median present → unaffected `Final Approved`/no tag).

## 5. Conflict check

| Question | Answer |
|----------|--------|
| Does this change any existing badge/ceiling rule? | YES — adds a new sub-tag ceiling: `DATA_QUALITY_HOLD` defaults to `Watch`, ceilings at `Model Qualified` only with independent market/projection support, never terminal, never a Final Approved/Market Verified path. Does not touch any other badge's ceiling. |
| Does this add, rename, or remove a top-level field from §7's field contract? | ADD `used_average_only`, `data_quality_tag`, `block_power_flex`, `live_cushion_margin`, `retro_result_margin` (retro only), `final_result`. No existing field renamed or removed. |
| Does this change the set of hard vs. advisory failure-path tags (§6)? | Adds `DATA_QUALITY_HOLD` as a new advisory (non-terminal) sub-tag; existing hard/advisory tags unchanged. |
| Does this alter `_llp_decision` logic or its input thresholds (§3)? | No — this patch is scoped to `classify_prop()`/`/final-lock`, not the Kalshi/LLP `_llp_decision` path. |
| Does this change any Odds API market alias or sport-key mapping (§5, §8)? | No. |
| Does this affect the odds-snapshot cron, snapshot kinds, or CLV grading (§11)? | No. |
| Does this require a DB migration (new table, new column, new index)? | YES — 6 new columns on `scan_results` (listed above), applied via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, non-destructive. |
| Does this add a new route that the Express proxy in `scoring-proxy.ts` must forward? | No — reuses the existing `/final-lock` and `/wow/daily-scan` routes; no new route added. |
| Could gunicorn's 2-worker setup cause a race condition on any shared state this adds? | No — pure per-request computation and a single `INSERT`/param-set change; no shared in-process state introduced. |

## 6. Ground-truth doc update

Added `## §32 — DATA_QUALITY_HOLD Sub-Tag` to `LLP_GROUND_TRUTH.md`, documenting the
default/ceiling rules, the two enforcement points (`classify_prop()` and `/final-lock`
Gate 4b), the widened `/final-lock` Gate 3 condition, and the `live_cushion_margin` /
`retro_result_margin` field split.

---

## Notes

- `THIN_MARGIN_RISK` does not exist anywhere in the codebase yet (confirmed via grep at
  implementation time) — this patch correctly treats it as a no-op/future concern per
  the ruling, and does not fabricate any interaction with it.
- `retro_result_margin` is intentionally a standalone helper (`compute_retro_result_margin`)
  with no call site in any live scoring/gating path — it exists for retro QA tooling only.
