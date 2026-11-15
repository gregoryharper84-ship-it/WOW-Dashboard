# NBA team-event certification path — exact required steps — November 10, 2026

Status: **READ_ONLY_MAPPING / NOT_EXECUTED**.
`can_execute=false`, `probability_publishable=false`. Nothing below was written
to any database. This maps the certification path for one NBA team-event
(moneyline / outright-winner) specialist against the registry schema that
actually exists in this repository, and separates evidence that is present from
evidence that is missing.

The runtime gate is `v17/nba_current_pregame_scorer.py`. The registries are
`wow_team_event_specialist_certifications`,
`wow_basketball_team_event_model_artifacts`,
`wow_basketball_team_event_calibrators`, and
`wow_basketball_team_event_certification`. There is no `v17_model_registry`.

## 1. The actual gate chain, in execution order

`prepare_nba_current_pregame_scorer_receipt` returns `HOLD` at the first
failing check. Order matters: later registries are never consulted until the
earlier evidence checks pass.

| # | Check | Code location | Hold produced |
| --- | --- | --- | --- |
| 1 | Canonical event identity resolves (event id, both team ids, distinct) | `resolve_nba_current_event_identity`, `v17/nba_event_identity.py` | `MODEL_INPUTS_INSUFFICIENT` / `NBA_CANONICAL_IDENTITY_*` |
| 2 | Current pregame feature package: `status == "PASS"`, sport `NBA`, matching `official_event_id`, matching home/away team ids, matching `event_start_time_utc`, `feature_schema_version == "BASKETBALL_TEAM_EVENT_FEATURES_V2"` | `_feature_blocker` | `NBA_CURRENT_PREGAME_FEATURE_*` |
| 3 | Lineup evidence present; `injury_report.status` in `{CURRENT, CONFIRMED, OFFICIAL}`; `latest_material_update_timestamp` present and not later than `feature_timestamp`; `expected_starters_rotation` in `CONFIRMED_ROTATION_STATES = {CONFIRMED, OFFICIAL, COMPLETE, PASS}` and not `CHANGED` | `_rotation_blocker` | `NBA_LINEUP_EVIDENCE_MISSING`, `NBA_INJURY_REPORT_NOT_CURRENT`, `NBA_LINEUP_UPDATE_TIMESTAMP_MISSING`, `NBA_FEATURE_TIMESTAMP_MISSING`, `NBA_STARTER_CHANGED_AFTER_FEATURE_HYDRATION`, `NBA_CHANGED_STARTERS_RECEIPT_UNVERIFIED`, `NBA_STARTING_ROTATION_NOT_CONFIRMED` |
| 4 | `CERTIFIED_TEAM_EVENT_SPORTS.get("NBA")` is non-null | `v17/team_event_capability_manifest.py` | `MODEL_UNAVAILABLE` / `NBA_CONTROLLING_SPECIALIST_NOT_CERTIFIED` |
| 5 | RPC `wow_v17_active_team_event_certification('NBA','NBA')` returns `ok:true`, `certification_status='CERTIFIED'`, `independent_verification_status='PASS'`, `probability_publishable=true` | `v17/fitted_team_event_certification.py` | `MODEL_UNAVAILABLE` (not certified) / `MODEL_SCORER_FAILED` (registry down) / `MODEL_OUTPUT_INVALID` (bad response) |
| 6 | Certification scope matches: `sport='NBA'`, `league_scope='NBA'`, `controlling_specialist` equals the manifest value, `model_family='BASKETBALL_TEAM_EVENT_LOGISTIC_V1'`, `feature_schema_version='BASKETBALL_TEAM_EVENT_FEATURES_V2'` | scorer line 465 | `MODEL_UNAVAILABLE` / `NBA_CERTIFICATION_SPECIALIST_SCOPE_OR_MODEL_MISMATCH` |
| 7 | RPC `wow_basketball_certified_model_artifact('NBA')` returns the artifact with `promoted and active` and a joined certification row at `capability_status='CERTIFIED'`, whose `model_artifact_version` matches and whose `artifact_checksum` equals `certification.artifact_sha256` | `resolve_nba_champion_artifact` | `MODEL_UNAVAILABLE` / `NBA_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND`, `MODEL_OUTPUT_INVALID` / `..._CERTIFICATION_MISMATCH` |
| 8 | Re-derived SHA-256 over `{sport, model_family, model_artifact_version, feature_schema_version, feature_names, intercept, coefficients, means, scales}` equals `certification.artifact_sha256` and the stored `artifact_checksum` | `_research_receipt` | `MODEL_OUTPUT_INVALID` / `NBA_CERTIFIED_MODEL_ARTIFACT_PAYLOAD_MISMATCH` |
| 9 | A calibration-artifact resolver is supplied | scorer parameter, default `None` | `MODEL_INPUTS_INSUFFICIENT` / `NBA_CALIBRATOR_PAYLOAD_RESOLVER_UNAVAILABLE` |
| 10 | — | unconditional final return | `MODEL_INPUTS_INSUFFICIENT` / `NBA_DYNAMIC_BOUNDS_POLICY_NOT_APPROVED` |

Two consequences worth stating plainly:

- **Step 4 alone is decisive today.** `CERTIFIED_TEAM_EVENT_SPORTS` contains only
  `{"MLB": MLB_GAME_WIN_PROBABILITY_EXPERT}`. No registry row can satisfy a
  `None` manifest lookup, and the module's own docstring calls itself an
  "Unregistered fail-closed NBA current-pregame scorer integration seam".
- **Step 10 is unconditional.** Even with every registry row correct and every
  resolver wired, this module cannot emit a publishable probability: its best
  possible return is `status="RESEARCH_ONLY_NOT_FOR_PUBLICATION"` with
  `probability_publishable=false`, `rank_eligible=false`. Publishing requires a
  separate approved dynamic-bounds policy and a registered bridge that the
  repository does not contain.

## 2. Required governed writes, and what each one has to satisfy

### A. Fitted artifact — `wow_basketball_team_event_model_artifacts`

Produced by `basketball_specialist_pipeline.fit_and_persist` (insert, not upsert).
Required values: `sport='NBA'`, `model_family='BASKETBALL_TEAM_EVENT_LOGISTIC_V1'`,
`model_artifact_version='NBA_TEAM_EVENT_LOGISTIC_V1_<UTC stamp>'`,
`feature_schema_version='BASKETBALL_TEAM_EVENT_FEATURES_V2'`,
`artifact_format='WOW_BASKETBALL_LOGISTIC_JSON_V1'`, `artifact_payload` (the
9-field checksum payload plus training metadata), `artifact_checksum`,
`training_row_count`, `train_start_date`, `train_end_date`, `holdout_row_count`,
`holdout_brier`, `holdout_log_loss`, `holdout_accuracy`.
Table has **no** `probability_publishable` column; publishability is expressed by
`promoted`/`active` plus the joined certification row.

### B. Calibrator — `wow_basketball_team_event_calibrators`

`calibrator_version='NBA_TEAM_EVENT_PLATT_V1_<UTC stamp>'`,
`model_artifact_version` (same as A), `calibration_method` from
`phase_b_platt`, `coefficients={"a":…,"b":…}`, `calibration_row_count`, `brier`,
`log_loss`, `ece`, `calibration_bias`.

### C. Capability row — `wow_basketball_team_event_certification`

Primary key is `sport` alone (one row per sport), so this is an **upsert on
`sport`** and it overwrites the previous state. It must carry
`capability_status='CERTIFIED'`, `reason_code`, and the **same**
`model_artifact_version` and `calibrator_version` as A and B — the artifact RPC
joins on `(sport, model_artifact_version)`, so a stale version here silently
breaks step 7 while leaving a CERTIFIED label in place.

The pipeline deliberately does not do this. When `certification_decision`
returns `CERTIFIED`, `fit_and_persist` persists:

```python
persisted_status = "SHADOW" if decision.capability_status == "CERTIFIED" else decision.capability_status
persisted_reason = "CERTIFICATION_REPLAY_REQUIRED" if decision.capability_status == "CERTIFIED" else decision.reason_code
```

That is the "certification-to-promotion gap" described as a bug in the proposed
promotion transaction. It is intentional code: a successful fit earns SHADOW
evidence and a named obligation, not publication.

### D. Immutable specialist certification — `wow_team_event_specialist_certifications`

Insert-only (`wow_reject_immutable_mutation` blocks update/delete; `service_role`
holds `select, insert` only). One row must satisfy:

`sport='NBA'`, `league_scope='NBA'`, `controlling_specialist` = the manifest
identity (`wow.nba-game-win-probability-expert` per `wow_specialist_registry`),
`model_family='BASKETBALL_TEAM_EVENT_LOGISTIC_V1'`, `model_version` = A's
version, `artifact_id` = A's `artifact_id`, `artifact_sha256` = 64-hex checksum,
`feature_schema_version='BASKETBALL_TEAM_EVENT_FEATURES_V2'`,
`input_contract_version` (free text; no canonical constant exists in the
repository), `calibration_artifact_id`, `calibration_sha256` (64-hex),
`calibration_method`, `independent_verification_status='PASS'`,
`certification_status='CERTIFIED'`, `certified_at` (tz-aware),
`probability_publishable=true`, `can_execute=false`, `verification_receipt` jsonb.

Constraint `wow_team_event_certification_publish_gate` permits
`probability_publishable=true` only with `CERTIFIED` + verification `PASS`.
Unique index is `(sport, league_scope, controlling_specialist, model_version,
artifact_id, certification_status)`, so each model version gets one CERTIFIED row;
revocation is a new `REVOKED` row with `supersedes_certification_id`.

**Nothing in the repository inserts into this table.** The migration creates it,
`fitted_team_event_certification.py` only reads it through the RPC, and the
privilege-hardening migration narrows grants. So step D is an out-of-band
governed write or new reviewed code — not an output of the fitting pipeline.

### E. Static certification manifest (code)

`CERTIFIED_TEAM_EVENT_SPORTS` must gain `"NBA"`, otherwise step 4 holds before
the registry is ever read. The manifest states it "cannot be expanded merely
because code imports or a bridge is registered", so this edit belongs to the
same reviewed change that lands the real evidence.

### F. Runtime capability row — `wow_runtime_capabilities`

`NBA_EVENT_PROBABILITY` is currently `UNAVAILABLE` with
`blocker='CERTIFIED_ARTIFACT_REQUIRED'` (set by
`20260915_first_six_team_event_ownership.sql`, which preserves an existing
`AVAILABLE` but never raises it). The daily route consults this separately from
the scorer.

### G. Bridge registration + dynamic bounds policy (code)

Missing entirely: a production caller that supplies `current_feature_package`,
`lineup_evidence`, and a `calibration_artifact_resolver`, plus the approved
bounds method that replaces the unconditional step-10 hold. `validate_prediction_package`
already specifies the envelope it must produce: `raw_probability`,
`calibrated_probability`, `lower_bound`, `upper_bound` with
`lower <= calibrated <= upper`, `model_timestamp >= latest_material_update_timestamp`,
`probability_package_valid=true`, `can_execute=false`.

## 3. Evidence present

| Evidence | Where |
| --- | --- |
| Settled-game training corpus schema with per-row source provenance (`source_provider`, `source_endpoint`, `source_retrieved_at`, `source_payload_sha256`, `settled`) | `migrations/20260913_basketball_team_event_specialists.sql` |
| Training hydration from real sources (SportsDataverse ESPN schedule releases, BallDontLie) with monotonic date fence | `basketball_event_hydration_runtime.py` |
| Deterministic pregame feature builder, 8 features, `FEATURE_SCHEMA_VERSION='BASKETBALL_TEAM_EVENT_FEATURES_V2'`, `MIN_PRIOR_GAMES=5` | `basketball_team_event_specialist.py` |
| Standardized logistic fit with checksum over the exact 9-field payload the scorer re-derives | `basketball_team_event_specialist.py` |
| Chronological Platt calibration with date-safe folds; requires `>= 350` rows before evaluation and fails on fold leakage | `basketball_specialist_pipeline.py`, `calibration.phase_b_platt` |
| Disjoint calibration/holdout guard (`BASKETBALL_CALIBRATION_HOLDOUT_OVERLAP`) and untouched calibrated holdout metrics | `basketball_specialist_pipeline.py` |
| Quantified certification gates: `MIN_TRAIN_ROWS=200`, `MIN_HOLDOUT_ROWS=50`, `calibration_rows>=200`, `calibration_status in {PLATT_TIME_SPLIT_V1, ISOTONIC_V1}`, `holdout_brier < 0.25`, `holdout_log_loss < ln 2` | `certification_decision` |
| Provenance verifier requiring 64-hex payload hash on every settled row; 400-day corpus freshness rule | `basketball_training_replay.py` |
| Governed maintenance workflow that hydrates then fits, asserting `automatic_certification=false`, `automatic_promotion=false`, `probability_publishable=false` | `.github/workflows/wow-v17-basketball-model-maintenance.yml` |
| Artifact + calibrator + certification registries, hardened service-role-only artifact RPC | `20260913_…`, `20260915_harden_basketball_certified_model_artifact_rpc.sql` |
| Immutable specialist certification registry and its resolving RPC | `20260921_create_team_event_specialist_certifications.sql` |
| Routing row for NBA outright winner (`wow.nba-game-win-probability-expert`, `can_execute=false`) | `20260915_first_six_team_event_ownership.sql` |
| Fail-closed scorer seam with typed failure classes and checksum binding | `v17/nba_current_pregame_scorer.py` |
| Contract tests for the seam, the certification parser, and the maintenance bounds | `tests/test_v17_nba_current_pregame_scorer.py`, `tests/test_v17_fitted_team_event_certification.py`, `test_basketball_team_event_specialist.py`, `test_basketball_training_replay_boundaries.py` |

## 4. Evidence missing

| Missing | Why it blocks | Where it would have to come from |
| --- | --- | --- |
| A `promoted=true, active=true` NBA artifact and a `capability_status='CERTIFIED'` capability row at matching versions | Steps 7 returns `NBA_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND`; pipeline can only write SHADOW | A governed replay run whose `certification_decision` returns CERTIFIED, followed by an explicit reviewed promotion of A, B, C |
| Any NBA row in `wow_team_event_specialist_certifications` | Step 5 returns `TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED` | New governed insert; no producer exists in-repo |
| `independent_verification_status='PASS'` with a real `verification_receipt` | The field is validated, never generated; `{}` fails the publish gate's intent and no reviewer evidence exists | Independent QA / SIRT lane receipt |
| `"NBA"` in `CERTIFIED_TEAM_EVENT_SPORTS` | Step 4 holds before any registry read | Reviewed code change accompanying the evidence |
| A production `calibration_artifact_resolver` | Step 9 holds; the parameter defaults to `None` | Bridge code, not data |
| An approved dynamic-bounds policy | Step 10 holds unconditionally | Governance decision on calibrated lower-bound method |
| Current-pregame feature hydration for a live slate | Step 2 holds; `wow_nba_pregame_feature_rows` is FK-bound to settled `wow_nba_training_games`, and hydration is described as research/training only | New hydration path for the current slate |
| `wow_runtime_capabilities.NBA_EVENT_PROBABILITY = 'AVAILABLE'` | Daily route capability gate fails | Capability flip after the rest is proven |
| A canonical `input_contract_version` value for NBA | Free-text field required non-null; no repository constant defines it | Deployment decision, recorded with the certification |

Two accuracy notes for the certification review:

- **No ECE gate exists in code.** `ece` is recorded on the calibrator row but
  `certification_decision` does not test it. Any ECE threshold applied at
  certification time is an additional governance requirement, not an existing
  repository gate.
- **`wow_specialist_registry` has no DDL in this repository.** The migration
  inserts into it, so it is presumed pre-existing in the production schema; its
  definition was not verifiable here.

## 5. Disposition

The NBA path is not "flags off". It is: real settled corpus (present), fitted
artifact (mechanism present), chronological calibration (present), quantified
gates (present), then **two registries that nothing currently writes at
CERTIFIED state, one code manifest that omits NBA, one unapproved bounds policy,
and one missing current-pregame hydration path**.

Steps A–C can be produced by existing code under a governed replay. Steps D–G
each require either independent verification evidence or a model-governance
decision, so no engineering change in this session attempted them.

`can_execute=false` and `probability_publishable=false` remain in force for the
NBA lane.
