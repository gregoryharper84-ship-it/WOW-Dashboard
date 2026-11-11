# NFL certification checklist — 2026-11-11

Readiness inventory for the NFL team-event moneyline lane. This is not a
certification decision, an approval, or a publication receipt.

```text
certification_recommendation = DO_NOT_CERTIFY_YET
probability_publishable      = false
automatic_certification      = false
automatic_promotion          = false
can_execute                  = false
supported_regimes            = [REGULAR_SEASON]
```

Gates are ordered. A later gate cannot compensate for an earlier one, and no
gate may be cleared by lowering a threshold or promoting an artifact from this
document.

---

## 1. Where the lane stands

Live database values must be read, not assumed. The status below is what the
repository proves; `unproven` means the code demands evidence that no checked-in
artifact demonstrates.

| Item | Requirement | Current status | Read from |
|---|---|---|---|
| Forward cohort | `graded_n >= 100` | **unverified here** — last recorded observation was 95 / 100 (5 short), without a timestamp in this document | `wow_nfl_forward_calibration_health` (`graded_n`, `generated_at`) |
| Nov 15 slate | real slate size known before kickoff, every row pregame-publishable | **unverified here** — the slate size and the up-to-107 projection come from a same-day preflight run, not from this document | `wow_nfl_event_predictions` + `POST /internal/v17/nfl-forward-cohort-preflight` |
| Metric gate | Brier, log loss, ECE, bias, lower-bound gap | not evaluated until `graded_n >= 100` | `calibration_health()` |
| Per-row stage proof | exact readback, calibration producer receipt, final refresh | contract present; per-row proof unproven | `v17/nfl_daily_stage_receipts.py` |
| Fitted artifact | `active`, `promoted`, `calibrator_id`, `certification_id`, `lifecycle_state IN ('PROSPECTIVE_CERTIFIED','CHAMPION')` | unproven | `wow_nfl_event_fitted_model_artifacts` |
| Calibrator | active + promoted, `validation_status='PASS'`, `health_status='PASS'`, NFL `OUTRIGHT_WINNER` | unproven | `wow_calibrators` |
| Governed catalog | NFL row `CERTIFIED` + independent verification `PASS` + both hashes | unproven | `wow_team_event_specialist_certifications` |
| Runtime capability | certified artifact + ready calibrator | `MODEL_UNAVAILABLE` until both exist | `wow_v17_refresh_nfl_event_capability()` |
| Health probe | live champion loads through `load_champion_model` | `NFL_ACTIVE_PROMOTED_CHAMPION_NOT_PROVEN` | `v17/all_sport_capability_readiness.py` |
| Build classification | — | `FORWARD_EVIDENCE_PENDING` | `v17/capability_build_registry.py` |

Any gap is arithmetic, not a reporting artifact: the cohort counts only settled
games whose immutable pregame prediction was created before kickoff with
`model_package_valid`, `provenance_complete` and
`model_probability_publishable` all true. Ties are excluded, and an outcome row
without `home_win` is skipped. A prediction written after kickoff can never be
added, because `wow_nfl_event_predictions` is immutable and late rows are
dropped by `select_canonical_forward_predictions`.

Both figures in the table are observations, not assertions. Read `graded_n`
together with the `generated_at` of the health row it came from, and take the
slate size from a preflight run on the same gameday. A count whose timestamp is
unknown is unverified, and neither figure in this document should be quoted as
current state.

### 2026-11-11 read-only validation receipt — not production acceptance

A read of the **validation project** (`wow-engine-validation`, Supabase project
`iczfhsmjrrafhvcpmqhr`) found the latest
`wow_nfl_forward_calibration_health` row generated at
`2026-11-10T19:54:14.238636+00:00` with `graded_n=95`,
`minimum_forward_required=100`, `INSUFFICIENT_FORWARD_EVIDENCE`,
`DO_NOT_CERTIFY_YET`, and `can_execute=false`. A separate read-only
canonical-prediction/current-settlement reconciliation found 95 currently
qualifying distinct grades and 95 persisted distinct grade IDs, with no
identity drift at that read.

The 2026-11-15 US/Eastern prediction window contained 48 immutable prediction
records for 12 distinct official-event identifiers; all 12 had at least one
eligible pregame prediction under the inspected publication-flag and
probability-bound conditions. This is a **conditional projection**, not a
settled-game grade or proof that the official expected slate is complete.
The independently accepted `expected_slate_games` input and the production
database identity are not established by these validation-project reads.

If and only if all 12 events settle with qualifying non-tie outcomes and
authenticated expected-slate coverage matches, the maximum projected count
from this snapshot is 107. Certification and publication remain on HOLD;
do not treat this observation as a live production receipt.

---

## 2. Gates in order

### G1 — Forward cohort reaches 100

- **Requires:** enough additional qualifying settled games to bring the live
  `graded_n` to 100 (last recorded observation: 5 short).
- **Enforced by:** `v17/nfl_forward_shadow.py` (`MIN_FORWARD_GRADED`,
  `select_canonical_forward_predictions`, `grade_forward_predictions`).
- **Clears when:** `graded_n >= 100` in the latest
  `wow_nfl_forward_calibration_health` row.
- **Blocker today:** `FORWARD_GRADED_N_<n>_LT_100`.
- **Operational note:** predictions for a slate must exist *before* kickoff.
  The read-only preflight in PR #2599 lists any slate game that cannot count,
  with its reason, hours before the first kickoff. It reports
  `ELIGIBLE_PREGAME` for pregame eligibility (not a grade), and withholds
  `PROJECTED_TO_REACH_MINIMUM` when slate coverage is unverified or the
  prediction ledger would make the grader abort.

### G2 — Calibration-health metrics pass on the same cohort

- **Requires:** Brier `< 0.25`; log loss `< log 2`; ECE `<= 0.12`;
  `|calibration_bias| <= 0.08`; lower-bound reliability gap `>= -0.03`.
- **Enforced by:** `calibration_health()` in `v17/nfl_forward_shadow.py`.
- **Clears when:** the latest health row has `status='PASS'`,
  `certification_recommendation='FORWARD_EVIDENCE_PASS'`, `blockers=[]`.
- **Blocker codes:** `FORWARD_BRIER_NOT_BELOW_0_25`,
  `FORWARD_LOG_LOSS_NOT_BELOW_LOG2`, `FORWARD_ECE_ABOVE_0_12`,
  `FORWARD_CALIBRATION_BIAS_ABOVE_0_08`,
  `FORWARD_LOWER_BOUND_RELIABILITY_FAILED`.

Reaching 100 is necessary, not sufficient. No gate here may be retuned to make
a cohort pass.

### G3 — Per-row stage receipt is proven

For each governed prediction, `attach_nfl_daily_stage_receipt` must prove, from
an exact immutable readback:

- exact one prediction row, matching `event_prediction_id`, `official_event_id`,
  `event_key`, `requested_slate_date`, `selected_participant`,
  `source_snapshot_id`, `score_snapshot_id`, `model_artifact_id`,
  `model_version`, `calibration_method`, `calibration_version`;
- `event_start_time_utc`, `source_snapshot_timestamp` and `model_timestamp`
  identical to the request/response, and `model_timestamp >= latest material
  update`;
- publication flags true and `can_execute is False`;
- `0 <= lower <= p <= upper <= 1`, with the selected side's upper bound, never
  the opposite side's;
- `calibration_health_status='PASS'`, a calibration method/version, an
  uncertainty method, and `calibration_training_n > 0`;
- an actual `calibration_operation_receipt` from
  `nfl_event_model_v17.score_feature_row` with a matching evidence hash;
- a final refresh with `source='NFL_CANONICAL_SCHEDULE_REQUERY'`,
  `final_refresh_status='PASS'`, `reasons=[]`,
  `probability_invalidated=False`, `rerun_required=False`,
  `refresh_source_snapshot_id` present, matching model timestamp and
  `scored_source_snapshot_id == source_snapshot_id`, and
  `model_timestamp <= checked_at < kickoff`.

- **Enforced by:** `v17/nfl_daily_stage_receipts.py`.
- **Blocker codes:** `NFL_STAGE_PROOF_*`,
  `NFL_DYNAMIC_CALIBRATION_PRODUCER_RECEIPT_MISSING`,
  `NFL_FINAL_REFRESH_SOURCE_PROOF_UNPROVEN`, `NFL_STAGE_PROOF_UNPROVEN`,
  `NFL_UPSTREAM_TERMINAL_UNPROVEN`.
- **Note:** stage proof does not certify accuracy. A passing stage receipt on
  fewer than 100 real grades still returns `NFL_FORWARD_CERTIFICATION_UNPROVEN`.

### G4 — Exact-version forward evidence (pre-promotion)

- **Requires:** the latest health row `PASS` / `FORWARD_EVIDENCE_PASS` / no
  blockers / `graded_n >= minimum` with `minimum >= 100`, **and** at least that
  many **distinct** grades carrying this prediction's exact `model_version`.
- **Enforced by:** the cohort checks in `_forward_certification_proven()`
  (`v17/nfl_daily_stage_receipts.py`) and `calibration_health()` in
  `v17/nfl_forward_shadow.py`.
- **On hold:** the health row's metric fields (Brier, log loss, ECE,
  calibration bias, lower-bound gap) are one **global** snapshot. The code
  proves the exact-version *count* and requires that global row to say `PASS`;
  it does not recompute those metrics for a single model version. G4 stays on
  **HOLD** until independently verified, exact-version health evidence shows
  that this same version passes **every G2 metric**. Merely producing the
  version-scoped evidence is insufficient; a mixed-version global `PASS` cannot
  substitute for passing version-specific metrics.
- **Blocker today:** `NFL_FORWARD_CERTIFICATION_UNPROVEN`.
- **Note:** A mixed-version global `PASS`, even with enough exact-version
  grades, does not prove that this version passes the G2 metrics.

**Evidence-production and ordering HOLD.** The existing NFL scorer needs an
active, promoted champion and calibrator before it produces publishable rows.
No independently authorized pre-G5 producer of exact-version prospective grades
is verified here. Existing immutable pregame rows may supply G4 evidence only
when their production authority, model version and current settlement are
independently verified; the scorer cannot be assumed to bootstrap G4 before G5.
G5 is a separate governed promotion decision, not permission for an agent to
activate an artifact to produce missing evidence. The full
`_forward_certification_proven()` receipt-time predicate also requires the
artifact to be active, promoted and publishable, so G4/G5 are not proven as a
working pre-promotion sequence. Keep G4 and promotion/publication on HOLD until
an authorized, independently reviewed evidence-collection path and passing
exact-version G2 health are demonstrated.

### G5 — Artifact and calibrator promotion (governance decision)

- **Artifact must satisfy** `wow_nfl_event_certified_model_artifact`:
  `active = true`, `promoted = true`,
  `lifecycle_state IN ('PROSPECTIVE_CERTIFIED','CHAMPION')`,
  `calibrator_id IS NOT NULL`, `certification_id IS NOT NULL`, matched on
  `feature_schema_version = 'NFL_EVENT_PREGAME_PRIOR_V1'`.
- **Calibrator must satisfy** `wow_v17_refresh_nfl_event_capability()`:
  `sport='NFL'`, `market_family='OUTRIGHT_WINNER'`, model family matching the
  artifact, `active`, `promoted`, `validation_status='PASS'`,
  `health_status='PASS'`.
- **Clears when:** both exist and their IDs are cross-referenced.
- **Nothing in the code sets these flags automatically.** This is an explicit
  review/release decision, not an evidence-collection step.

### G6 — Runtime capability refresh

- **Requires:** G5 complete.
- **Enforced by:** `wow_v17_refresh_nfl_event_capability()` (triggered by
  artifact lifecycle changes and by the capability refresh path).
- **Clears when:** `wow_runtime_capabilities` for `NFL_EVENT_PROBABILITY` reaches
  `status='AVAILABLE'` with `terminal_label_if_scored_now='MODEL_QUALIFIED_HOLD'`
  (never a publication or execution grant).
- **Until then:** the terminal label is `MODEL_UNAVAILABLE`, and the live
  readiness probe keeps reporting `NFL_ACTIVE_PROMOTED_CHAMPION_NOT_PROVEN`.

### G7 — Governed catalog certification and route replay

- **Requires:** a `wow_team_event_specialist_certifications` row for
  `sport='NFL'`, `league_scope='NFL'` with `certification_status='CERTIFIED'`,
  `independent_verification_status='PASS'`, `probability_publishable=true`, and
  non-empty `artifact_sha256` and `calibration_sha256`.
- **Read-only preflight:** `scripts/v17_certification_promotion_preflight.sql`
  reports, in order, `DO_NOT_CERTIFY_YET` →
  `TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED` →
  `CERTIFICATION_REPLAY_REQUIRED` → `RECEIPT_PRESENT_REQUIRES_ROUTE_REPLAY`.
  Every row keeps `preflight_authorizes_publication=false` and
  `can_execute=false`.
- **Replay:** `v17/team_event_certification_replay.py` consumes exact
  candidate-bound evidence receipts (`wow_d1_candidate_artifacts`,
  `wow_d1_certification_evidence_receipts`) and never certifies, promotes,
  publishes, or ranks on its own.

---

## 3. Decisions no code or agent may make

1. Promoting the fitted artifact or the calibrator (G5).
2. Writing the governed certification row (G7).
3. Extending `supported_regimes` beyond `REGULAR_SEASON`, which needs separately
   collected postseason evidence.
4. Accepting a calibration-health PASS on a cohort that mixed model versions.
5. Replacing any missing evidence with market-implied, qualitative, or synthetic
   probability.

---

## 4. Audit gates still open

From `v17/NFL-CERTIFICATION-AUDIT-2026-09-15.md`, with the current code state:

| Gate | Status | What still closes it |
|---|---|---|
| G-NFL-07 dynamic calibration | PARTIAL | Per-prediction Platt operation is receipt-proven; no dynamic recalibration lane comparable to MLB exists |
| G-NFL-08 calibrated bounds | PARTIAL | Bounds remain a holdout error margin, explicitly not a confidence interval |
| G-NFL-10 failure-regime / unconditional probability | NOT PROVEN | A failure-path contract that alters the unconditional probability, with tests |
| G-NFL-13 probability claim auditor | NOT PROVEN | Exercise the auditor against real NFL output, not only other sports |
| G-NFL-15 event mutex / final refresh | PARTIAL | The schedule re-query receipt is enforced per row; independent mutex proof for NFL is absent |
| G-NFL-16 live cross-over smoke | PENDING | Re-verify after the next Render deploy of the current SHA |
| G-NFL-11/12 forward validation + health | IMPLEMENTED, EVIDENCE PENDING | G1 and G2 above |

---

## 5. Other NFL lanes (each its own evidence program)

| Lane | Gate | Status |
|---|---|---|
| Core props — passing, rushing, receiving, anytime TD | `PHASE_B_MIN_N = 200` settled per exact route, then candidate review and reviewed production registration | Collection only; hourly producer at `/internal/v17/nfl-prop-forward-evidence/acquire` |
| Spread | Class C challenger canary; `probability_publishable=false` | Context challenger holds on `NFL_SPREAD_CONTEXT_SEASON_HOLDOUT_INSUFFICIENT` |
| Totals | Research replay only | `NFL_TOTAL_POINTS_V1_HOLDOUT_REPLAY_COMPLETE`; no forward cohort |
| Pick'em | Contract-only runtime over the governed scorer | Holds on `NFL_FITTED_SCORER_FAILED`; nothing certified |
| Fantasy / DFS score | `PHASE_B_MIN_N = 200`, `PHASE_C_MIN_N = 500` unique settled snapshots | `actual_certification_status=CANDIDATE_ONLY` |
| ATD settlement | Gamebook verification and official settlement proposals | Evidence path, not certification |

---

## 6. Read-only verification

Run only against an explicitly authorized database, in a read-only role or
transaction:

```sql
-- G1/G2
select graded_n, minimum_forward_required, status,
       certification_recommendation, blockers, generated_at
  from public.wow_nfl_forward_calibration_health
 order by generated_at desc, health_id desc
 limit 1;

-- G4 exact-version cohort
select model_version, count(distinct official_event_id) as graded_events
  from public.wow_nfl_forward_shadow_grades
 group by model_version;

-- G5
select artifact_id, active, promoted, lifecycle_state,
       calibrator_id, certification_id, feature_schema_version
  from public.wow_nfl_event_fitted_model_artifacts;

-- G6
select capability_status, evidence
  from public.wow_runtime_capabilities
 where capability_key = 'NFL_EVENT_PROBABILITY';

-- G7
select certification_id, certification_status, independent_verification_status,
       probability_publishable, artifact_sha256, calibration_sha256
  from public.wow_team_event_specialist_certifications
 where sport = 'NFL';
```

Read-only pre-kickoff cohort check (after PR #2599 deploys). Do **not** use
`workflow_dispatch: wow-v17-nfl-forward-shadow` for this read: its
`forward-shadow` job also runs the grading route, which writes grade rows and a
calibration-health row. Only the reporting step inside that job is read-only.

```text
POST /internal/v17/nfl-forward-cohort-preflight   (OIDC-authenticated)
  {"slate_date": "<US/Eastern gameday>", "expected_slate_games": <n>}

or, from the repository root in an approved isolated environment:
PYTHONPATH=artifacts/wow-engine python -m v17.nfl_forward_cohort_preflight --slate-date <date> --expected-slate-games <n>

The CLI's current `_client()` obtains a Supabase service credential; its
code path is read-only, but that credential is **not** a read-only role.
Do not use the CLI as proof of least-privilege production access. Use a
separately restricted client when performing an authorized production read.
```

Omitting `expected_slate_games` leaves slate coverage unverified, so the
projection will not report `PROJECTED_TO_REACH_MINIMUM`.

Repository checks for any change touching these gates:

```sh
python -m pytest -q artifacts/wow-engine/test_v17_nfl_forward_shadow.py \
  artifacts/wow-engine/test_v17_nfl_forward_settlement_refresh.py \
  artifacts/wow-engine/tests/test_v17_nfl_daily_stage_receipts.py \
  artifacts/wow-engine/tests/test_v17_nfl_calibration_producer_receipt.py \
  artifacts/wow-engine/tests/test_v17_nfl_tie_forward_grade_exclusion.py
```

---

## 7. What this document does not do

It does not train, fit, calibrate, certify, promote, register, publish, rank, or
execute anything. It does not change `MIN_FORWARD_GRADED`, any metric threshold,
or any terminal ceiling. A favorable row here is readiness evidence; the
governed decision remains separate.
