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
| WOW-PATCH-011 | 2026-07-01 | SHIPPED | WNBA ingestion scheduler — ESPN public API; 6 tables (wnba_schedule, wnba_player_game_logs, wnba_box_scores, wnba_injury_status, wnba_transactions, source_audit_log); in-process daemon cron (advisory-locked, once per ET day); 5 endpoints (health, refresh, player-log, schedule, source-audit); stale flag at 25h; missing_fields[] array per row |
| WOW-PATCH-010 | 2026-07-01 | CLOSED | Market Data Contract Registry — `POST /wow/data-contract/check` + `GET /wow/data-contract/registry`; 7 WNBA markets; `advisory_code` field; approval_ceiling: GATE_3_ELIGIBLE / GATE_3_ELIGIBLE_WITH_ADVISORY / WATCH / NO_APPROVAL (CONDITIONAL removed); Gate 3 untouched |
| WOW-PATCH-009 | 2026-07-02 | SHIPPED | Confidence Envelope — `POST /wow/confidence-envelope`; four independent axes: signal_confidence (HIGH/MEDIUM/LOW/NEGATIVE/UNKNOWN), data_confidence (COMPLETE_FRESH/COMPLETE_STALE/PARTIAL_FRESH/PARTIAL_STALE/LOW_SAMPLE/DATA_CONTRACT_INCOMPLETE/DATA_CONTRACT_PARTIAL/UNKNOWN), market_confidence (MARKET_CONFIRMED/MARKET_CONFLICT/MARKET_UNVERIFIED/MARKET_STALE/NOT_REQUIRED_FOR_THIS_GATE), approval_confidence (max ceiling MODEL_QUALIFIED_HOLD; FINAL_APPROVED/MONEY_QUALIFIED/MARKET_VERIFIED_HOLD reserved for orchestrator); accepts raw gate3_result + data_contract_result objects or individual fields; blocker_axes list; Gate 3 math untouched |
| WOW-PATCH-013A | 2026-07-02 | SHIPPED | Role-State Ledgers — `POST /wow/role-state/build`, `GET /wow/role-state/player`, `POST /wow/role-state/evaluate`; 11 sub-ledgers; role-change detection; WATCH_ELEVATED ceiling when ideal split <5 rows; KEY_TEAMMATE_CONTEXT_UNAVAILABLE advisory; 9/9 tests pass |
| WOW-PATCH-013B | 2026-07-02 | SHIPPED | Pick Lifecycle State Machine — 5 endpoints (create/transition/list/settle/pick); `wow_pick_lifecycle` + `wow_pick_lifecycle_log` tables; 19 valid states; terminal-state guard; `can_execute` always false in schema + every endpoint; 9/9 tests pass |
| WOW-PATCH-012 | 2026-07-02 | SHIPPED | Candidate Triage Score — `POST /wow/candidate-triage/score`; 8 weighted components (100pt max); bands PRIORITY_BUILD/WATCH_ELEVATED/WATCH/SCOUT/REJECT; 3 hard caps (DATA_CONTRACT_INCOMPLETE→45, GATE3_REJECT→30, MARKET_CONFLICT→-10); approval_confidence echoed unchanged; 9/9 tests pass |
| WOW-PATCH-014 | 2026-07-02 | SHIPPED | Unified Model Run Orchestrator — `POST /wow/model-run/orchestrate`; inline CE + triage + lifecycle upsert; no HTTP self-calls; `persist_lifecycle` flag; every row preserved; can_execute enforced at pick and top level; 9/9 tests pass |
| WOW-PATCH-PROV | 2026-07-02 | SHIPPED | Imported Ledger Provenance + Status Escalation Gate — `POST /wow/provenance/validate` + `/validate-batch`; 5-check pipeline (required-fields, summary-only, pasted-contradiction, MLB-pitcher-escalation, REASONED_NOT_MODELED); 7 new blocker tags; playable label gate; `can_execute` always false; 6/6 tests pass |
| WOW-PATCH-VQ-CACHE | 2026-07-02 | SHIPPED | Validation Queue Output + Ledger Cache — `POST /wow/validation-queue/build` (Approved/Validation Queue/Rejected boards, 10-field `confidence_debt`, upgrade_path per debt, banned-phrase scrub); `POST /wow/ledger-cache/upsert` + `GET /wow/ledger-cache/lookup` (player_game_log_cache + pitcher_game_log_cache, fresh/stale/no_cache detection); amends Section 29.2 run-level output; `can_execute` always false; 7/7 tests pass |
| WOW-PATCH-BINARY-EVENT-PURGE | 2026-07-02 | SHIPPED | Binary-event structural cap for `line == 0.5` props (e.g. MLB Hitter Hits LESS 0.5) — sport-agnostic, independent of gap%/hit-rate stats. Enforced at 3 points: `POST /wow/l10/gate3` caps `approval_ceiling` to WATCH (`blocker_code: BE1_BINARY_LINE_0PT5`, `binary_event_cap: true`); `_jf_slate_purge()` purges pre-scoring in the JF lane; `classify_prop()` in `jobs/wow_daily_scan.py` caps the main daily-scan classifier to Watch/Reject/Data Insufficient ahead of every scoring tier so it can never reach Model Qualified/Final Approved/Market Verified. Downgrade-only, fully transparent (blocker/purge_reason surfaced, never a silent drop); 329/329 existing gate_engine tests + 3 new ad-hoc classify_prop regression cases pass |
| WOW-PATCH-BINARY-EVENT-POSTSCAN-INVARIANT | 2026-07-02 | SHIPPED | Belt-and-suspenders follow-up to WOW-PATCH-BINARY-EVENT-PURGE. (1) Shared `normalize_line()`/`is_binary_event_line()` helper in `jobs/wow_daily_scan.py` used by all 3 enforcement points, extracts numeric line from OCR/string rows (`"0.5"`, `".5"`, `"0.50 Hits"`, `"LESS 0.5"`); (2) new post-scan invariant in `run_scan()` sweeps market_verified/final_approved_internal/model_qualified/conditional after classification and downgrades any surviving `line==0.5` card to watch (`binary_event_cap: true`, `can_execute: false`, `postscan_invariant_downgraded_from` tag) before counts/output are built — guards against any future path bypassing Gate 3/JF purge/classify_prop. Deliberately does NOT extend the hard cap to 1.5/2.5 lines (deferred to a future low-line volatility-tax patch). 329/329 gate_engine tests + new normalize_line/classify_prop/scan-invariant regression cases pass; live-verified via `/wow/l10/gate3` |
| WOW-PATCH-DATA-QUALITY-HOLD | 2026-07-06 | SHIPPED | Section 32 sub-tag — `DATA_QUALITY_HOLD` fires when internal projection falls back to average-only support (`l10_median` missing, only `l10_avg` used); default parent label `Watch`, ceiling `Model Qualified` only with independent market support, never terminal, always `block_power_flex: true`. Enforced in `classify_prop()` (`jobs/wow_daily_scan.py`, 4-tuple return) and new Gate 4b in `POST /final-lock` (`app.py`); `/final-lock` Gate 3 narrowed so the average-only path is actually reachable. Split `live_cushion_margin` (live) from new standalone `compute_retro_result_margin()` (retro QA only, no live call site). 6 new non-destructive `scan_results` columns. Live-verified via `/final-lock` + ad-hoc `classify_prop` script tests. |
