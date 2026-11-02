# WOW V17 — Independent NFL / NBA / NHL Restoration Audit

**Audit date:** 2026-11-01
**Auditor:** Freebuff (independent engineering verification)
**Repository:** `gregoryharper84-ship-it/WOW-Dashboard`
**Audited main SHA:** `3b4b3eeb8f870e5f3f3a19c9acfb7ac9c987a7f2` (commit `2026-11-01T09:21:04-06:00`, *"fix(nfl-data): refresh active-season prior-only feature table (#1562) (#1571)"*)
**Authority:** engineering verification only. `can_execute=false` throughout. No model weight, calibration coefficient, promotion, publication gate or terminal reduction was altered.

> This audit replaces an earlier report that was found to be materially inaccurate. Every number below is read from the authoritative sources named next to it. Nothing is estimated. Where a value could not be obtained, it is marked `UNVERIFIED` rather than filled in.

---

## 0. Sources actually used

| Source | What it establishes | Method |
|---|---|---|
| Git clone of `main` (fetch on 2026-11-01) | source revision, merged history, file contents | `cloud_git fetch` + `git` |
| Supabase project `wow-engine-validation` (`iczfhsmjrrafhvcpmqhr`, `us-east-1`, `ACTIVE_HEALTHY`) | model artifacts, certifications, prediction ledgers, forward grades, calibration health, Agent Runtime tables | read-only SQL |
| `artifacts/wow-engine` test suite at the audited SHA | current code behaviour under regression tests | `pytest` in a clean venv |

**Sources explicitly NOT available to this audit, therefore nothing is claimed about them:**

- Render service revisions (`wow-governed-probability-engine`, `wow-agent-worker`) — no Render credentials, no API access.
- Live authenticated `POST /score-pick-request` / `POST /score-prop` behaviour on the deployed host.
- GitHub PR open/merged state (#1612, #1618, #1959, #1964, #1974, #2318) — no GitHub API access. Only merged history reachable from `main` is verified.
- SIRT / Independent Verification outcomes.

Consequently **no route below can be reported at M3 or M4**, and none is.

---

## 1. Deliverable A — independent evidence inventory

### 1.1 Repository

| Field | Value |
|---|---|
| protected main SHA | `3b4b3eeb8f870e5f3f3a19c9acfb7ac9c987a7f2` |
| audit branch HEAD | identical (`git rev-list --left-right --count HEAD...FETCH_HEAD` → `0 0`) |
| merged PR verified in history | **#1571 = current main tip** (confirmed merged) |
| engine size | 636 modules under `artifacts/wow-engine/v17` |
| relevant suite state at this SHA | `619 passed, 4 skipped` for `-k "nfl or forward or team_event"` |

### 1.2 NFL moneyline — active champion (transactional truth)

Read from `public.wow_nfl_event_fitted_model_artifacts`:

| Field | Value |
|---|---|
| artifact_id | `7fee1209-0bed-4bd0-aa0f-fc7a881eff80` |
| model_family | `NFL_OUTRIGHT_WIN_LOGREG_V1` |
| model_artifact_version | `NFL_EVENT_LOGREG_PLATT_V1_5d603a873fbb` |
| lifecycle_state / active / promoted | `CHAMPION` / `true` / `true` |
| `probability_publishable` | **`false` — and structurally forced false, see §2.1** |
| training_rows | 787 (seasons 2021–2023) |
| calibration | 2024, n=285 (`NFL_PLATT_TIME_SPLIT_V1`) |
| validation | 2025, n=284: Brier **0.23115896846749356**, log loss 0.6529250857823151, AUC 0.6555023923444976, ECE₁₀ 0.04190465343842191 |
| baseline | Brier 0.24890796301353832, log loss 0.6909632983050226 |
| uncertainty_margin | 0.05591808310857731 |

### 1.3 NFL forward evidence (the certification cohort)

`public.wow_nfl_forward_shadow_grades` — exactly one model version, one row per event:

| model_version | graded events | window | mean p | hit rate | Brier | log loss |
|---|---|---|---|---|---|---|
| `NFL_EVENT_LOGREG_PLATT_V1_5d603a873fbb` | **67** | 2026-09-15 → 2026-10-30 | **0.6414028** | **0.5223881** | **0.2509912** | **0.6943293** |

Latest `public.wow_nfl_forward_calibration_health` row (`generated_at` 2026-11-01T15:56:51.615073Z):

```
graded_n                  = 67
minimum_forward_required  = 100
status                    = INSUFFICIENT_FORWARD_EVIDENCE
certification_recommendation = DO_NOT_CERTIFY_YET
blockers                  = ["FORWARD_GRADED_N_67_LT_100"]
brier = 0.2509912   ece = 0.1218783
```

### 1.4 NFL prediction ledger

| Metric | Value |
|---|---|
| rows | 1489 |
| distinct events | 81 |
| model versions | 2 |
| rows with `model_probability_publishable = true` | **1489 / 1489** |
| latest insert | 2026-11-01T16:46:10.415214Z |
| slate `2026-11-01` | **492 rows over 12 distinct events** |

Both `wow_nfl_event_predictions` and `wow_nfl_forward_shadow_grades` carry immutable triggers:
`BEFORE DELETE OR UPDATE ... EXECUTE FUNCTION wow_v17_reject_nfl_prediction_mutation()` /
`... wow_v17_reject_nfl_forward_grade_mutation()`.

### 1.5 NBA

`public.wow_basketball_team_event_certification` (2 rows total, both `capability_status = SHADOW`):

```
sport = NBA
model_artifact_version   = NBA_TEAM_EVENT_LOGISTIC_V1_20261031T173833Z
calibrator_version       = NBA_TEAM_EVENT_PLATT_V1_20261031T173833Z
capability_status        = SHADOW
reason_code              = CERTIFICATION_REPLAY_REQUIRED
promotion_attempted      = false
holdout: rows 1755, calibrated_brier 0.22711327660416086,
         calibrated_log_loss 0.6454525130789786, bias -0.0071857,
         publication_authorized = false
```

| Support table | Rows | State |
|---|---|---|
| `wow_basketball_team_event_model_artifacts` | 96+ (`NBA_TEAM_EVENT_LOGISTIC_V1_*`, one per daily refresh) | present |
| `wow_basketball_team_event_calibrators` | 189 | present |
| `wow_nba_training_games` | 8919 | settled corpus present |
| `wow_nba_pregame_feature_rows` | 8747 | present |
| **`wow_team_event_specialist_certifications`** | **0** | **registry empty** |

### 1.6 NBA props

`public.wow_prop_fitted_model_artifacts` for NBA: `POINTS`, `ASSISTS`, `REBOUNDS`, `PRA`, `POINTS_ASSISTS`, `POINTS_REBOUNDS`, `REBOUNDS_ASSISTS`, `FANTASY_SCORE` — all `lifecycle_state = CANDIDATE`, `active = false`, `promoted = false`, created 2026-09-21. No certified or promoted NBA prop artifact exists.

### 1.7 NHL

| Check | Result |
|---|---|
| public tables matching `%nhl%` or `%hockey%` | **0** |
| `wow_event_predictions` sports present | **MLB only** (1989 rows / 213 events, latest 2026-10-28) |
| NHL fitted artifacts | none |
| NHL certifications | none |
| code registration | `NHL` appears only in `DECLARED_TEAM_EVENT_SPECIALIST_IDENTITIES`, never in `CERTIFIED_TEAM_EVENT_SPORTS` |
| **NHL rows in `wow_v17_daily_run_row_detail` (2026-11-01 UTC)** | **45 rows / 5 events / 9 runs, all `row_status = HELD`** |
| **NBA rows in `wow_v17_daily_run_row_detail` (2026-11-01 UTC)** | **36 rows / 4 events / 9 runs, all `row_status = HELD`** |

Correction to this audit's first pass: NHL and NBA **do** reach the daily run ledger, as `HELD` rows. What is absent is every *modelling* surface — no fitted artifact, no calibrator, no certification row, no prediction row, and no NHL-specific table. The earlier phrasing "no NHL persistence surface" was too broad; the accurate statement is "no NHL model or prediction persistence, only fail-closed `HELD` run rows".

### 1.8 Shared runtime

| Table | Rows |
|---|---|
| `wow_agent_runs` | 0 |
| `wow_agent_jobs` | 0 |
| `wow_agent_job_outputs` | 0 |
| `wow_agent_run_candidates` | 0 |
| `wow_prop_action_canary_receipts` | 0 |
| `wow_prop_evidence_snapshots` | 21705 |

The Agent Runtime has produced **zero persisted runs, jobs, outputs or terminal decisions** in this store. This confirms the prior observation; it does not prove the legacy scoring paths are inactive, and it does not prove the deployed worker is unhealthy.

### 1.9 Corrections to the previous report

| Prior claim | Verdict | Actual evidence |
|---|---|---|
| NFL champion `..._74b9ef6e8fa8` | **FALSE** | champion is `..._5d603a873fbb`. (The `74b9ef6e8fa8…` string is the **training_dataset_hash of the NFL ANYTIME_TD prop artifact** in `migrations/20260920_nfl_direct_prop_governed_activation.sql`, not an NFL moneyline version. That is the likely origin of the error.) |
| NFL validation Brier 0.2184 | **FALSE** | `validation_brier_score = 0.23115896846749356` |
| 96 NFL rows / 12 events for Nov 1 | **FALSE** | 12 events is correct; rows are **492**, not 96 |
| NFL example `NFL_2026_WK09_BAL_CLE` | **not reproducible** | Week 9 is absent. Slate `2026-11-01` (week 8) is `2026_08_ARI_DAL, ATL_TB, BAL_BUF, CLE_PIT, IND_JAX, KC_DEN, LAC_LA, LV_NYJ, MIN_DET, NE_MIA, PHI_WAS, TEN_CIN` — **BAL_BUF**, not BAL_CLE |
| 1,463 NFL ML predictions | **stale** | 1,489 rows / 81 events as of 2026-11-01T16:46Z |
| PR #1612 / #2318 merged | **UNVERIFIED** | no GitHub API access; not reachable as merged commits in this history |
| 19 worker stages, 0 runs/jobs/decisions | **CONFIRMED** | 0 / 0 / 0 / 0 |
| Artifact `probability_publishable=false` vs prediction-level `true` is a flag disagreement | **INCOMPLETE** | the artifact column is `CHECK (probability_publishable = false)` — the two fields are not meant to agree (see §2.1) |

---

### 1.10 Reconciliation of the disputed daily-run row count

The two figures describe **different tables with disjoint scopes**. Both are correct.

**Figure A — `108 rows / 9 runs / 12 events`** (the coordination-side number). Exact SQL, executed 2026-11-01:

```sql
SELECT count(*) AS rows,
       count(DISTINCT run_id) AS runs,
       count(DISTINCT identity->>'official_event_id') AS events
FROM public.wow_v17_daily_run_row_detail
WHERE captured_at >= '2026-11-01 00:00:00+00'
  AND captured_at <  '2026-11-02 00:00:00+00'
  AND lane = 'MONEYLINE'
  AND identity->>'sport' = 'NFL';
-- rows 108 | runs 9 | events 12 | row_status: {HELD} (100%)
```

**Figure B — `504 rows / 12 events`** (this audit's number). Exact SQL:

```sql
SELECT count(*) AS total_rows,
       count(DISTINCT official_event_id) AS events,
       min(created_at), max(created_at)
FROM public.wow_nfl_event_predictions
WHERE requested_slate_date = '2026-11-01';
-- total_rows 504 | events 12 | created_at 2026-10-26T05:10:19Z .. 2026-11-01T17:03:16Z
```

That 504 was **492** when first measured earlier the same day, which is the source of the previous report's 492.

| Dimension | Figure A | Figure B |
|---|---|---|
| table | `wow_v17_daily_run_row_detail` | `wow_nfl_event_predictions` |
| grain | one row per (run, row_index) | one row per (prediction, selection) |
| unit | run attempts, all `HELD` | persisted model probabilities |
| window | `captured_at` within 2026-11-01 UTC | `requested_slate_date = 2026-11-01`, written from 2026-10-26 |
| denominator | 9 runs, 12 events | 504 rows, 12 events |

Both figures are **timestamped snapshots** taken at the original audit time (2026-11-01, prediction ledger read at 17:03Z). They are recorded as-is: a later re-read returning a larger prediction total must not silently overwrite them, and repeated prediction rows for one event must never be counted as independent games.

**They are not contradictory and neither is a defect.** Every one of the 108 run rows terminated `HELD`, which is consistent with — and further evidence for — the §2.1 finding that no NFL row can reach a publication terminal state. Note the run ledger is 100% `HELD` for NFL on that date; the prediction ledger nevertheless carries 504 publishable-flagged projection rows, because the prediction-level flag is written upstream of the certification gate.

Full-day sport breakdown of the same run ledger (2026-11-01 UTC): `TENNIS 213`, `SOCCER 126`, `NFL 108`, `NHL 45`, `NBA 36`, `NCAAB 14`, `CRICKET 8`, `MMA 2`.

---

## 2. Deliverable B — reproduced failures (first failing stage per route)

### 2.1 NFL-01 moneyline — *first failure is an incorrect implementation boundary, then genuine missing evidence*

**Objective:** `NFL-01` full-game moneyline winner.

**Stage 1 (implementation boundary, native):** `v17/nfl_daily_stage_receipts.py::_forward_certification_proven` requires

```python
and model.get("probability_publishable") is True
```

for the artifact row from `wow_nfl_event_fitted_model_artifacts`. That table carries

```sql
constraint wow_nfl_event_fitted_registry_not_probability_publication
  check (probability_publishable = false)
```

(`artifacts/wow-engine/20260913_nfl_governed_probability_publication.sql`; verified live in `pg_constraint`). The column is named *registry-not-probability-publication* on purpose: the registry is not the publication authority. The conjunct can therefore **never** be true, and the certification gate is **structurally unsatisfiable regardless of evidence**. Its `False` is indistinguishable from six other causes.

**Stage 2 (genuine missing evidence, native):** even with that conjunct removed, the gate requires `graded_n >= 100` and a `PASS` health snapshot. Actual: **67 graded events**, status `INSUFFICIENT_FORWARD_EVIDENCE`, blocker `FORWARD_GRADED_N_67_LT_100`.

**Stage 3 (genuine model-quality failure, native):** replaying the recorded thresholds in `v17/nfl_forward_shadow.py::calibration_health` against the actual 67-game cohort:

| Gate | Threshold | Actual | Verdict |
|---|---|---|---|
| Brier | `< 0.25` | 0.2509912 | **FAIL** |
| log loss | `< log 2 (0.6931472)` | 0.6943293 | **FAIL** |
| ECE | `<= 0.12` | 0.1218783 | **FAIL** |
| calibration bias | `|bias| <= 0.08` | **+0.1190148** (mean p 0.6414028 − hit 0.5223881) | **FAIL** |
| lower-bound reliability | `lower_gap >= -0.03` | not independently recomputed | UNVERIFIED |

So the deep root cause is **not plumbing at all**: the champion forecasts 0.641 for its selected side and wins 0.522. Reaching 100 games would not certify this artifact; it would surface a FAIL.

**Reconciliation of the cohort itself:** 68 NFL events have started since grading began; 67 graded; the single ungraded started event is `2026_05_NYG_WAS`, settled **27–27 (`tie = true`, `home_win = false`, locked 2026-10-12)** and excluded by `v17/nfl_forward_shadow.py::grade_forward_predictions` by design. **No ingestion defect exists.** Exactly 0 eligible events were lost.

```text
objective_id            : NFL-01
first_failing_stage      : forward certification (post-refresh, pre-publication)
native_failure_codes     : NFL_FORWARD_CERTIFICATION_UNPROVEN (collapsed)
                           FORWARD_GRADED_N_67_LT_100 (health)
                           wow_nfl_event_fitted_registry_not_probability_publication (CHECK)
source_file_and_function : v17/nfl_daily_stage_receipts.py::attach_nfl_daily_stage_receipt
                           -> _forward_certification_proven
concurrent_source       : v17/nfl_forward_shadow.py::calibration_health
current_source_sha       : 3b4b3eeb8f870e5f3f3a19c9acfb7ac9c987a7f2
affected_runtime         : wow-governed-probability-engine (NFL publication bridge)
existing_fix_or_PR       : none merged; this audit supplies the diagnostic repair (§3)
remaining_dependency      : 33 more eligible graded events + 4 passing accuracy gates
                           + a governed artifact-publication decision
```

### 2.2 NFL-02…05 player props

Artifacts are present and `PROSPECTIVE_CERTIFIED`, `active`, `promoted`:

| stat | model_artifact_version | lifecycle |
|---|---|---|
| PASSING_YARDS | `NFL_PASSYDS_ROLLING_FITTED_V1_2026_09_20` | PROSPECTIVE_CERTIFIED |
| RUSHING_YARDS | `NFL_RUSHYDS_ROLLING_FITTED_V1_2026_09_20` | PROSPECTIVE_CERTIFIED |
| RECEIVING_YARDS | `NFL_RECYDS_ROLLING_FITTED_V1_2026_09_20` | PROSPECTIVE_CERTIFIED |
| ANYTIME_TD | `NFL_ATTD_ROLLING_FITTED_V1_2026_09_20` | PROSPECTIVE_CERTIFIED |

All carry `probability_publishable = false` under `constraint wow_prop_fitted_never_publish check (probability_publishable = false)` — the same registry-is-not-publication pattern as §2.1. First failing stage for these four is therefore **publication authority, not model capability**. Their fitted evidence is genuine (e.g. ATTD Brier 0.1618 vs naive L10 0.1738, holdout 4318 rows, holdout OOD rate z>6 = 0.0, CC-BY-4.0 nflverse provenance bundle hash recorded). No `M3`/`M4` evidence exists and none was verified.

### 2.3 NBA-01 moneyline

`v17/nba_current_pregame_scorer.py::prepare_nba_current_pregame_scorer_receipt` fails closed at three independent points, in order:

1. `CERTIFIED_TEAM_EVENT_SPORTS.get("NBA")` is `None` (`v17/team_event_capability_manifest.py`; the dict contains **MLB only**) → `MODEL_UNAVAILABLE` / `NBA_CONTROLLING_SPECIALIST_NOT_CERTIFIED`.
2. `wow_team_event_specialist_certifications` has **0 rows**, so `resolve_certification(client, sport="NBA", …)` cannot return a certified NBA specialist → `TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED`.
3. Even past both, the module terminates at `MODEL_INPUTS_INSUFFICIENT` / `NBA_DYNAMIC_BOUNDS_POLICY_NOT_APPROVED` — bounds semantics are unimplemented by design.

Database capability is `SHADOW` with `reason_code = CERTIFICATION_REPLAY_REQUIRED` and `publication_authorized = false` on a 1755-row untouched holdout (bias −0.0072, Brier 0.2271). **The blocker is independent certification, not a missing allow-list entry.** Adding NBA to the allow-list would be self-certification and is prohibited.

```text
objective_id            : NBA-01
first_failing_stage      : controlling-specialist certification gate
native_failure_codes     : NBA_CONTROLLING_SPECIALIST_NOT_CERTIFIED
                           SPORT_SPECIFIC_TEAM_EVENT_ADAPTER_NOT_REGISTERED
                           NBA_DYNAMIC_BOUNDS_POLICY_NOT_APPROVED
source_file_and_function : v17/nba_current_pregame_scorer.py::prepare_nba_current_pregame_scorer_receipt
affected_runtime         : team-event bridge (NBA unregistered)
remaining_dependency     : certified NBA specialist receipt + approved NBA bounds policy
```

### 2.4 NBA-02 points

`wow_prop_fitted_model_artifacts` NBA/POINTS is `CANDIDATE`, not active, not promoted, publishable false. The shared router returns `PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE` for NBA (`prop_auto_hydration.py`, `prop_auto_hydration_router.py`, `pick_request_runtime_core.py`). No calibrated lower bound exists to certify.

### 2.5 NHL-01 moneyline and NHL-02 shots on goal

- `NHL` is absent from `CERTIFIED_TEAM_EVENT_SPORTS`; only declared as a routing identity.
- No NHL fitted artifact, calibrator, certification, or entry in `wow_nfl_event_predictions`/`wow_event_predictions` exists in the store. NHL does appear in `wow_v17_daily_run_row_detail` (45 rows / 5 events on 2026-11-01, all `HELD`), which is fail-closed evidence, not capability.
- No `%nhl%`/`%hockey%` table exists at all.
- `NHL` required inputs (`goalie_status`, `home_elo`, `home_goalie_sv_pct`, `home_pp_pct`, `calibration_artifact`, …) have no hydrator producing them.

NHL-02 additionally has **no fitted SOG artifact in `wow_prop_fitted_model_artifacts`** (only MLB, NBA-candidate, NFL, WNBA rows exist). The correct status is `BLOCKED — NHL_SOG_FITTED_ARTIFACT_AND_DATA_UNAVAILABLE`.

NHL moneyline is **not** held because SOG is missing; it is blocked independently.

---

## 3. Deliverable C — engineering change implemented

A planned patch is not code; this is code, with tests, at the audited SHA.

**Change 1 — `artifacts/wow-engine/v17/nfl_daily_stage_receipts.py`**

- Added `_forward_certification_diagnosis(db, row) -> (proven, blockers, evidence)`. It evaluates **exactly the same conjunction** as before, one conjunct per typed blocker, and returns the same boolean.
- `_forward_certification_proven` is retained as a thin boolean view, so every existing caller and assertion is unchanged.
- The stage receipt now carries `forward_certification_blockers` and `forward_certification_evidence` (graded events, minimum, health `graded_n`, health status, model version, artifact id), and the typed blockers join the row `blockers`.
- `NFL_FORWARD_CERTIFICATION_UNPROVEN` is still emitted, for backward compatibility with existing acceptance assertions.

Typed blockers now possible: `NFL_FORWARD_CERTIFICATION_QUERY_FAILED`, `NFL_FORWARD_CERTIFICATION_REGISTRY_SHAPE_INVALID`, `NFL_FORWARD_MINIMUM_COHORT_INVALID`, `NFL_FORWARD_GRADE_COHORT_QUERY_FAILED`, `NFL_FORWARD_GRADED_EVENTS_BELOW_MINIMUM`, `NFL_FORWARD_COHORT_MODEL_VERSION_MISMATCH`, `NFL_FORWARD_COHORT_ENUMERATION_INCOMPLETE`, `NFL_ARTIFACT_NOT_ACTIVE`, `NFL_ARTIFACT_NOT_PROMOTED`, `NFL_ARTIFACT_PUBLICATION_FLAG_NOT_GRANTED`, `NFL_REGISTRY_PUBLICATION_AUTHORITY_CONFLICT`, `NFL_FORWARD_HEALTH_COHORT_MALFORMED`, `NFL_FORWARD_HEALTH_GRADED_N_BELOW_MINIMUM`, `NFL_FORWARD_HEALTH_STATUS_NOT_PASS`, `NFL_FORWARD_RECOMMENDATION_NOT_PASS`, `NFL_FORWARD_HEALTH_BLOCKERS_PRESENT`.

**Change 2 — `artifacts/wow-engine/tests/test_v17_nfl_forward_certification_diagnosis.py`** (new, 7 tests) pins:

- the observed 67-of-100 cohort is reported as a **count**, not a flag;
- the schema-locked artifact publication column is reported as a **policy conflict** and never "repaired";
- a hypothetically healthy 140-game cohort **still cannot promote itself**;
- diagnosis and boolean view never diverge, and `proven is (blockers == [])`;
- a `minimum_forward_required < 100` policy fails closed;
- a missing health row is a registry-shape blocker, not `MODEL_UNAVAILABLE`;
- the receipt surfaces the new fields and contains no `can_execute=True` path.

**Change 1b — forward cohort pagination (P0 correction `63c88924db53ed34d8dfd264878b4aca7a70df8b`)**

- `_observed_version_cohort` replaced unordered `.range()` offset paging with keyset paging: `.eq("model_version", …)` + `.gt("official_event_id", cursor)` + `.order("official_event_id")` + `.limit(_GRADE_PAGE_SIZE)`, bounded by `_GRADE_MAX_PAGES`.
- `official_event_id` is `UNIQUE` in `wow_nfl_forward_shadow_grades` (`migrations/20260915_nfl_forward_shadow_calibration.sql`), so ordering by it is a total order over the version-filtered rows; the table also carries the immutability trigger, so rows are inserted only. A grade inserted mid-read sorts after the cursor and is either counted on a later page or excluded from a read that already ended, so the reported count can only be a lower bound, never inflated. `max(page_ids)` keeps progress monotonic even if a response ignored the requested order.
- An enumeration that reaches the page cap, or any page containing a row with no usable event key, now returns `graded_events_exact=false` and emits `NFL_FORWARD_COHORT_ENUMERATION_INCOMPLETE`. An inexact enumeration cannot prove the gate, so a truncated or uncountable read cannot certify.
- The 100-game minimum, the boolean decision, `forward_certification_status`, the terminal label and `can_execute=false` are unchanged. No unbounded scan, no new publication service.

**Change 3 — NCAAF handoff fixture clock (test-only, P0 correction `63c88924db53ed34d8dfd264878b4aca7a70df8b`)**

- `artifacts/wow-engine/test_ncaaf_event_feature_handoff.py` passed an explicit pregame `now` but no `clock`; `score_current_event_snapshot`'s post-snapshot recheck reads `clock()` or the runner wall clock, never `now`. Against the fixture's fixed `2026-11-01T20:00:00Z` kickoff, three tests therefore failed as soon as CI ran after kickoff (`wow-engine-verify` shard 1: 3 failed / 2615 passed).
- The fixture now supplies one deterministic pregame instant as both `now` and `clock`, and a new parametrized regression pins that a second clock at or after kickoff still holds as `NCAAF_EVENT_NOT_PREGAME` without invoking the scorer.
- No production NCAAF behaviour, freshness enforcement or event classification changed. Ownership: this file belongs to NCAAF Engineering (#1951); the test-only fix was included in the NFL diagnostic branch because the exact-head acceptance gate cannot pass without it. **NCAAF owner acceptance in #1951 is still outstanding and was not obtainable from the correcting environment.**

**What this change deliberately does NOT do:** it does not set any publication flag, does not remove a gate, does not touch coefficients, margins, calibration or bounds, does not register NBA or NHL, and does not change any route's terminal outcome. `can_execute=false` and `V17_TERMINAL_REDUCER` are untouched.

**Test results (actual, this audit):**

```
pytest tests/test_v17_nfl_forward_certification_diagnosis.py \
       tests/test_v17_nfl_calibration_producer_receipt.py \
       tests/test_v17_nba_current_pregame_scorer.py \
       tests/test_v17_team_event_capability_manifest.py \
       test_v17_nfl_forward_settlement_refresh.py \
       test_nfl_dfs_calibration_evidence.py \
       test_v17_nfl_governed_probability_publication.py
→ 70 passed

pytest tests test_nfl_dfs_calibration_evidence.py -k "nfl or forward or team_event"
→ 619 passed, 4 skipped, 2098 deselected
```

The block above is the original audit snapshot and is retained unchanged as historical record.

**Re-measured at P0 correction commit `63c88924db53ed34d8dfd264878b4aca7a70df8b`** (clean venv built from `artifacts/wow-engine/requirements.txt`; the earlier counts were taken before the NCAAF fixture clock repair and the forward-cohort keyset change):

```
# exact-head targeted suites (5 NFL/NL modules + NCAAF handoff)
pytest test_ncaaf_event_feature_handoff.py \
       tests/test_v17_nfl_forward_certification_diagnosis.py \
       tests/test_v17_nfl_calibration_producer_receipt.py \
       tests/test_v17_nfl_stage_receipt_forward_integration.py \
       tests/test_v17_nfl_daily_stage_receipts.py \
       test_v17_nfl_forward_shadow.py
→ 72 passed

# the exact shard that failed in wow-engine-verify (scripts/wow_rapid.py --index 1 --total 4)
pytest -q --tb=short -rs <231 files>
→ 2618 passed, 7 skipped, 1 failed

pytest tests test_nfl_dfs_calibration_evidence.py -k "nfl or ncaaf or forward or team_event"
→ 877 passed, 4 skipped, 1866 deselected
```

The single shard failure is `test_v17_factory_status_js.py::test_read_only_factory_status_javascript_contract`, classified `CI_ENVIRONMENT_GAP`: the local runner has Node v26, whose TAP reporter prints `ℹ pass 7`, while the assertion and CI's Node expect `# pass 7`. The Node suite it shells out to reported `pass 7 / fail 0`. That file and its Node fixture are unmodified by this correction, so the failure is not attributable to it.

**Regression risk:** low. The change is additive to the returned payload and preserves the exact boolean decision; the only shared surface is `attach_nfl_daily_stage_receipt`'s output dict, consumed by `v17/nfl_team_event_publication.py` (which reads named keys) and by the two NFL test modules, both green.

**Prior strengths preserved:** unscored/held rows still downgrade to `MODEL_QUALIFIED_HOLD`; a valid immutable fitted probability is still retained when calibration or refresh proof is missing; SQL governance and the final-refresh re-query are still independent gates.

---

## 4. Deliverable D — live model acceptance

**None achieved. No route has an authentic positive current-event production receipt in this audit.**

For transparency, the closest available authentic NFL artifacts, which are *persisted evidence*, not production acceptance:

| Item | Value |
|---|---|
| slate | `2026-11-01`, week 8, 12 events, 492 prediction rows |
| example official event | `2026_08_BAL_BUF` (41 rows, all `model_probability_publishable = true`) |
| example event with more rows | `2026_08_ARI_DAL` (41 rows, 2026-11-01T16:45:34Z) |
| controlling artifact | `7fee1209-0bed-4bd0-aa0f-fc7a881eff80` / `NFL_EVENT_LOGREG_PLATT_V1_5d603a873fbb` |
| per-row calibrated probability / lower bound | not read in this audit — `UNVERIFIED`, not invented |

I did not read a single row's `calibrated_selection_probability` / `calibrated_selection_lower_bound`, so no such figure is reported. Negative-case acceptance (§16) was **not** exercised against a live service; the equivalent fail-closed behaviour is asserted by the green suites listed above.

---

## 5. Deliverable E — production and independent acceptance

| Requirement | Status |
|---|---|
| authorized merge | **not performed** — change is uncommitted in the audit worktree; `main` is protected and no merge was authorized |
| Render deployment + SHA | **UNVERIFIED** — no Render access |
| production Action result | **UNVERIFIED** |
| persisted prediction record | verified (ledger evidence above) but **not** an authenticated Action receipt |
| terminal reducer decision | **not observed** for any current row |
| SIRT challenge | **not run** |
| Independent Verification | **not obtained** |

---

## 6. Final scorecard

| Route | M1 Model | M2 Scoring | M3 Production | M4 Independent | Exact blocker | Evidence |
|---|---|---|---|---|---|---|
| NFL ML | PASS | PASS | UNVERIFIED | NO | `SAFE_HOLD`: forward cohort 67/100; 4 of 5 accuracy gates failing (Brier 0.25099, logloss 0.69433, ECE 0.12188, bias +0.11901); artifact publication column CHECK-locked false | §1.2, §1.3, §2.1 |
| NFL Passing | PASS | UNVERIFIED | UNVERIFIED | NO | `SAFE_HOLD`: publication authority only (`wow_prop_fitted_never_publish`) | §2.2 |
| NFL Rushing | PASS | UNVERIFIED | UNVERIFIED | NO | `SAFE_HOLD`: same | §2.2 |
| NFL Receiving | PASS | UNVERIFIED | UNVERIFIED | NO | `SAFE_HOLD`: same | §2.2 |
| NFL Anytime TD | PASS | UNVERIFIED | UNVERIFIED | NO | `SAFE_HOLD`: same | §2.2 |
| NBA ML | FAIL (`SHADOW`, replay required) | NO | NO | NO | `BLOCKED`: no certified specialist; empty certification registry; unapproved bounds policy | §1.5, §2.3 |
| NBA POINTS | FAIL (`CANDIDATE`) | NO | NO | NO | `BLOCKED`: candidate uncalibrated; `PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE` | §1.6, §2.4 |
| NHL ML | FAIL (none) | NO | NO | NO | `BLOCKED`: no artifact, no calibration, no NHL prediction data (run rows exist but are 100% `HELD`), input families unproduced | §1.7, §1.10, §2.5 |
| NHL SOG | FAIL (none) | NO | NO | NO | `BLOCKED — NHL_SOG_FITTED_ARTIFACT_AND_DATA_UNAVAILABLE` | §1.7, §2.5 |

Independent lanes:

```text
BACKEND_RUNTIME          = UNVERIFIED (no Render access; Agent Runtime 0/0/0/0 confirmed)
MODEL_CAPABILITY         = PARTIAL (NFL fitted + calibrating + scoring; NBA SHADOW; NHL absent)
REPOSITORY_GOVERNANCE    = VERIFIED at 3b4b3eeb8f870e5f3f3a19c9acfb7ac9c987a7f2
LIVE_GPT_EDITOR_SYNC     = UNVERIFIED
```

```text
PROGRAM_STATUS = WORKING_NOT_COMPLETE
can_execute = false
```

### Exact missing dependencies, with owners

| Route | Missing item | Owner |
|---|---|---|
| NFL ML | 33 additional eligible graded events; a champion whose forward Brier < 0.25, ECE ≤ 0.12, bias ≤ 0.08; a governed artifact-publication decision (the column is intentionally non-publishable) | WOW model governance + Independent Verification |
| NFL props | publication authorization semantics for `wow_prop_fitted_model_artifacts` | WOW model governance |
| NBA ML | immutable `wow_team_event_specialist_certifications` receipt for NBA at a replay-verified artifact; an approved NBA dynamic-bounds policy | Independent Verification + model governance |
| NBA POINTS | certified calibrator with uncertainty bounds for the POINTS candidate | model governance |
| NHL ML | licensed pregame goalie / rest-travel / special-teams / Elo inputs; fitted artifact; calibrator; certification | data acquisition + model governance |
| NHL SOG | licensed point-in-time player SOG history; fitted distribution; calibrator | data acquisition + model governance |
