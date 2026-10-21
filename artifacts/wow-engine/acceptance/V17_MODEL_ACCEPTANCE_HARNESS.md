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
| NFL props | PASSING/RUSHING/RECEIVING_YARDS only; exact line, direction, event identity, verified ESPN alias (#1894), immutable receipt, no threshold weakening |
| NBA props | POINTS, POINTS_REBOUNDS: HTTP 200 + `MODEL_UNAVAILABLE` stays `HELD_TYPED`, never success |
| NFL/NCAAF spreads | `SCORE_MARGIN_COVER` specialist only, signed exact line, push rule on integer lines, settlement rule, chronology; unsupported routes are typed HOLD and may not borrow ML probabilities |
| Pregame vs live | started events invalidate pregame requests; live requests need a recognized live event and a certified live specialist (no pregame reuse) |

Typed failures are kept distinct: `MODEL_UNAVAILABLE`, `MODEL_SCORER_FAILED`,
`MODEL_INPUTS_INSUFFICIENT`, `MODEL_OUTPUT_INVALID`, plus `RECEIPT_STALE`,
`RECEIPT_FOREIGN`, `LIVE_EVENT_UNRECOGNIZED`, `RUN_SHA_MISMATCH`,
`MONEY_EVIDENCE_ABSENT`, `IDENTITY_CONFLICT` and others.

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
