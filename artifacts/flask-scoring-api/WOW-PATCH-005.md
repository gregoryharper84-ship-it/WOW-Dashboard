# WOW-PATCH-005 — Pitcher Handedness Splits

## Patch ID
`WOW-PATCH-005`

## Author / date
User + Replit agent — 2026-07-01

## Status
`DRAFT`

---

## 1. Problem statement

The MLB pitcher analysis endpoint (`/wow/l10/v2` or equivalent, `sport=MLB`) currently returns aggregate starter metrics (opp K%, leash proxy via statcast) without accounting for batter handedness. A right-handed pitcher facing a lineup heavy with left-handed batters has meaningfully different K% expectations than the aggregate implies. Kalshi and PrizePicks strikeout props routinely show 1–3 strikeout edges when the starter's platoon splits are ignored. Adding handedness-weighted opp K% as an additional input to the edge calculation, without changing any existing output fields, is the lowest-risk improvement available for the MLB pitcher lane.

**Why cleanest of the three queued items:** uses the same MLB Stats API endpoint family already in production (`/api/v1/teams/{id}/roster`, `/api/v1/people/{id}/stats`) — no new data source, no new auth, no label conflicts with the existing field contract.

## 2. Affected spec sections

| Section | Change type | Description |
|---------|-------------|-------------|
| §7 — Field contract (MLB pitcher) | ADD | `platoon_splits` object — optional, null if unavailable |
| MLB data pipeline | ADD | New helper `_fetch_pitcher_platoon_splits(pitcher_id, season)` |
| Edge calculation (MLB strikeout) | MODIFY | If `platoon_splits` available, blend aggregate opp_k_pct with handedness-weighted value |

## 3. Exact delta

### New field: `platoon_splits` (top-level, nullable)

```json
"platoon_splits": {
  "vs_lhb_k_pct":    0.28,   // K% vs left-handed batters (null if unavailable)
  "vs_rhb_k_pct":    0.22,   // K% vs right-handed batters (null if unavailable)
  "opp_lhb_pct":     0.41,   // fraction of opposing lineup that bats left
  "weighted_k_pct":  0.2534, // vs_lhb_k_pct * opp_lhb_pct + vs_rhb_k_pct * (1 - opp_lhb_pct)
  "data_source":     "mlb_stats_api",
  "season":          2026,
  "sample_pa_lhb":   120,
  "sample_pa_rhb":   180
}
```

### Scoring change

If `platoon_splits.weighted_k_pct` is available AND sample_pa ≥ 30 for each split:
- Replace `opp_k_pct` with `weighted_k_pct` in the edge formula
- Add `platoon_edge_active: true` to the response

If unavailable (API miss, insufficient PAs, or season too early):
- Fall back to existing `opp_k_pct` aggregate
- `platoon_splits: null`
- `platoon_edge_active: false`
- **No change to existing badge/terminal label gates** — platoon splits are advisory only

### Data source

MLB Stats API — same host already in use:
```
GET https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats
    ?stats=vsPlayer&group=pitching&season=2026&opposingTeamId={opp_team_id}
```
Splits by batter handedness available in the `vsPlayer` stat group. Falls back to `career` if current-season sample is too thin.

## 4. Test case

```bash
# Happy path — pitcher with known splits
curl -X POST http://localhost:80/api/wow/l10/v2 \
  -H "X-API-Key: $SCORING_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"sport":"MLB","player":"Garrett Crochet","prop_type":"strikeouts","line":6.5}'

# Expected (key fields):
# { "ok": true, "platoon_splits": { "weighted_k_pct": <float>, ... }, "platoon_edge_active": true }

# Fallback — pitcher with insufficient split data
# platoon_splits should be null, platoon_edge_active: false
# All existing fields unchanged (edge, verdict, terminal_label, etc.)

# Edge case — sample_pa too thin (< 30 PA per split)
# { "platoon_splits": { "vs_lhb_k_pct": null, "sample_pa_lhb": 12, ... }, "platoon_edge_active": false }
```

## 5. Conflict check

| Question | Answer |
|----------|--------|
| Does this change any existing badge/ceiling rule? | No. Platoon splits are advisory; existing badge gates unchanged. |
| Does this add, rename, or remove a top-level field from §7's field contract? | ADD `platoon_splits` (object, nullable) and `platoon_edge_active` (bool). No existing fields renamed or removed. |
| Does this change the set of hard vs. advisory failure-path tags (§6)? | No. |
| Does this alter `_llp_decision` logic or its input thresholds (§3)? | No — MLB strikeout props are scored separately from LLP h2h decisions. |
| Does this change any Odds API market alias or sport-key mapping (§5, §8)? | No. |
| Does this affect the odds-snapshot cron, snapshot kinds, or CLV grading (§11)? | No. |
| Does this require a DB migration (new table, new column, new index)? | No — platoon_splits is computed at request time, not stored. |
| Does this add a new route that the Express proxy in `scoring-proxy.ts` must forward? | No — extends existing `/wow/l10/v2` response only. |
| Could gunicorn's 2-worker setup cause a race condition on any shared state this adds? | No — stateless MLB Stats API call per request. |

## 6. Ground-truth doc update

_Leave blank until status = SHIPPED._

---

## Pre-build checklist

Before handing to Replit for implementation:
- [ ] Confirm MLB Stats API `vsPlayer` group returns per-handedness splits at endpoint above
- [ ] Verify `pitcher_id` is already resolved by existing MLB data pipeline (check `_fetch_pitcher_game_log`)
- [ ] Confirm `opp_team_id` is available in the existing request context
- [ ] ChatGPT conflict-check against LLP_GROUND_TRUTH.md §7 field contract
