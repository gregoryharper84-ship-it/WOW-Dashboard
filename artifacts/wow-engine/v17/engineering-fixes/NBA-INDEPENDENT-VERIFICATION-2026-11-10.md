# NBA team-event independent verification — procedure and receipt schema (PROPOSED)

Status: **PROPOSED / NOT_RATIFIED / NOT_EXECUTED**.
`can_execute=false`, `probability_publishable=false`.
This document proposes how an independent verifier would earn a
`independent_verification_status = 'PASS'` for one NBA team-event specialist. It
grants nothing by itself, and no verification run has been performed.

Companion machine-readable schema:
[nba_team_event_verification_receipt.schema.json](nba_team_event_verification_receipt.schema.json).

## 0. Why this exists

`wow_team_event_specialist_certifications.independent_verification_status` is
`check (... in ('PASS','FAIL'))` and `verification_receipt` defaults to
`'{}'::jsonb`. The publish gate
(`wow_team_event_certification_publish_gate`) permits
`probability_publishable = true` only when the row is `CERTIFIED` **and**
verification is `PASS`. Nothing in the repository produces that evidence: the
field is validated by `parse_certification` and consumed by
`multisport_team_event_governance._certification_gate` and
`sirt_assurance.assess_class_a_closure`, but no component generates it.

So "PASS" is currently an unearned string. This procedure makes it a
reproducible claim with an auditable artifact.

### What a PASS can and cannot conclude

| Can conclude | Cannot conclude |
| --- | --- |
| The fitted artifact, its calibrator, and its training corpus are the objects the registry says they are, and the recorded metrics are reproducible | That the calibrated lower bound is approved — `_hold("MODEL_INPUTS_INSUFFICIENT", "NBA_DYNAMIC_BOUNDS_POLICY_NOT_APPROVED")` is unconditional |
| The chronological calibration and holdout partitions are disjoint and ordered | That current-pregame feature hydration exists for a live slate |
| The numeric gates that `certification_decision` encodes were actually met | That NBA is registered as a certifiable lane — `CERTIFIED_TEAM_EVENT_SPORTS` contains only MLB |
| The certification row's hashes bind to real bytes | That any probability may be published |

A PASS removes exactly one blocker:
`TEAM_EVENT_INDEPENDENT_VERIFICATION_NOT_PASS`. It is a
`EVIDENCE_COMPLETE_PENDING_TERMINAL_AUTHORITY` state, matching the SIRT
disposition pattern in `v17/sirt_assurance.py`.

## 1. Independence requirements (hard)

The repository already encodes an independence model in
`sirt_assurance.assess_class_a_closure`; this procedure reuses its semantics.

1. `implementation_owner != verification_owner`, both non-empty. If the same
   identity authored the fitting change and signs the verification, the verdict
   is `FAIL` with `VERIFICATION_INDEPENDENCE_UNPROVEN`.
2. `independent_verifier` must be a distinct identity from the fitting pipeline
   and must not be the author of the artifact version under review.
3. The verifier must recompute from **primary evidence** — the corpus rows, the
   stored artifact payload, the stored calibrator coefficients — never from the
   registration payload it is being asked to bless. Re-signing values copied out
   of `wow_basketball_team_event_model_artifacts` is not verification.
4. The verifier must not hold write authority on
   `wow_team_event_specialist_certifications`. Verification is an artifact that a
   governed write consumes; it is not the write itself. `service_role` holds
   `select, insert` only, and that must not be widened to accommodate the
   verifier.
5. Absence is failure. A missing field, unreadable corpus, or unreproducible
   metric yields `FAIL`, never a partial PASS. The `'{}'` default is a FAIL
   receipt, not an empty PASS.

## 2. Required checks

Twelve checks. Each names the code it must replicate, so the verifier is not
inventing criteria. `observed` in the receipt records the values actually seen.

| ID | Check | Must equal / satisfy | Code anchor |
| --- | --- | --- | --- |
| V1 | Every settled `wow_nba_training_games` row carries `source_provider`, `source_endpoint`, `source_retrieved_at`, and a 64-hex `source_payload_sha256` | zero rows missing any field | `basketball_training_replay.verify_training_provenance` |
| V2 | Corpus freshness | newest settled game within `MAX_TRAINING_CORPUS_AGE_DAYS = 400` | `evaluate_training_freshness` |
| V3 | Pagination integrity | no duplicate `game_id` across pages; total equals the paged count | `basketball_specialist_pipeline.load_games` |
| V4 | Feature determinism | rebuild features from corpus; per-row `feature_payload_sha256` identical; schema `BASKETBALL_TEAM_EVENT_FEATURES_V2`; exactly the 8 `FEATURE_NAMES` | `basketball_team_event_specialist.build_pregame_features` |
| V5 | Artifact checksum | SHA-256 over `{sport, model_family, model_artifact_version, feature_schema_version, feature_names, intercept, coefficients, means, scales}` (sorted keys, compact separators) equals `artifact_checksum` | `train_specialist`, `_research_receipt` |
| V6 | Holdout reproducibility | recomputed `holdout_brier`, `holdout_log_loss`, `holdout_accuracy` match the stored row; holdout rows are strictly after `train_end_date`; counts equal `training_row_count` / `holdout_row_count`; no overlap (`BASKETBALL_CALIBRATION_HOLDOUT_OVERLAP`) | `evaluate_untouched_calibrated_holdout`, `disjoint_calibration_and_holdout` |
| V7 | Calibration reproducibility | recomputed Platt `a`,`b` match the calibrator row; `calibration_row_count >= 200`; method in `{PLATT_TIME_SPLIT_V1, ISOTONIC_V1}`; no calendar date split across folds; `calibration_fit_end <= immutable_model_timestamp` | `phase_b_platt`, `_walk_forward_raw`, `_calibration_blockers` |
| V8 | Quantified fit gates | `training_row_count >= MIN_TRAIN_ROWS (200)`, `holdout_row_count >= MIN_HOLDOUT_ROWS (50)`, `holdout_brier < 0.25`, `holdout_log_loss < ln 2` | `certification_decision` |
| V9 | Calibration error recorded | `ece` and `calibration_bias` recomputed and recorded. **No code gate exists for ECE**; if a threshold is imposed it must be stated in the receipt's `limitations` and applied explicitly | `calibration` metrics |
| V10 | Identity consistency | `model_version`, `artifact_id`, `artifact_sha256` identical across the artifact row, the calibrator's `model_artifact_version`, the capability row's `model_artifact_version`, and the certification row; the unique index `(sport, league_scope, controlling_specialist, model_version, artifact_id, certification_status)` admits exactly one CERTIFIED row | `20260921_create_team_event_specialist_certifications.sql` |
| V11 | Source attestation capability | the producer can emit `feature_audit.source_replay_status = "RAW_CAPTURE_HASH_AND_EVENT_REPLAY_PASS"` and `timestamp_attestation = "VERIFIED"`. Without these the scorer refuses the input regardless of certification | `_feature_blocker` |
| V12 | No substitution | `market_features_used = false`, `market_probability_used_as_model = false`, `generic_reasoning_used_as_model = false` | feature builder, `reduce_multisport_team_event` |

Notes that matter for an honest verdict:

- **V11 is a capability check, not a slate check.** A verifier cannot attest a
  future game's snapshot. It can only confirm the producer emits attested
  captures, and must record `NOT_INDEPENDENTLY_ATTESTED` when it cannot.
- **V12 has no numeric proof.** It is a review of the feature builder and the
  scoring path; record it as `PASS` only after reading the code that builds the
  feature vector, not from a flag in a payload.

## 3. Receipt schema

The receipt lives in
`wow_team_event_specialist_certifications.verification_receipt` and is validated
against [the schema](nba_team_event_verification_receipt.schema.json)
(`WOW_V17_TEAM_EVENT_INDEPENDENT_VERIFICATION_V1`).

Root fields: `schema_version`, `verification_id`, `sport` (const `NBA`),
`league_scope` (const `NBA`), `controlling_specialist`, `model_family` (const
`BASKETBALL_TEAM_EVENT_LOGISTIC_V1`), `model_version`, `model_artifact_id`,
`model_artifact_sha256`, `calibration_artifact_id`, `calibration_sha256`,
`feature_schema_version` (const `BASKETBALL_TEAM_EVENT_FEATURES_V2`),
`input_contract_version`, `independent_verifier`, `implementation_owner`,
`verification_owner`, `independent_verification_status`, `method`, `checks`,
`metrics`, `source_attestation`, `evidence_refs`, `limitations`, `verdict`,
`can_execute` (const `false`), `terminal_authority` (const
`V17_TERMINAL_REDUCER`), `created_at_utc`.

Schema-enforced consequences:

- `independent_verification_status = "PASS"` **requires** `metrics`,
  `source_attestation`, and all five `evidence_refs`; permits no check with
  status `FAIL`; permits no check blocker; and forces
  `verdict.certification_eligible = true` with `verdict.blockers` empty.
- `independent_verification_status = "FAIL"` forces
  `verdict.certification_eligible = false` with at least one blocker.
- `verdict.publishable_scope` is the constant `"NONE"`. A verification receipt
  can never assert publication scope.

Enforced by the harness rather than the schema (JSON Schema cannot express
these): identity inequality, exactly one entry per `check_id` V1–V12, and
`limitations` covering every `NOT_APPLICABLE` check.

### Minimal example

Placeholder digests below are `a…a` / `b…b`; they are not evidence and must be
replaced with real digests.

```json
{
  "schema_version": "WOW_V17_TEAM_EVENT_INDEPENDENT_VERIFICATION_V1",
  "verification_id": "0f1d2c3b-4a59-4e6f-8b7a-1c2d3e4f5a6b",
  "sport": "NBA",
  "league_scope": "NBA",
  "controlling_specialist": "wow.nba-game-win-probability-expert",
  "model_family": "BASKETBALL_TEAM_EVENT_LOGISTIC_V1",
  "model_version": "NBA_TEAM_EVENT_LOGISTIC_V1_<stamp>",
  "model_artifact_id": "<artifact uuid>",
  "model_artifact_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "calibration_artifact_id": "NBA_TEAM_EVENT_PLATT_V1_<stamp>",
  "calibration_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "feature_schema_version": "BASKETBALL_TEAM_EVENT_FEATURES_V2",
  "input_contract_version": "NBA_GAME_WIN_INPUTS_V1",
  "independent_verifier": "<verifier identity>",
  "implementation_owner": "<fitting owner>",
  "verification_owner": "<independent owner>",
  "independent_verification_status": "PASS",
  "method": {
    "harness": "wow-v17-nba-team-event-verification",
    "code_sha": "<40-64 hex>",
    "environment": "ephemeral-ci",
    "run_started_at_utc": "2026-11-10T18:00:00Z",
    "run_completed_at_utc": "2026-11-10T18:14:00Z",
    "replay_command": "python -m v17.nba_team_event_verification --source-json bundle.json --artifact-id <uuid> ..."
  },
  "checks": [
    { "check_id": "V1", "status": "PASS", "observed": { "settled_rows": 0, "missing_provenance": 0 }, "blockers": [] }
  ],
  "metrics": {
    "training_rows": 0, "holdout_rows": 0, "calibration_rows": 0,
    "holdout_brier": 0, "holdout_log_loss": 0, "holdout_accuracy": 0,
    "calibration_brier": 0, "calibration_error_ece": 0, "calibration_bias": 0,
    "platt_a": 0, "platt_b": 0, "calibration_fit_end": "2026-11-10T00:00:00Z"
  },
  "source_attestation": {
    "source_replay_status": "RAW_CAPTURE_HASH_AND_EVENT_REPLAY_PASS",
    "timestamp_attestation": "VERIFIED",
    "dataset_id": "<dataset id>"
  },
  "evidence_refs": {
    "corpus_evidence_ref": "<ref>",
    "artifact_evidence_ref": "<ref>",
    "calibration_evidence_ref": "<ref>",
    "replay_evidence_ref": "<ref>",
    "independent_verification_evidence_ref": "<ref>"
  },
  "limitations": ["V9: no code ECE gate; reported value is advisory"],
  "verdict": { "publishable_scope": "NONE", "certification_eligible": true, "blockers": [] },
  "can_execute": false,
  "terminal_authority": "V17_TERMINAL_REDUCER",
  "created_at_utc": "2026-11-10T18:14:00Z"
}
```

The example is abridged (`checks` shows one entry); a real receipt carries
V1–V12 and real numbers.

## 4. Verdict rubric

| Condition | `independent_verification_status` |
| --- | --- |
| Any V-check `FAIL` | `FAIL` |
| Any not-`FAIL` check `NOT_APPLICABLE` without a matching `limitations` entry | `FAIL` |
| Any required `evidence_refs` key empty | `FAIL` |
| `implementation_owner == verification_owner`, or either empty | `FAIL` |
| V1–V12 all `PASS` (or `NOT_APPLICABLE` with a stated limitation) and metrics reproducible | `PASS` |

A `PASS` here means "this artifact and calibrator are what they claim and met
the encoded gates". It does not mean "publish this".

## 5. How the receipt binds to the certification row

On the governed insert into `wow_team_event_specialist_certifications`:

- `artifact_sha256` must equal **both** the artifact row's `artifact_checksum`
  and the value the scorer re-derives from the payload. A mismatch surfaces at
  runtime as `NBA_CERTIFIED_MODEL_ARTIFACT_PAYLOAD_MISMATCH` (gate 8), which a
  correct receipt prevents.
- `calibration_sha256` must equal the `calibration_artifact_fingerprint` the
  reducer compares; otherwise the reduction returns
  `TEAM_EVENT_CERTIFICATION_CALIBRATION_SHA256_MISMATCH`.
- `model_version` and `artifact_id` must match the capability row's
  `model_artifact_version`; the artifact RPC joins on that pair, so a stale
  capability row yields `NBA_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND` while still
  displaying `CERTIFIED`.
- The embedded `verification_receipt` is immutable after insert. Revocation is a
  new `REVOKED` row with `supersedes_certification_id`, carrying its own
  receipt.

## 6. Blockers this procedure does not clear

Even a perfect PASS leaves the NBA lane held:

1. `NBA_DYNAMIC_BOUNDS_POLICY_NOT_APPROVED` — unconditional final hold; needs an
   approved calibrated lower-bound method.
2. Current-pregame feature hydration producer — `wow_nba_pregame_feature_rows`
   is FK-bound to settled `wow_nba_training_games`.
3. `CERTIFIED_TEAM_EVENT_SPORTS` lacks `NBA`, so gate 4 holds before the
   registry is read.
4. `reduce_multisport_team_event` returns
   `MULTISPORT_TERMINAL_ROUTE_UNSUPPORTED` for NBA (its sum-check branches cover
   only `SOCCER`, `WNBA`, `NHL`, `TENNIS`, `MMA`), so NBA needs its own
   registered terminal route rather than reuse of that reducer.
5. `wow_runtime_capabilities.NBA_EVENT_PROBABILITY` remains `UNAVAILABLE` with
   `CERTIFIED_ARTIFACT_REQUIRED`.

## 7. Harness

Implementation: [`v17/nba_team_event_verification.py`](../nba_team_event_verification.py),
tests: [`tests/test_v17_nba_team_event_verification.py`](../../tests/test_v17_nba_team_event_verification.py).

- `verify_nba_team_event(source, request, ...)` returns the receipt described
  here. `VerificationSource` is a read-only port; `SupabaseVerificationSource`
  wraps an already-constructed client and issues `select` reads only, and
  `MappingVerificationSource` serves a materialized bundle offline.
- A missing artifact or calibrator row raises `VerificationContextError` rather
  than emitting a receipt with placeholder digests. Absence is not a digest.
- Exit code is `0` for PASS and `2` for FAIL, so CI can gate on it.
- Local run from `artifacts/wow-engine`:

```sh
PYTHONPATH=. ../../.venv/bin/python -m pytest -q --noconftest \
  tests/test_v17_nba_team_event_verification.py
```

`--noconftest` is only needed where the repository's full `conftest.py`
dependencies (Celery, FastAPI, Supabase) are not installed; under the full
requirements the suite runs unmodified.

### 7.1 Wiring into governed maintenance

`v17/nba_verification_wiring.py` runs the harness after every NBA fitting run
and appends the receipt to `wow_independent_verification_receipts`
(`migrations/20261110_v17_independent_verification_receipts.sql`). The row is
immutable, `publishable_scope` is `NONE`, and `can_execute` is false.

The run fails closed when a receipt cannot be built (`NBA_VERIFICATION_EVIDENCE_INCOMPLETE`),
cannot be archived (`NBA_VERIFICATION_ARCHIVE_UNAVAILABLE`), or reports a
blocker that was not deferred (`NBA_INDEPENDENT_VERIFICATION_FAILED`). Maintenance
fails the workflow through the existing `status == BLOCKED` contract.

Before any NBA fit, `verification_prerequisites` probes the two structures this
wiring depends on and holds the lane if either is absent, so an unapplied
migration never leaves a persisted-but-unverifiable artifact behind:

| Code | Meaning |
|---|---|
| `NBA_VERIFICATION_LEDGER_MISSING` | `wow_independent_verification_receipts` (or its columns) is absent. |
| `NBA_VERIFICATION_CALIBRATOR_EVIDENCE_MISSING` | `calibration_sha256` / `calibration_fit_end` are absent from the calibrator registry. |
| `NBA_VERIFICATION_PREREQUISITE_PROBE_FAILED` | The probe failed for an unclassified reason (for example a missing grant); reported with the provider code, never a raw message. |

A blocked prerequisite row carries `replay_attempted: false`, so no fit is
attributed to a run that could not verify it.

Exactly two blockers may be deferred, and only by name:

| Blocker | Why a nightly fit cannot clear it |
|---|---|
| `CHECK_FAILED:V10` (`CAPABILITY_STATUS_NOT_CERTIFIED`) | `fit_and_persist` persists `SHADOW` by design; certification is a separate governed act. Any *other* V10 blocker (version, schema, digest drift) is not deferrable. |
| `CHECK_FAILED:V11` | The maintenance lane holds no independent source-event replay attestation. V1 still enforces row-level provenance. |

Deferral is a property of the caller, not the harness: `classify_verdict(receipt, defer=())`
closes the gate on any FAIL. WNBA emits `NOT_APPLICABLE` and no receipt.

### 7.2 PostgreSQL integration check

`test_v17_nba_verification_prerequisites_postgres.py` applies the real migration
files to a throwaway database and asserts all three states, so the probe is
proven against actual DDL rather than a hand-written fixture:

| State | Expected |
|---|---|
| base schema only | `NBA_VERIFICATION_LEDGER_MISSING`, probes `42P01` + `42703` |
| ledger present, calibrator evidence dropped | `NBA_VERIFICATION_CALIBRATOR_EVIDENCE_MISSING` |
| ledger migration applied | ready, plus a real receipt round-trip |

The applied state also proves the archive payload fits the table, that
`update`/`delete` are rejected with `P0001` and `truncate` with `55000`, and that
`can_execute = true`, an inconsistent PASS and equal owners are all rejected.

```sh
WOW_TEST_POSTGRES_DSN=postgresql:///postgres \
  PYTHONPATH=. ../../.venv/bin/python -m pytest -q \
  test_v17_nba_verification_prerequisites_postgres.py
```

Skips when the DSN is absent. The Supabase/PostgREST transport is replaced by a
thin psycopg adapter that raises the same SQLSTATE codes, so this is not an HTTP
transport test and does not contact Supabase.

### 7.3 PostgREST integration check

`test_v17_nba_verification_prerequisites_postgrest.py` runs the probe against a
real PostgREST 12.2.3 — the version pinned in
`.github/workflows/wow-engine-verify.yml` — so the codes the probe accepts are
observed rather than assumed. Every phase asserts both the raw HTTP `code` and
the probe's typed classification:

| Phase | Endpoint behaviour | Observed raw code | Probe result |
|---|---|---|---|
| A | ledger relation absent | `42P01` (404) | `NBA_VERIFICATION_LEDGER_MISSING` |
| A | calibrator evidence columns absent | `42703` (400) | `COLUMN_MISSING` |
| B | ledger migration applied | `200` | ready |
| C | evidence columns dropped | `42703` (400) | `NBA_VERIFICATION_CALIBRATOR_EVIDENCE_MISSING` |
| D | schema cache reloaded without the columns | `42703` / `PGRST204` | `NBA_VERIFICATION_CALIBRATOR_EVIDENCE_MISSING` |
| E | migration re-applied | `200` | ready |

PostgREST relays the PostgreSQL SQLSTATE for both read failures, so `42P01` and
`42703` are the codes an unproxied endpoint really emits; both are members of
`_MISSING_TABLE_CODES` / `_MISSING_COLUMN_CODES`. The `PGRST205` / `PGRST204`
members remain accepted for gateway variants that answer from the schema cache;
phase D allows either because that depends on cache state, and the probe must
classify both. The probe is deliberately not the only thing under test: phase A
also asserts the raw status and message text.

Two endpoint facts this check had to encode, both previously wrong in the test
itself:

* PostgREST serves relations at `/`; `/rest/v1` is Supabase's gateway prefix. A
  local gateway rewrites the prefix so the production URL shape is exercised.
* The ledger migration revokes from `anon`, `authenticated` and `service_role`.
  Those roles do not exist in vanilla PostgreSQL, so the migration fails there
  (`role "anon" does not exist`) and the ledger table is never created. The
  fixture creates the roles first; the production apply on Supabase is
  unaffected because the roles already exist.

```sh
WOW_TEST_POSTGRES_DSN=postgresql:///postgres \
WOW_TEST_POSTGREST_URL=http://127.0.0.1:3000 \
  PYTHONPATH=. ../../.venv/bin/python -m pytest -q \
  test_v17_nba_verification_prerequisites_postgrest.py
```

Skips when either variable is absent. `WOW_TEST_POSTGREST_URL` is the PostgREST
root (no `/rest/v1`), and the anon role must be able to read the public schema.

CI: `.github/workflows/wow-v17-basketball-model-maintenance.yml` runs the job
`verification-prerequisites` on pull requests (and pushes to `main`) that touch
any of the three files, the migration, the wiring or the harness. It provisions
PostgreSQL 16 as a service container, fetches the pinned PostgREST 12.2.3 and
asserts its version, creates two isolated databases so the PostgreSQL and
PostgREST checks never share state, and runs all three files. A skipped check
fails the job, so the proof cannot silently disappear when an environment
variable or role goes missing. The job uses no credentials, contacts no
production endpoint, and asserts `can_execute=false`.
This still never contacts the project's Supabase endpoint or any production
database: the migration remains unapplied there, so deployed readiness is
unproven.

## 8. Non-authority

This procedure is evidence production, not certification. It cannot grant
publication, rank eligibility, capital allocation, or execution, and it does not
alter `can_execute=false`. Approval of the procedure itself is a governance act
outside this document.
