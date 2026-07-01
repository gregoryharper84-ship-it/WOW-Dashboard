# WOW Patch Template

**How to use:**
Copy this file, fill in every section, then share it with the Replit agent (or any
planning thread) _before_ any code is written. A patch that cannot answer all five
sections clearly is not ready to build.

The live spec is `LLP_GROUND_TRUTH.md` in this directory. That file, not memory or
a stale project file, is the conflict-check target. Code in `app.py` is the
implementation truth; the ground-truth doc is a snapshot of it. If they disagree,
the code wins and the doc needs updating.

---

## Patch ID
`WOW-PATCH-XXX` — increment from the last patch in the log below.

## Author / date
_Who proposed this and when (YYYY-MM-DD)._

## Status
`DRAFT` | `READY` | `BUILDING` | `SHIPPED` | `REJECTED`

---

## 1. Problem statement

_One paragraph. What is wrong or missing today, and what user-visible or
model-visible behavior does this patch change? Be concrete — cite the actual
endpoint, field, gate flag, badge, or decision that is affected._

## 2. Affected spec sections

_List every section in `LLP_GROUND_TRUTH.md` that this patch touches or extends.
If it adds a new section, say so explicitly._

| Section | Change type | Description |
|---------|-------------|-------------|
| §N — Title | ADD / MODIFY / DELETE | What changes |

_If this patch does not touch LLP_GROUND_TRUTH.md at all, state why (e.g.
"pure endpoint addition with no badge/gate/field-contract changes")._

## 3. Exact delta

_The minimal, precise change. For spec fields: old value → new value. For new
gate flags: name, trigger condition, badge/verdict impact. For new endpoints:
route, method, required/optional body fields, response contract. For thresholds:
old number → new number with rationale._

```
Example:
  Section §4 badge ceiling:
  ADD: clv_beat is None → cap at WAIT  (was: not listed)

  Section §7 field contract:
  ADD top-level key: "platoon_splits" (object | null)
```

## 4. Test case

_A concrete pass/fail check that can be run by curl or pytest. At minimum:
input → expected output for the happy path, and at least one failure/edge case._

```bash
# Happy path
curl -X POST http://localhost:80/api/<endpoint> \
  -H "X-API-Key: $SCORING_API_KEY" \
  -H "Content-Type: application/json" \
  -d '<exact JSON body>'

# Expected response (key fields only):
# { "ok": true, "<field>": <expected_value>, ... }

# Edge / failure case
# <describe input> → expected: { "ok": false, "error": "..." }
```

## 5. Conflict check

_Answer each question explicitly. "N/A" is not acceptable — if it does not apply,
say why._

| Question | Answer |
|----------|--------|
| Does this change any existing badge/ceiling rule? | |
| Does this add, rename, or remove a top-level field from §7's field contract? | |
| Does this change the set of hard vs. advisory failure-path tags (§6)? | |
| Does this alter `_llp_decision` logic or its input thresholds (§3)? | |
| Does this change any Odds API market alias or sport-key mapping (§5, §8)? | |
| Does this affect the odds-snapshot cron, snapshot kinds, or CLV grading (§11)? | |
| Does this require a DB migration (new table, new column, new index)? | |
| Does this add a new route that the Express proxy in `scoring-proxy.ts` must forward? | |
| Could gunicorn's 2-worker setup cause a race condition on any shared state this adds? | |

## 6. Ground-truth doc update

_After the patch ships, list the exact lines that must be updated in
`LLP_GROUND_TRUTH.md`. Leave blank until status = SHIPPED._

---

## Patch log

| Patch ID | Date | Status | Summary |
|----------|------|--------|---------|
| WOW-PATCH-001 | 2026-06-30 | SHIPPED | Kalshi NHIGH weather lane — 5-city station map, NWS CLI fetcher, bracket scorer, `/wow/kalshi/weather/evaluate` + `/stations` |
| WOW-PATCH-002 | 2026-07-01 | SHIPPED | Gaussian bracket probabilities — `_score_weather_brackets_gaussian`, `math.erf` CDF, sigma_f=3.5 default, full normalization, CLI date-mismatch guard |
| WOW-PATCH-003 | 2026-07-01 | SHIPPED | Price-source staleness gate — `_apply_weather_price_gate`, `_weather_terminal_label_v2`, synthetic/operator_supplied capped at KALSHI_WATCH, FINAL+no-live → KALSHI_DATA_UNOBTAINABLE |
| WOW-PATCH-004 | 2026-07-01 | DRAFT | Summer-only sigma calibration — filter NCEI CDO window to summer months; Phase 2: empirical MAE from WEATHER_SCOUT ledger |
| WOW-PATCH-005 | 2026-07-01 | DRAFT | Pitcher handedness splits — platoon-weighted opp K%, `vs_lhb_k_pct` / `vs_rhb_k_pct` from MLB Stats API `vsPlayer` stat group |
| WOW-PATCH-006 | 2026-07-01 | DRAFT | Line movement tracker — `line_movement` advisory field from odds_snapshot history; `sharp_signal` POSSIBLE/UNLIKELY/UNKNOWN gate |
| WOW-PATCH-007 | 2026-07-01 | DRAFT | CLV tracker — `clv_beat` to `llp_postmortem`; `/wow/clv/summary`; depends on closing snapshot within 4h of game start |
| WOW-PATCH-008 | 2026-07-01 | SHIPPED | Gate 3 proportional-edge classifier — `POST /wow/l10/gate3`; proportional gap_pct replaces absolute 1.5-unit kill; 55–64% hit-rate = DISCOVERY_ONLY (WATCH_ELEVATED ceiling); winsor_cap_v1 deterministic Winsorization; WATCH_ELEVATED tier; shadow logging to `gate3_shadow_log` |
