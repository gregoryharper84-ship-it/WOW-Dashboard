# WOW-PATCH-006 — Line Movement Tracker

## Patch ID
`WOW-PATCH-006`

## Author / date
User + Replit agent — 2026-07-01

## Status
`DRAFT`

---

## 1. Problem statement

The LLP engine scores a game at a single point in time — the moment the user calls `/wow/l10/v2` or the odds-snapshot cron fires. Line movement between open (e.g., Sunday night) and current time is a meaningful signal for sharp money detection that the current model ignores. A line that opened at -3.5 and has moved to -6.5 over 48 hours likely reflects sharp action; fading that line without knowing the movement direction introduces false confidence. The line movement tracker adds a new `line_movement` advisory field to the response, derived from the existing odds-snapshot table, without changing any badge or terminal label gates.

**Scope:** Advisory only. Does not change `_llp_decision` output or any badge ceiling. Adds `line_movement` to the §7 field contract as a nullable object.

## 2. Affected spec sections

| Section | Change type | Description |
|---------|-------------|-------------|
| §7 — Field contract | ADD | `line_movement` object (nullable) |
| §11 — Odds snapshot cron | MODIFY | Snapshot must retain earliest snapshot per event (currently may not — verify) |
| New endpoint (optional) | ADD | `/wow/line-movement` GET for standalone queries |

## 3. Exact delta

### New field: `line_movement` (top-level, nullable)

```json
"line_movement": {
  "open_line":         -3.5,
  "current_line":      -6.5,
  "movement":          -3.0,     // current - open; negative = moved toward favorite
  "open_timestamp":    "2026-06-29T18:00:00Z",
  "current_timestamp": "2026-07-01T10:00:00Z",
  "hours_elapsed":     40.0,
  "direction":         "toward_favorite",  // toward_favorite | toward_underdog | stable
  "sharp_signal":      "POSSIBLE",         // POSSIBLE | UNLIKELY | UNKNOWN
  "sharp_note":        "Line moved 3+ pts toward favorite in 40h — possible sharp action",
  "data_source":       "odds_snapshot_db"
}
```

### `sharp_signal` logic (advisory only)

| Condition | sharp_signal |
|-----------|-------------|
| `abs(movement) >= 2.5` AND `hours_elapsed < 72` | POSSIBLE |
| `abs(movement) < 1.0` OR `hours_elapsed >= 72` | UNLIKELY |
| No open snapshot found | UNKNOWN |

### DB dependency

Requires at least 2 snapshots per event in `odds_snapshot` table: one near open, one current.
- Check: does the snapshot cron retain the earliest snapshot per event, or only the latest N?
- If it prunes aggressively, need to add an `is_opening_line BOOLEAN` column set on first insert.

### No badge/gate changes

`line_movement` is purely informational. `_llp_decision` does not read it.
Badge ceilings, PLAYABLE/WATCH/REJECT gates: all unchanged.

## 4. Test case

```bash
# Happy path — game with 2+ snapshots
curl -X POST http://localhost:80/api/wow/l10/v2 \
  -H "X-API-Key: $SCORING_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"sport":"NFL","team":"Kansas City Chiefs","opponent":"Baltimore Ravens"}'

# Expected (key fields):
# { "ok": true, "line_movement": { "open_line": -3.5, "current_line": -6.5, "sharp_signal": "POSSIBLE", ... } }

# No snapshot history available:
# { "line_movement": null }

# Single snapshot only (no open to compare):
# { "line_movement": { "sharp_signal": "UNKNOWN", "open_timestamp": null } }
```

## 5. Conflict check

| Question | Answer |
|----------|--------|
| Does this change any existing badge/ceiling rule? | No. `line_movement` is advisory — not read by `_llp_decision` or any badge gate. |
| Does this add, rename, or remove a top-level field from §7's field contract? | ADD `line_movement` (object, nullable). No existing fields changed. |
| Does this change the set of hard vs. advisory failure-path tags (§6)? | No. |
| Does this alter `_llp_decision` logic or its input thresholds (§3)? | No — explicitly excluded. |
| Does this change any Odds API market alias or sport-key mapping (§5, §8)? | No — reads existing snapshots, does not change how they are fetched. |
| Does this affect the odds-snapshot cron, snapshot kinds, or CLV grading (§11)? | POSSIBLE: if cron prunes earliest snapshot per event, need `is_opening_line` column. Verify before building. |
| Does this require a DB migration (new table, new column, new index)? | Maybe: `ALTER TABLE odds_snapshot ADD COLUMN IF NOT EXISTS is_opening_line BOOLEAN DEFAULT FALSE` + index on (event_id, is_opening_line). Verify first. |
| Does this add a new route that the Express proxy in `scoring-proxy.ts` must forward? | Optional standalone endpoint `/wow/line-movement` would need a proxy route. Core use extends existing endpoint — no new route required for Phase 1. |
| Could gunicorn's 2-worker setup cause a race condition on any shared state this adds? | No — pure DB read. |

## 6. Ground-truth doc update

_Leave blank until status = SHIPPED._

---

## Pre-build checklist

Before handing to Replit for implementation:
- [ ] Query `odds_snapshot` table: `SELECT event_id, COUNT(*), MIN(snapshot_time) FROM odds_snapshot GROUP BY event_id HAVING COUNT(*) > 1 LIMIT 10` — confirm multi-snapshot events exist
- [ ] Verify snapshot cron retention policy — does it keep the opening line?
- [ ] Confirm spread field name in snapshot table (`spread_home`? `line`? `point_spread`?)
- [ ] ChatGPT conflict-check against LLP_GROUND_TRUTH.md §11
