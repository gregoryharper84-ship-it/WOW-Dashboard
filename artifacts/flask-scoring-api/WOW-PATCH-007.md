# WOW-PATCH-007 — CLV Tracker

## Patch ID
`WOW-PATCH-007`

## Author / date
User + Replit agent — 2026-07-01

## Status
`DRAFT`

---

## 1. Problem statement

Closing line value (CLV) is the industry-standard long-run edge validation metric: if a bettor consistently gets odds better than the closing line, they have demonstrated real edge independent of short-term win/loss variance. The current LLP system grades results as win/loss via `wow_llp_settle` but does not compare the entry price against the closing line at settlement time. Without CLV tracking, the calibration ledger can only report Brier scores and raw win rate — it cannot distinguish skill from luck. Adding CLV tracking to the `llp_postmortem` table is the highest-ROI data collection improvement available for the LLP lane.

**Dependency:** Requires the odds-snapshot cron to have captured a snapshot near closing (within 2–4 hours of game start). Verify this is already happening before building.

## 2. Affected spec sections

| Section | Change type | Description |
|---------|-------------|-------------|
| §11 — Odds snapshot / CLV grading | MODIFY | Add CLV computation to `wow_llp_settle`; add `clv_beat` and `closing_line` to response |
| §7 — Field contract | ADD | `clv_beat` (float, nullable) and `closing_line` (float, nullable) to settle response |
| `llp_postmortem` table | MODIFY | Add `clv_beat`, `closing_line`, `closing_snapshot_id` columns |
| New endpoint | ADD | `/wow/clv/summary` — aggregate CLV stats across settled rows |

## 3. Exact delta

### New columns in `llp_postmortem`

```sql
ALTER TABLE llp_postmortem ADD COLUMN IF NOT EXISTS closing_line      REAL;
ALTER TABLE llp_postmortem ADD COLUMN IF NOT EXISTS closing_snapshot_id INT;
ALTER TABLE llp_postmortem ADD COLUMN IF NOT EXISTS clv_beat           REAL;
-- clv_beat = entry_odds_american - closing_odds_american (positive = beat closing)
-- e.g. entry at -110 vs close at -120 → clv_beat = +10 (American odds units)
```

### New fields in `wow_llp_settle` response

```json
"clv_beat":         10.0,       // null if no closing snapshot found
"closing_line":    -120.0,      // American odds at closing snapshot
"closing_snapshot_id": 4821,    // for audit traceability
"clv_status":      "BEAT",      // BEAT | MISSED | PUSH | UNKNOWN
"clv_note":        "Entry at -110 vs close at -120; +10 American odds units of CLV"
```

### `clv_status` thresholds

| Condition | clv_status |
|-----------|-----------|
| `clv_beat > 0` | BEAT |
| `clv_beat < 0` | MISSED |
| `clv_beat == 0` | PUSH |
| No closing snapshot | UNKNOWN |

### New endpoint: `/wow/clv/summary`

```
GET /wow/clv/summary?sport=NFL&limit=100
```

Returns:
```json
{
  "total_bets":       87,
  "clv_tracked":      71,         // rows where clv_beat is not null
  "clv_mean":         4.2,        // mean American odds CLV
  "clv_beat_pct":     0.63,       // fraction where clv_beat > 0
  "roi_if_clv_pos":   null,       // future: win rate for CLV+ bets
  "breakdown_by_sport": { ... }
}
```

### CLV computation logic in `wow_llp_settle`

1. Look up the most recent `odds_snapshot` for the same `event_id` where `snapshot_time` is within 4 hours before game start and after the entry was logged.
2. Extract `home_ml` / `away_ml` (or spread odds) matching the side the entry was placed on.
3. Compute `clv_beat = entry_odds_american - closing_odds_american`.
4. Write to `llp_postmortem`.
5. If no qualifying snapshot found → `clv_beat = null`, `clv_status = UNKNOWN`.

## 4. Test case

```bash
# Happy path — settle a bet with closing snapshot available
curl -X POST http://localhost:80/api/wow/llp/settle \
  -H "X-API-Key: $SCORING_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "postmortem_id": 42,
    "result":        "WIN",
    "final_score":   "24-17"
  }'

# Expected (key fields):
# { "ok": true, "clv_beat": 10.0, "closing_line": -120, "clv_status": "BEAT", ... }

# No closing snapshot:
# { "clv_beat": null, "clv_status": "UNKNOWN", "closing_snapshot_id": null }

# CLV summary
curl http://localhost:80/api/wow/clv/summary?sport=NFL
# { "ok": true, "clv_tracked": 71, "clv_mean": 4.2, "clv_beat_pct": 0.63, ... }
```

## 5. Conflict check

| Question | Answer |
|----------|--------|
| Does this change any existing badge/ceiling rule? | No. CLV is post-settlement tracking only — not read by `_llp_decision`. |
| Does this add, rename, or remove a top-level field from §7's field contract? | ADD `clv_beat`, `closing_line`, `clv_status`, `clv_note` to settle response. No existing fields changed. |
| Does this change the set of hard vs. advisory failure-path tags (§6)? | No. |
| Does this alter `_llp_decision` logic or its input thresholds (§3)? | No — CLV is retrospective grading, not prospective decision input. |
| Does this change any Odds API market alias or sport-key mapping (§5, §8)? | No — reads existing snapshots only. |
| Does this affect the odds-snapshot cron, snapshot kinds, or CLV grading (§11)? | YES — depends on cron capturing a snapshot within 4h of game start. Verify cron schedule vs. game times before building. |
| Does this require a DB migration (new table, new column, new index)? | YES — 3 new columns on `llp_postmortem` + index on `(event_id, snapshot_time)` in `odds_snapshot`. Non-destructive. |
| Does this add a new route that the Express proxy in `scoring-proxy.ts` must forward? | YES — `/wow/clv/summary` needs a GET proxy route in `scoring-proxy.ts`. |
| Could gunicorn's 2-worker setup cause a race condition on any shared state this adds? | No — pure DB read/write with no in-process state. |

## 6. Ground-truth doc update

_Leave blank until status = SHIPPED._

---

## Pre-build checklist

Before handing to Replit for implementation:
- [ ] Query odds_snapshot: `SELECT MAX(snapshot_time) - MIN(snapshot_time) AS window FROM odds_snapshot WHERE event_id = <recent_event>` — confirm closing snapshot exists within 4h of game start
- [ ] Confirm `entry_odds_american` is stored in `llp_postmortem` (check current schema)
- [ ] Confirm `event_id` linkage between `llp_postmortem` and `odds_snapshot`
- [ ] ChatGPT conflict-check against LLP_GROUND_TRUTH.md §11 CLV grading section
- [ ] Verify `wow_llp_settle` endpoint name and route in app.py before building
