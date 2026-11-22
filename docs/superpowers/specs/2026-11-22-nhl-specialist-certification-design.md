# NHL Team-Event Specialist Certification (Design A)

**Status:** REVIEWED — CHANGES REQUESTED (resolving WOW Betting Intelligence design review on HEAD `78f9053`; not Independent QA/SIRT acceptance or certification).  
**Scope:** NHL regular-season moneyline / game-win probability specialist only.  
**Safety:** `can_execute=false` at every layer. No live wagering. No fabricated probabilities. No governance bypass.  
**Incident:** `#1952`

## Problem

PR #2637 restored the NHL fitted-specialist engineering path (FULL_SEASON schedule coverage, timeline receipts, reingest leases, fail-closed source-rights/goalie HOLDs). That merge is **WORKING_NOT_COMPLETE**, not certified. CI green and static catalog membership are never live model certification.

Validation project (`iczfhsmjrrafhvcpmqhr`) evidence at design time:

| Evidence | Observation |
|---|---|
| Timeline / lease tables | Present |
| `wow_nhl_season_timeline_receipts` | 0 rows |
| `wow_nhl_source_reingest_leases` | 0 rows |
| NHL `wow_d1_source_events` | 11991 rows; 0 with `provenance_version`; 0 for season `20262027` |
| NHL candidates | `lifecycle_state=CANDIDATE`, `source_review_status=REQUIRED`, calibrator `IDENTITY_RAW_PROBABILITY_V1` |
| `wow_v17_active_team_event_certification('NHL')` | `TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED` |
| `wow_team_event_specialist_certifications` | 0 rows |
| Static catalog `CERTIFIED_TEAM_EVENT_SPORTS` | MLB only |

Contract mismatches that block naive reuse:

- `candidate_certification_evidence.py` accepts only `source_policy_id=TEAM_STATE_DYNAMIC_PRIOR_ONLY_V1` and model families ending `_DYNAMIC_TEAM_STATE_LOGIT_V2`. NHL uses `NHL_PUBLIC_WEB_API` / `NHL_REGULAR_SEASON_LOGISTIC_V1` and currently fails with `CANDIDATE_REPLAY_CONTRACT_UNSUPPORTED`.
- `team_event_certification_replay.py` consumes already-persisted PASS receipts; it does not generate an NHL candidate-bound deterministic replay.
- `nhl_fitted_specialist.py::score_nhl_fitted_candidate` returns research/candidate flags (`calibration_status=CANDIDATE`, `calibration_artifact_certified=false`, `calibration_health_status=RESEARCH_SCREEN_PASS`, `probability_publishable=false`, `rank_eligible=false`) and sets `calibration_artifact_fingerprint` to the **model** `artifact_checksum`, not a calibrator SHA. Shared governance and `validate_prediction_package` require the certified envelope.
- Incident `#1952` records a historical training/calibration/test count mismatch (8,580 claimed vs 6,128 reproducible). That disputed candidate must not be reused or rewritten.

A routable bridge, research candidate, or merged restoration PR is not certification.

## Approaches considered

1. **Full production certification (selected)** — clear engineering prerequisites, bound-producing calibrator with chronological proof, NHL-specific independent replay, independent acceptance **before** registry write, immutable receipt, separate catalog admission, then production deployed-SHA / current-slate proof.
2. **Prospective / shadow only** — checklist + shadow capture without registry CERTIFIED / catalog admission. Evidence only; leaves the specialist uncertified.
3. **Engineering readiness only** — finish reingest/rights/schedule/provenance and stop. Unblocks later work; does not certify.

## Success criteria

NHL is certified only when **every** criterion below is true. Meeting a prefix of the list (for example registry + catalog alone) is **not** certification.

1. A **new** provenance-complete candidate exists whose training/calibration/test row counts reconcile exactly to frozen corpus and validation metrics (no reuse of the disputed `#1952` count-mismatch candidate; no rewrite of its immutable evidence).
2. A bound-producing calibrator artifact exists with fit-end timestamp, chronological holdout integrity, and documented pre/post Brier, log loss, calibration bias, and lower-bound reliability; method is not `IDENTITY_RAW_PROBABILITY_V1`.
3. Independent source review PASS for that candidate’s source policy, by a principal independent of the engineering author.
4. An NHL-specific candidate-bound deterministic replay verifier records an immutable PASS receipt (hashes + metrics), with independent author/reviewer provenance. Generic dynamic-team-state replay is not used as a substitute.
5. Authorized exact-head Independent QA / governance review PASS for the certification change set **before** any registry CERTIFIED insert.
6. An immutable row exists in `wow_team_event_specialist_certifications` for sport `NHL` with `certification_status=CERTIFIED`, `independent_verification_status=PASS`, `probability_publishable=true`, `can_execute=false`, and valid artifact/calibration sha256 fields; RPC `wow_v17_active_team_event_certification('NHL', …)` returns `ok=true` and parses via `parse_certification`.
7. Independent post-write resolution proves the active receipt matches the verified artifact/calibrator identities.
8. A separate reviewed change admits NHL into `CERTIFIED_TEAM_EVENT_SPORTS`, matching the certified controlling specialist identity.
9. A server-only, registry-backed certified scoring adapter emits the exact certified prediction envelope (including home and away bound mappings) without flipping flags from request data or mere catalog membership.
10. Production proof: deployed SHA carries the certification/catalog path; a fresh authenticated current-slate Action/scorer receipt scores under the certified envelope with goalie/status HOLDs still enforced.
11. Exact-head CI proves fail-closed behavior for missing NHL replay support, identity calibrator, failed source review, digest/provenance gaps, package/receipt mismatch, and premature catalog admission.
12. Status remains `WORKING_NOT_COMPLETE` until (1)–(11) all pass; CI green alone never clears production certification.

## Non-goals

- Enabling `can_execute` or live market orders
- Certifying NHL props, totals, spreads, or playoff regimes
- Promoting `IDENTITY_RAW_PROBABILITY_V1` as a certified calibrator
- Self-asserting certification from candidate/research rows
- Weakening `V17_TERMINAL_REDUCER`, source-rights, or goalie gates
- Fabricating or market-implying probabilities when the fitted model is unavailable
- Reusing or rewriting the disputed `#1952` candidate evidence
- Treating `team_event_certification_replay.py` alone as NHL replay generation
- Flipping `calibration_artifact_certified` / `probability_publishable` from request metadata or catalog membership alone

## Authority and contracts

Reuse existing contracts; do not invent a parallel certification authority.

| Concern | Authority |
|---|---|
| Receipt shape / parse | `v17/fitted_team_event_certification.py` |
| Certified package envelope | `validate_prediction_package` + `v17/multisport_team_event_governance.py` |
| Registry storage / active resolve | `wow_team_event_specialist_certifications` + `wow_v17_active_team_event_certification` |
| Static catalog | `CERTIFIED_TEAM_EVENT_SPORTS` in `v17/team_event_capability_manifest.py` |
| Catalog vs registration | `v17/team_event_model_registry_audit.py` (`certification_state`) |
| Existing (non-NHL) candidate replay | `v17/candidate_certification_evidence.py` — **not** NHL-capable today |
| Receipt-consuming team-event replay | `v17/team_event_certification_replay.py` — consumes PASS; does not create NHL replay |
| NHL research scorer / FULL_SEASON | `v17/nhl_fitted_specialist.py` (candidate envelope today) |
| NHL certified scoring adapter (new) | Server-only adapter that resolves registry receipt and emits certified envelope; candidate rows stay immutable |
| NHL-specific replay verifier (new) | Narrow verifier for `NHL_PUBLIC_WEB_API` + `NHL_REGULAR_SEASON_LOGISTIC_V1` |
| Reingest / leases | `v17/nhl_source_reingest.py`, `nhl_candidate_pipeline.py` |
| Source entitlements | `v17/model_source_entitlements.py` |

## Ordered gates

Later gates cannot compensate for earlier failures. No gate may be cleared by lowering a threshold. **Independent acceptance precedes irreversible registry/catalog promotion.**

### G1 — Source corpus: training coverage vs current-game hydration (separated)

#### G1a — Historical model-training FULL_SEASON coverage

**Requires**

- Rights-approved source path (no `NHL_SOURCE_RIGHTS_HOLD`)
- Independent schedule digest (not settled-row self-attest)
- FULL_SEASON timeline receipt for the **training** season scope in `wow_nhl_season_timeline_receipts`
- Provenance present on settled training corpus rows used for the new candidate
- Lease discipline via `wow_nhl_source_reingest_leases`

**Clears when** a durable training-scope timeline receipt exists with matching `schedule_digest`, and the candidate corpus is drawn only from provenance-complete rows whose counts reconcile to metrics.

#### G1b — Current-game hydration for pregame acceptance

A historical FULL_SEASON training receipt does **not** supply current 2026–27 pregame features.

**Requires**

- Current-season (`20262027` or the live season id) event identity present in source events
- Season-appropriate schedule/status and prior-game feed for those events
- Season-appropriate timeline receipt for the current season scope
- Confirmed fresh goalie snapshot (`goalie_status` in allowed set, `goalie_status_observed_at`, `goalie_identity_snapshot_id`) for the final real pregame scorer acceptance test
- Rights remain fail-closed

**Clears when** current-season hydration evidence exists independently of the training receipt and can drive one authenticated pregame score attempt under HOLDs.

**Fails closed on** missing rights, missing digest, incomplete schedule coverage, provenance gaps, or treating G1a as sufficient for G1b.

### G2 — New fitted candidate from the reconciled corpus

**Requires**

- **New** candidate row in `wow_d1_candidate_artifacts` for NHL moneyline / `NHL_REGULAR_SEASON_LOGISTIC_V1`
- Frozen training dataset hash, artifact checksum, feature schema version
- Exact reconciliation: `training_rows + calibration_rows + test_rows` equals frozen corpus size and matches validation metrics (no 8,580-vs-6,128 class mismatch)
- Do not reuse the disputed `#1952` candidate; do not rewrite its immutable evidence
- `research_screen_pass` may be true; that alone does not clear later gates
- `automatic_certification=false`, `automatic_promotion=false`, `can_execute=false`

**Clears when** one new candidate identity is selected and its hashes and split counts are reproducible.

### G3 — Bound-producing calibrator with chronological quality proof

**Requires**

- Non-identity calibration method that produces calibrated probabilities and lower/upper bounds required by `validate_prediction_package` and multisport calibration blockers
- Distinct `calibration_artifact_id` and `calibration_sha256` (never the model `artifact_checksum`)
- Explicit rejection of promoting `IDENTITY_RAW_PROBABILITY_V1`
- Actual `calibration_fit_end` timestamp with fit-end ≤ immutable model timestamp (no future leakage)
- Chronological holdout / split integrity with leakage and counterexample negative tests
- Documented metrics on the holdout: pre/post Brier, log loss, calibration bias, lower-bound reliability
- Previous-correct-prediction regression checks where the lane already defines them
- Sample size satisfies existing `MIN_CALIBRATION_N` **and** the chronological quality proofs above (n alone is insufficient)

**Clears when** the linked calibrator artifact is non-identity, sha256-stable, chronologically valid, and metric evidence is persisted for independent review.

**Fails closed on** identity/raw-only calibrators, missing fingerprint, fit-end leakage, unresolved split mismatch, or metrics-only claims without chronological proof.

### G4 — Independent source review PASS

**Requires**

- `source_review_status` transition from `REQUIRED` to an accepted PASS token under the existing source-review / entitlement workflow
- Review principal independent of the engineering author
- Entitlement rules in `model_source_entitlements.py` remain authoritative for `NHL_PUBLIC_WEB_API`

**Clears when** the selected new candidate records accepted source-review PASS for its source policy.

**Fails closed on** self-asserted PASS, missing review, or REJECT/INVALID outcomes.

### G5 — NHL-specific independent verification PASS (replay)

**Requires (new narrow NHL verifier)**

- A dedicated NHL candidate-bound deterministic replay path for `source_policy_id` / family pair `NHL_PUBLIC_WEB_API` + `NHL_REGULAR_SEASON_LOGISTIC_V1`
- Do **not** widen or misuse `candidate_certification_evidence.py`’s dynamic-team-state contract as a silent substitute
- Do **not** treat `team_event_certification_replay.py` as the generator of NHL PASS; it may only consume an already-persisted PASS receipt
- Frozen corpus bound to candidate `created_at` (append-only ledger discipline)
- Immutable PASS/FAIL receipt including:
  - independent author vs reviewer provenance
  - exact dataset hash, artifact checksum, calibrator sha256, feature schema, split counts
  - metric comparison against the candidate’s frozen validation metrics
- `independent_verification_status=PASS` only after hashes and metrics match

**Clears when** the NHL verifier persists a PASS receipt against the exact new candidate identities.

**Fails closed on** `CANDIDATE_REPLAY_CONTRACT_UNSUPPORTED` for NHL under the generic verifier, corpus ambiguity, hash mismatch, incomplete outcomes, or author==reviewer provenance.

### G5b — Independent acceptance before irreversible promotion

**Requires**

- Exact-head Independent QA / authorized governance review PASS on the certification change set
- Independent source-review + NHL replay PASS already recorded (G4, G5)
- No registry CERTIFIED insert and no catalog admission yet

**Clears when** independent acceptance at the exact head is recorded PASS.

**Fails closed on** missing incident field, missing independent source review, or engineering self-approval. Earlier `QA_CANONICAL_INCIDENT_MISSING_OR_AMBIGUOUS` on PR #2798 was an Independent QA check outcome (PR body lacked `Incident: #1952`); it must be re-evaluated by the next Independent QA cycle after the body fix, not waived and not mislabeled as a docs defect.

### G6 — Immutable certification registry write + post-write resolution

**Requires (only after G5b PASS)**

- Service-role insert into `wow_team_event_specialist_certifications` with fields required by `FittedTeamEventCertification`
- `certification_status=CERTIFIED`, `independent_verification_status=PASS`, `probability_publishable=true` under the table publish gate, `can_execute=false`
- `verification_receipt` jsonb references the NHL replay PASS receipt identities
- No update/delete (immutable trigger)
- Independent post-write resolution: RPC returns `ok=true`, `parse_certification` accepts payload, and resolved artifact/calibrator SHAs match G5

**Clears when** active RPC resolve + independent post-write check both PASS.

**Fails closed on** missing fields, bad hashes, non-PASS verification, client-role writes, or insert attempted before G5b.

### G6b — Certified-runtime scoring adapter (server-only)

**Requires**

- New server-only adapter (or explicit certified mode) that:
  - resolves the live registry receipt (never request-asserted certification)
  - loads the certified model + calibrator by receipt identities
  - emits the exact certified envelope expected by `validate_prediction_package` and multisport governance, including at minimum:
    - `raw_probability`, `calibrated_probability`, `lower_bound`, `upper_bound`
    - home and away calibrated probabilities / bounds with binary sum discipline
    - `model_timestamp`, `latest_material_update_timestamp`
    - `probability_package_valid=true` only when envelope is complete
    - `independent_verification_status=PASS` from receipt
    - `artifact_id`, `model_version`, `calibration_method`, `calibration_artifact_fingerprint` (= calibrator sha256, not model checksum)
    - `calibration_status=PASS`, `calibration_artifact_certified=true`, `calibration_health_status=PASS` only when evidence supports them
  - keeps candidate rows immutable; does not mutate research candidates into champions by flag flip
  - never sets publishable/certified flags from request data or from catalog membership alone
- Preserve existing row-level HOLDs (goalie status / observed-at / identity snapshot, rights)

**Clears when** exact-head tests prove both home and away bound mappings and fail-closed mismatches against the receipt.

### G7 — Static catalog admission (separate reviewed change)

**Requires**

- Distinct reviewed PR after G6 and G6b are proven on the target environment
- Separate independent review of the catalog change
- Add NHL → controlling specialist in `CERTIFIED_TEAM_EVENT_SPORTS`
- Do not treat `DECLARED_TEAM_EVENT_SPECIALIST_IDENTITIES` as certification

**Clears when** `certification_state('NHL', registered=True)` returns `CERTIFIED` with the catalog specialist id **and** runtime still requires the live receipt for package validation.

### G8 — Production proof (deployed SHA + current-slate receipt)

**Requires**

- Deployed production SHA includes the certification path, NHL verifier, and certified scoring adapter
- Fresh authenticated current-slate Action / scorer receipt under the certified envelope using G1b hydration
- Goalie/status HOLDs still block when evidence is incomplete
- Status label remains `WORKING_NOT_COMPLETE` until this production evidence passes

**Clears when** production deployed-SHA verification and the fresh current-slate certified score receipt both PASS without waiving HOLDs.

**Fails closed on** treating validation-only RPC success, CI green, or catalog membership as production certification.

## Architecture

```text
Rights-approved NHL sources
  -> G1a training FULL_SEASON timeline receipt + lease + provenance
  -> G1b current-season hydration + fresh goalie snapshot (separate)
  -> NEW fitted candidate with reconciled split counts (not disputed #1952 row)
  -> bound-producing calibrator + chronological quality proof
  -> independent source review PASS
  -> NHL-specific candidate-bound replay PASS receipt
  -> independent exact-head QA/governance acceptance PASS
  -> immutable wow_team_event_specialist_certifications insert
  -> independent post-write RPC resolution
  -> server-only certified scoring adapter (registry-backed envelope)
  -> separate PR: CERTIFIED_TEAM_EVENT_SPORTS admits NHL
  -> production deployed-SHA + fresh current-slate certified Action/scorer receipt
  -> V17_TERMINAL_REDUCER sole terminal authority; row-level HOLDs remain
  -> can_execute=false always
  -> WORKING_NOT_COMPLETE until production proof clears
```

## Implementation slices (for the plan, not this file’s execution)

Each slice ends fail-closed; owners and SHA/receipt proof are required in the implementation plan.

1. **G1a/G1b engineering prerequisites** — training receipts/leases/provenance and separate current-season hydration; rights HOLD fail-closed; no new provider spend beyond existing approved sources.
2. **G2/G3 new candidate + calibrator** — reconciled splits; chronological calibration quality; never reuse disputed candidate.
3. **G5 NHL replay verifier** — narrow contract for NHL policy/family; immutable PASS/FAIL receipt; negative tests for unsupported generic replay.
4. **G6b certified scoring adapter** — registry-backed envelope; home/away bounds; fail-closed vs receipt; candidate immutability preserved.
5. **G4 + G5b independent acceptance** — source review + exact-head QA/governance before any CERTIFIED insert.
6. **G6 registry write + post-write resolution** — service-role only after G5b.
7. **G7 catalog admission PR** — separate review after G6/G6b proof.
8. **G8 production proof** — deployed SHA + current-slate certified receipt; keep `WORKING_NOT_COMPLETE` until clear.

## Test requirements

- Exact-head negatives: NHL under generic `candidate_certification_evidence` remains unsupported; identity calibrator rejected; source-review missing/REJECT; schedule digest missing; split-count mismatch; fit-end leakage; package/receipt mismatch; calibrator fingerprint ≠ model checksum; premature catalog admission; request-asserted certification ignored.
- Home and away bound mapping positives under the certified adapter once G6b exists.
- Goalie HOLD still blocks publication with a valid receipt.
- Negative tests must not be deleted or weakened to green CI.
- No production credentials in CI; use ephemeral/validation fixtures.
- Catalog admission tests assert NHL is absent until the G7 change, then present with matching specialist identity.
- Production proof is out-of-CI evidence (deployed SHA + Action/scorer receipt), recorded explicitly.

## Stop conditions

Stop and request human/governance review (do not guess) when:

- A change would alter probability semantics rather than plumbing
- A test must be weakened to pass
- The only available calibrator is identity/raw
- Independent source review, NHL replay, or exact-head QA cannot PASS on evidence
- Registry insert is requested before G5b
- Catalog admission is requested before G6 post-write proof
- `can_execute` would become true
- Production credentials would need to be exposed
- The only path forward requires reusing or rewriting the disputed `#1952` candidate

## Regression focus

- Do not collapse scorer/input/output failures into `MODEL_UNAVAILABLE`
- Do not replace missing specialist output with L5/L10, market implied, or qualitative fallback
- Do not let candidate `research_screen_pass` imply certification
- Do not waive `NHL_GOALIE_STATUS_NOT_MODEL_READY` via certification receipt
- Preserve lease-before-corrupt and one-pass corpus acquire behavior from #2637
- Do not set `calibration_artifact_fingerprint` to the model checksum in the certified path
- Do not treat CI green or catalog membership as production certification

## Open operational dependencies (not design ambiguity)

These are execution dependencies, not unresolved design choices:

- Live source-rights approval must exist before G1a/G1b can clear in an environment
- Independent source-review, Independent QA, and SIRT principals must act; engineering cannot mint PASS
- Bound-producing calibrator fit requires sufficient chronological holdout evidence under existing gates
- Current-season (`20262027`) hydration must be acquired; validation currently shows zero such rows
- Catalog PR is intentionally sequenced after registry + certified-adapter proof
- Production deployed-SHA verification is required before clearing `WORKING_NOT_COMPLETE`
