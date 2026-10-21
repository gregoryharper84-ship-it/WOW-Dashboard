# V17 model acceptance harness (#1935)

Dry-run-only, fixture-driven end-to-end acceptance harness for the V17 routes
in workstream #4. Module: `v17/model_acceptance_harness.py`.

`can_execute=false`. The harness never scores, never changes a probability,
never writes, never calls the network, never triggers deploys and never
places wagers. It audits captured route evidence and reports a typed verdict.

## Acceptance matrix covered by fixtures

| Route | What is enforced |
|---|---|
| NFL moneyline (LLP) | fitted specialist, calibrated lower bound ≥ declared threshold ≥ publication-guard floor, exact prediction/receipt persistence, #1896 artifact-vs-prediction publishability contradiction |
| NFL props | PASSING/RUSHING/RECEIVING_YARDS only; exact line, direction, event identity, the real nested ESPN alias contract (#1894), immutable receipt, no threshold weakening |
| NBA props | POINTS, POINTS_REBOUNDS: HTTP 200 + `MODEL_UNAVAILABLE` stays `HELD_TYPED`, never success |
| NFL/NCAAF spreads | `SCORE_MARGIN_COVER` specialist only, signed exact line, push rule on integer lines, settlement rule, chronology; unsupported routes are typed HOLD and may not borrow ML probabilities |
| Pregame vs live | started events invalidate pregame requests; live requests need a recognized live event and a certified live specialist (no pregame reuse) |

Typed failures are kept distinct: `MODEL_UNAVAILABLE`, `MODEL_SCORER_FAILED`,
`MODEL_INPUTS_INSUFFICIENT`, `MODEL_OUTPUT_INVALID`, plus `RECEIPT_STALE`,
`RECEIPT_FOREIGN`, `LIVE_EVENT_UNRECOGNIZED`, `RUN_SHA_MISMATCH`,
`MONEY_EVIDENCE_ABSENT`, `IDENTITY_CONFLICT` and others.

## Receipt contract: two typed route adapters, not one shared shape

The prop-canary hash ledger is not imposed on ML/spread routes, and the
ML/spread governed-row contract is not imposed on props — each route family's
`receipt` is validated against its own real persisted schema
(`ROUTES[route]["receipt_kind"]`):

- **`PROP_ACTION_CANARY`** (NFL/NBA props) — matches
  `wow_prop_action_canary_receipts` (migrations/20260917_prop_action_canary_receipts.sql):
  `receipt_id`, `action_operation_id`, `action_request_fingerprint`,
  `prediction_id`, `sport`/`stat_type`/`feature_schema_version`,
  `model_family`/`model_artifact_version`/`artifact_checksum`,
  `calibrator_version`/`certification_id`/`calibration_evidence_hash`,
  `raw_model_probability`/`calibrated_probability`/`calibrated_lower_bound`,
  `reconciliation_status` (must be `PASS`), and `immutable_receipt_hash`.
  `prop_canary_identity_hash()` recomputes that hash from the receipt's own
  identity tuple to catch a **tampered hash** (`RECEIPT_HASH_INVALID`), a
  **wrong prediction linkage** (`RECEIPT_PREDICTION_LINKAGE_MISMATCH`), an
  **incompatible calibration identity** — a receipt whose hash is
  self-consistent with its own (forged) `certification_id` but disagrees with
  the canonical prediction's calibration lineage
  (`RECEIPT_CALIBRATION_IDENTITY_INCOMPATIBLE`) — and a **duplicate/replayed
  receipt**, where one persisted hash backs more than one case
  (`receipt_reconciliation.hash_duplicates`, mirroring the existing duplicate
  `receipt_id` check; each individual case can look well-formed on its own,
  so only a cross-case comparison catches the replay).
- **`GOVERNED_PREDICTION`** (NFL moneyline, NFL/NCAAF spreads) — matches
  `wow_predictions` (schema.sql): `prediction_id`, `event_id`,
  `calibrated_probability`/`calibrated_probability_lower_bound`/
  `calibrated_probability_upper_bound` (mirroring `chk_calibrated_bounds`
  exactly: lower bound `> 0`, upper bound `< 1`), `probability_publishable`,
  and `locked_at` — the real immutability signal (a before-update trigger
  blocks edits once set), not a self-attested `immutable: true` boolean.
  Missing/null `locked_at` is `RECEIPT_NOT_LOCKED`.

**Schema-shape checked, not database-constraint proven.** Every report
carries `receipt_evidence_class: FIXTURE_SCHEMA_SHAPE_CHECKED_NOT_DATABASE_CONSTRAINT_PROVEN`.
Recomputing a hash or checking a bounds/linkage invariant from fixture or
captured-canary fields can prove a receipt is internally self-consistent and
correctly linked — it can never prove Postgres's own unique-constraint or
trigger-enforced immutability guarantee, which only the database can certify
at write time.

## #1894 ESPN alias: the real nested `role_status` contract

The request's `evidence.role_status` (not a flat `espn_alias_verified`
boolean) mirrors `pick_request_runtime.py::_validate_evidence` exactly:
`canonical_event_id`, `provider_event_ids.ESPN`, `espn_athlete_id`, `status`,
`verified_canonical_event_id`, and `identity_binding_status`. The same typed
`PROP_EVENT_IDENTITY_CONFLICT:*` branches fire as in production:
`CANONICAL_EVENT_ID_MISMATCH`, `NFL_CANONICAL_EVENT_ID_UNVERIFIED`,
`NFL_CANONICAL_EVENT_ID_MISMATCH`, `IDENTITY_BINDING_NOT_PASS`.

## Row reconciliation naming

Per-row terminal entries use `row_key`/`terminal_status` (matching
`pick_request_runtime_core.py` and `agent_runtime_api.py`), not a
harness-invented `row_id`/`state` pair. The aggregate
`rows_in`/`rows_completed`/`rows_held`/`rows_rejected` counters were already
correct and are unchanged.

## Per-route output

Each row reports environment (GitHub SHA, Render live SHA / deploy ID,
Supabase project ref, production-binding evidence), Action route + HTTP status
+ latency, canonical event/line/side/direction, model artifact/version,
calibration ID and lower bound, `probability_publishable`, `rank_eligible`,
receipt ID, reconciliation counts, `first_failing_stage` and `exact_blocker`.

Stages run in order: ENVIRONMENT → ROUTE → IDENTITY → LIFECYCLE → MODEL →
CALIBRATION → PUBLICATION → RECEIPT → RECONCILIATION. The money audit is a
separate stage: when only the money audit fails, `probability_status` stays
`PROBABILITY_ACCEPTED`. Held rows report `MONEY_AUDIT_NOT_RUN`.

Bundle-level checks: route/stat/live coverage, `cases = accepted + held + rejected`,
duplicate receipt IDs, and fixture expectation mismatches. Secret-shaped keys
are redacted from reports.

## Usage

```bash
cd artifacts/wow-engine
python -m pytest -q test_v17_model_acceptance_harness.py
python -m v17.model_acceptance_harness tests/fixtures/model_acceptance_v17/bundle.json \
  --json-out acceptance/v17-model-acceptance-harness-sample.json \
  --md-out acceptance/v17-model-acceptance-harness-sample.md
# regenerate fixtures after editing the builder:
python tests/fixtures/model_acceptance_v17/build_bundle.py tests/fixtures/model_acceptance_v17/bundle.json
```

Exit code is 0 only when the bundle is `HARNESS_FIXTURES_CONSISTENT`.
Samples: `v17-model-acceptance-harness-sample.json` / `.md` (a test asserts
they are reproducible from the fixtures).

## Post-deploy canary (designed, not run)

`plan_post_deploy_canary()` only describes the opt-in authenticated dry-run
canary. Running it requires a separately authorized launch after an
authorized Render release, an independent SIRT/QA operator, OIDC-authenticated
dry-run Action calls, and a captured bundle with `environment.mode=CANARY`
(which then requires `render_live_sha == github_sha` plus deploy ID, Supabase
project ref and binding evidence). Production writes, deploy triggers and
wagers are forbidden.

## Limits

Fixture evidence only. `production_acceptance` is always
`NOT_PERFORMED_REQUIRES_INDEPENDENT_SIRT_QA` and `self_certified=false`.
The publication floor is pinned to
`team_event_official_publication_guard.WINNER_WATCH_MIN_LOWER_BOUND`; the
harness does not define governance thresholds.
