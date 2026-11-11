# V17 Fitted-Model Certification Replay and Promotion

Status: **IMPLEMENTED, AUDIT-ONLY, NON-AUTHORIZING**

`can_execute=false`
`DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`

## Purpose

One deterministic lifecycle step for every fitted team/event and player-prop
artifact, across all 13 required sports:

```text
frozen fitted artifact + frozen replay corpus
  -> candidate-bound deterministic replay receipt
  -> ratified promotion-policy gate
  -> independent authorization gate
  -> governed-apply packet (dry run, privileged application required)
```

This layer composes the existing sport-specific certification contracts. It
does not replace the per-sport scoring, calibration, settlement, publication, or
terminal-reducer authorities.

## Authority invariants

The module can never:

- certify or promote automatically;
- activate or register an artifact;
- change `probability_publishable` or any publication state;
- rank a selection;
- place a wager or market order;
- substitute market-implied, qualitative, or synthetic probability;
- rewrite `MODEL_UNAVAILABLE`, `MODEL_SCORER_FAILED`,
  `MODEL_INPUTS_INSUFFICIENT`, or `MODEL_OUTPUT_INVALID`.

Every receipt and packet keeps:

```text
automatic_certification=false
automatic_promotion=false
probability_publishable=false
rank_eligible=false
can_execute=false
```

## Deterministic replay contract

`v17.fitted_model_certification_promotion.replay_fitted_artifact` verifies:

1. exact artifact, model-version, training-code, training-dataset, artifact,
   calibrator, and feature-schema identity;
2. the canonical digest of the complete frozen prediction corpus
   (`expected_corpus_sha256`);
3. disjoint chronological `TRAIN` / `CALIBRATION` / `TEST` partitions with
   per-partition row counts and at least one settled test row;
4. pre-event feature timing (`feature_as_of < event_start_time`) and no event
   crossing partition boundaries;
5. one controlling specialist per row, matching the artifact identity;
6. per-row immutable source-manifest hash and `market_features_used=false`;
7. strict probability domain `0 < p < 1`, multi-outcome normalisation, and
   governed lower-bound ordering;
8. preserved `PUSH` / `VOID` states (never silently dropped and never used as
   binary targets);
9. deterministic reproduction of stored Brier, log-loss, and ECE metrics;
10. explicit reporting of stored metrics that were not reproduced
    (`unverified_stored_metrics`).

Binary lanes carry one class-positive probability and are not required to sum to
1. Multi-outcome lanes must normalise to 1 within `1e-9`.

A receipt is content-addressed by `receipt_sha256`. Replays are deterministic:
the same artifact and corpus produce the same digest.

## Promotion decision

A promotion policy is never invented. The caller must supply a `RATIFIED`
policy with explicit thresholds:

```text
min_settled_rows
min_test_rows
max_brier
max_log_loss
max_ece
require_test_holdout
ratified_by
ratified_at
scope_sport (ALL or exact sport)
scope_surface (ALL, TEAM_EVENT, or PROP)
```

Statuses, in fail-closed order:

| Status | Meaning |
| --- | --- |
| `PROMOTION_BLOCKED` | replay failed, live/promoted state is inconsistent, publication flag is premature, or another structural gate failed |
| `PROMOTION_POLICY_REVIEW_REQUIRED` | no policy, unratified policy, ambiguous policy, or scope mismatch |
| `REMAIN_SHADOW` | policy is ratified but required rows or metric thresholds are not met |
| `PROMOTION_READY_FOR_INDEPENDENT_REVIEW` | replay and policy pass; no authorization yet |
| `PROMOTION_AUTHORIZATION_INVALID` | reviewer decision is missing, rejects, is bound to another receipt/artifact, is before policy ratification, or is not independent |
| `PROMOTION_APPROVED_FOR_GOVERNED_APPLY` | exact receipt, policy, and independent authorization agree; emits a dry-run apply packet |
| `PROMOTION_ALREADY_APPLIED` | the registry already reports the certified state and the exact authorization matches |

An authorization must bind:

- the exact replay `receipt_sha256`;
- the exact artifact checksum;
- the policy ID;
- an `APPROVE` decision at or after policy ratification;
- a reviewer distinct from the artifact author;
- an independent reviewer role.

The approved packet is a proposal only:

```text
operation=GOVERNED_FITTED_MODEL_PROMOTION
dry_run=true
requires_privileged_application=true
publication_authorized=false
target probability_publishable=false
target can_execute=false
```

Applying the packet is a separate privileged, reviewed registry operation. This
module never performs it.

## Cross-sport matrix

`build_cross_sport_matrix` always emits the full
`13 sports × {TEAM_EVENT, PROP}` inventory (26 lanes), including lanes with no
artifact:

- `MODEL_BUILD_REQUIRED` where the development manifest declares no pipeline;
- `CANDIDATE_EVIDENCE_MISSING` where a pipeline exists but no frozen evidence
  packet was supplied.

No sport is silently omitted. The matrix digest is deterministic and every lane
carries `can_execute=false`.

## Runtime surface

Authenticated internal routes, mounted on the active V17 production entrypoint
(`api_ncaaf_acceptance:app`) only when `WOW_V17_ACTIVE=1`:

```text
POST /internal/v17/fitted-model-certification/replay
POST /internal/v17/fitted-model-certification/promotion
POST /internal/v17/fitted-model-certification/matrix
```

The replay and promotion routes persist append-only receipts through the
service-role client. A persistence failure does not turn into a pass: the
response sets `audit_complete=false`, and an approved promotion is downgraded to
`PROMOTION_BLOCKED` with `apply_packet=null`.

## Receipt tables

Migration:
`migrations/20261111_v17_fitted_model_certification_replay_promotion.sql`

- `public.wow_fitted_model_certification_replay_receipts`
- `public.wow_fitted_model_promotion_receipts`

Both tables:

- enforce `probability_publishable=false` and `can_execute=false`;
- enforce content-addressed unique identities;
- reject update and delete through an append-only trigger;
- enable RLS;
- revoke all access from `anon` and `authenticated`;
- grant only `select, insert` to `service_role`.

A corrected result is a new receipt, never an edit.

## Tests

```bash
cd artifacts/wow-engine
PYTHONPATH=. python -m pytest tests/test_v17_fitted_model_certification_promotion.py -q
```

The suite covers:

- hand-computed binary metric reproduction;
- multi-outcome replay and normalisation;
- corpus/artifact tamper detection;
- temporal leakage and partition overlap;
- probability-domain, lower-bound, push/void preservation;
- market-feature and source-provenance rejection;
- unverified-metric transparency;
- ratified-policy, metric-gate, scope, and ambiguity behavior;
- independent authorization, author separation, and pre-ratification rejection;
- dry-run apply packet with no publication/execution authority;
- already-promoted idempotency;
- 13-sport × 2-surface matrix completeness;
- receipt idempotency and fail-closed persistence;
- route installation idempotency;
- append-only/RLS migration invariants.
