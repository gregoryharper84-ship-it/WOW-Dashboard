# NHL Team-Event Specialist Certification (Design A)

**Status:** DESIGN APPROVED — implementation plan gated on review of this file.  
**Scope:** NHL regular-season moneyline / game-win probability specialist only.  
**Safety:** `can_execute=false` at every layer. No live wagering. No fabricated probabilities. No governance bypass.

## Problem

PR #2637 restored the NHL fitted-specialist engineering path (FULL_SEASON schedule coverage, timeline receipts, reingest leases, fail-closed source-rights/goalie HOLDs). That merge is **working, not certified**.

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

A routable bridge, research candidate, or merged restoration PR is not certification.

## Approaches considered

1. **Full production certification (selected)** — clear engineering prerequisites, bound-producing calibrator, independent source review PASS, deterministic replay verification, immutable registry receipt, then separate catalog admission.
2. **Prospective / shadow only** — checklist + shadow capture without registry CERTIFIED / catalog admission. Evidence only; leaves the specialist uncertified.
3. **Engineering readiness only** — finish reingest/rights/schedule/provenance and stop. Unblocks later work; does not certify.

## Success criteria

NHL is certified when **all** of the following are true:

1. An immutable row exists in `wow_team_event_specialist_certifications` for sport `NHL` with:
   - `certification_status=CERTIFIED`
   - `independent_verification_status=PASS`
   - `probability_publishable=true`
   - `can_execute=false`
   - artifact and calibration sha256 fields valid (64-char lowercase hex)
2. Service-role RPC `wow_v17_active_team_event_certification('NHL', …)` returns `ok=true` and parses via `parse_certification`.
3. A separate reviewed change admits NHL into `CERTIFIED_TEAM_EVENT_SPORTS` (static catalog), matching the certified controlling specialist identity.
4. Exact-head CI proves fail-closed behavior for missing receipt, identity calibrator, failed source review, and digest/provenance gaps.
5. Row-level goalie/status HOLDs remain enforced; certification does not waive them.

Anything short of (1)–(3) is **not certified**, even if engineering gates are green.

## Non-goals

- Enabling `can_execute` or live market orders
- Certifying NHL props, totals, spreads, or playoff regimes
- Promoting `IDENTITY_RAW_PROBABILITY_V1` as a certified calibrator
- Self-asserting certification from candidate/research rows
- Weakening `V17_TERMINAL_REDUCER`, source-rights, or goalie gates
- Fabricating or market-implying probabilities when the fitted model is unavailable

## Authority and contracts

Reuse existing contracts; do not invent a parallel certification authority.

| Concern | Authority |
|---|---|
| Receipt shape / parse | `v17/fitted_team_event_certification.py` |
| Registry storage / active resolve | `wow_team_event_specialist_certifications` + `wow_v17_active_team_event_certification` |
| Terminal gates | `v17/multisport_team_event_governance.py` |
| Static catalog | `CERTIFIED_TEAM_EVENT_SPORTS` in `v17/team_event_capability_manifest.py` |
| Catalog vs registration | `v17/team_event_model_registry_audit.py` (`certification_state`) |
| Candidate replay evidence | `v17/candidate_certification_evidence.py` |
| Team-event replay route | `v17/team_event_certification_replay.py` |
| NHL scorer / FULL_SEASON | `v17/nhl_fitted_specialist.py` |
| Reingest / leases | `v17/nhl_source_reingest.py`, `nhl_candidate_pipeline.py` |
| Source entitlements | `v17/model_source_entitlements.py` |

## Ordered gates

Later gates cannot compensate for earlier failures. No gate may be cleared by lowering a threshold.

### G1 — Source corpus and FULL_SEASON coverage

**Requires**

- Rights-approved source path (no `NHL_SOURCE_RIGHTS_HOLD`)
- Independent schedule digest (not settled-row self-attest)
- FULL_SEASON timeline receipt persisted in `wow_nhl_season_timeline_receipts`
- Provenance present on settled training corpus rows used for the candidate
- Lease discipline via `wow_nhl_source_reingest_leases` (no double-acquire races)

**Clears when** a durable timeline receipt exists for the training season scope with matching `schedule_digest`, and the candidate corpus is drawn only from provenance-complete rows.

**Fails closed on** missing rights, missing digest, incomplete schedule coverage, or provenance gaps.

### G2 — Fitted candidate from that corpus

**Requires**

- Candidate row in `wow_d1_candidate_artifacts` for NHL moneyline / `NHL_REGULAR_SEASON_LOGISTIC_V1` (or the exact family named on the eventual receipt)
- Frozen training dataset hash, artifact checksum, feature schema version
- `research_screen_pass` may be true; that alone does not clear later gates
- `automatic_certification=false`, `automatic_promotion=false`, `can_execute=false`

**Clears when** a single candidate identity is selected for certification work and its hashes are reproducible.

### G3 — Bound-producing calibrator

**Requires**

- Calibration method that produces governed calibrated probabilities and lower bounds required by `validate_prediction_package` / multisport calibration blockers
- `calibration_artifact_id` + `calibration_sha256` suitable for the certification receipt
- Explicit rejection of promoting `IDENTITY_RAW_PROBABILITY_V1` for NHL certification

**Clears when** the candidate’s calibrator payload (or linked calibrator artifact) is a non-identity method with a stable sha256 and sufficient calibration sample for the existing `MIN_CALIBRATION_N` gate.

**Fails closed on** identity/raw-only calibrators, missing fingerprint, or fit-end leakage.

### G4 — Independent source review PASS

**Requires**

- `source_review_status` transition from `REQUIRED` to an accepted PASS token under the existing source-review / entitlement workflow
- Review is independent of the engineering agent that produced the candidate
- Entitlement rules in `model_source_entitlements.py` remain authoritative

**Clears when** the selected candidate row records an accepted source-review PASS for its source policy.

**Fails closed on** self-asserted PASS, missing review, or REJECT/INVALID outcomes.

### G5 — Independent verification PASS (replay)

**Requires**

- Deterministic candidate-bound replay via `candidate_certification_evidence` and/or team-event certification replay
- Frozen corpus bound to candidate `created_at` (append-only ledger discipline already encoded)
- Verification receipt persisted (evidence receipt table / verification_receipt jsonb on certification insert)
- `independent_verification_status=PASS` only after replay hashes and metrics match

**Clears when** replay PASS is recorded against the exact candidate artifact + dataset hashes.

**Fails closed on** corpus ambiguity, hash mismatch, or incomplete outcomes.

### G6 — Immutable certification registry write

**Requires**

- Service-role insert into `wow_team_event_specialist_certifications` with fields required by `FittedTeamEventCertification`
- `certification_status=CERTIFIED`
- `independent_verification_status=PASS`
- `probability_publishable=true` only under the table’s publish gate
- `can_execute=false`
- No update/delete (immutable trigger)

**Clears when** `wow_v17_active_team_event_certification('NHL', league_scope)` returns `ok=true` and `parse_certification` accepts the payload.

**Fails closed on** missing fields, bad hashes, non-PASS verification, or client-role writes.

### G7 — Static catalog admission (separate change)

**Requires**

- Distinct reviewed PR after G6 is proven on the target environment
- Add NHL → controlling specialist in `CERTIFIED_TEAM_EVENT_SPORTS`
- Do not treat `DECLARED_TEAM_EVENT_SPECIALIST_IDENTITIES` as certification

**Clears when** `certification_state('NHL', registered=True)` returns `CERTIFIED` with the catalog specialist id, and runtime adapters resolve the live receipt for package validation.

### G8 — Exact-head proof and remaining HOLDs

**Requires**

- Exact-head unit/integration tests for G3–G7 fail-closed paths
- Proof that goalie status / observed-at / identity snapshot HOLDs still block publication when evidence is incomplete
- Independent QA / SIRT acceptance of the certification path (engineering cannot self-approve the probability)

**Clears when** CI on the certification/catalog heads is green for the scoped tests, and independent acceptance records PASS without waiving row-level HOLDs.

## Architecture

```text
Rights-approved NHL sources
  -> schedule_digest + FULL_SEASON timeline receipt + lease
  -> provenance-complete settled corpus
  -> fitted candidate (research only)
  -> bound-producing calibrator artifact
  -> independent source review PASS
  -> candidate-bound replay verification PASS
  -> immutable wow_team_event_specialist_certifications row
  -> wow_v17_active_team_event_certification resolves receipt
  -> separate PR: CERTIFIED_TEAM_EVENT_SPORTS admits NHL
  -> runtime package must match receipt; V17_TERMINAL_REDUCER remains sole terminal authority
  -> row-level goalie/status HOLDs still apply
  -> can_execute=false always
```

## Implementation slices (for the plan, not this file’s execution)

Work proceeds as ordered slices; each slice ends fail-closed if its gate does not clear.

1. **Engineering prerequisites** — durable FULL_SEASON reingest with receipts/leases/provenance on the validation (then production) path; rights HOLD remains fail-closed.
2. **Calibrator + candidate refresh** — replace identity calibrator with a bound-producing certified-capable method; keep candidate unpromoted until G4–G6.
3. **Source review + replay** — dispatch/complete independent review; persist verification receipt.
4. **Registry certification insert** — service-role only; prove RPC resolve.
5. **Catalog admission PR** — NHL in `CERTIFIED_TEAM_EVENT_SPORTS` only after G6 proof.
6. **Acceptance** — exact-head CI + independent QA/SIRT; document any remaining operational HOLDs without clearing them in code.

## Test requirements

- Exact-head tests: receipt parse success/failure; calibration method rejection for identity; source-review missing/REJECT; schedule digest missing; registry resolve miss; package/receipt mismatch blockers.
- Negative tests must not be deleted or weakened to green CI.
- No production credentials in CI; use ephemeral/validation fixtures.
- Catalog admission tests assert NHL is absent until the G7 change, then present with matching specialist identity.

## Stop conditions

Stop and request human/governance review (do not guess) when:

- A change would alter probability semantics rather than plumbing
- A test must be weakened to pass
- The only available calibrator is identity/raw
- Independent source review or verification cannot PASS on evidence
- `can_execute` would become true
- Production credentials would need to be exposed
- Catalog admission is requested before G6 RPC proof

## Regression focus

- Do not collapse scorer/input/output failures into `MODEL_UNAVAILABLE`
- Do not replace missing specialist output with L5/L10, market implied, or qualitative fallback
- Do not let candidate `research_screen_pass` imply certification
- Do not waive `NHL_GOALIE_STATUS_NOT_MODEL_READY` via certification receipt
- Preserve lease-before-corrupt and one-pass corpus acquire behavior from #2637

## Open operational dependencies (not design ambiguity)

These are execution dependencies, not unresolved design choices:

- Live source-rights approval must exist before G1 can clear in an environment
- Independent source-review and SIRT principals must act; engineering cannot mint PASS
- Bound-producing calibrator fit requires sufficient forward/calibration evidence under existing minimums
- Catalog PR is intentionally sequenced after registry proof
)
