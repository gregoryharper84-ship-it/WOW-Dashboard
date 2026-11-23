# NHL Specialist Certification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close Design A Fix C (NHL-specific deterministic certification replay) and Fix D (registry-backed certified scoring adapter) with exact-head tests, without certifying NHL, minting independent PASS, enabling `can_execute`, or rebuilding the specialist from scratch.

**Architecture:** Follow the soccer sport-owned verifier pattern: dispatch NHL candidates from `candidate_certification_evidence.py` into a narrow `nhl_certification_evidence.py` that replays `NHL_PUBLIC_WEB_API` / `NHL_REGULAR_SEASON_LOGISTIC_V1` only. Add a separate server-only `nhl_certified_scoring_adapter.py` that resolves `wow_v17_active_team_event_certification`, refuses request/catalog self-assertion, and emits the `validate_prediction_package` envelope while preserving candidate immutability and goalie HOLDs. Fix A/B remain operational prerequisites owned by Data Engineering / NHL Model Specialist and are fail-closed dependencies, not waived by this engineering PR.

**Tech Stack:** Python 3.11, FastAPI route dispatch, existing `train_binary_candidate` / `BinaryTrainingRow`, `fitted_team_event_certification`, pytest under `artifacts/wow-engine`.

## Global Constraints

- `can_execute=false` everywhere; never place live wagers or market orders
- `V17_TERMINAL_REDUCER` remains sole terminal authority
- Do not reuse or rewrite the disputed `#1952` candidate evidence
- Do not promote `IDENTITY_RAW_PROBABILITY_V1`
- Do not widen the generic dynamic-team-state replay contract to silently accept NHL
- Do not flip candidate `probability_publishable` / `calibration_artifact_certified` by mutating research rows
- Engineering cannot mint Independent source-review / QA / SIRT / Release PASS
- Disposition remains `WORKING_NOT_COMPLETE` until production proof (spec G8)
- Incident: `#1952`
- Spec: `docs/superpowers/specs/2026-11-22-nhl-specialist-certification-design.md`

---

### Task 1: NHL certification evidence verifier (Fix C)

**Files:**
- Create: `artifacts/wow-engine/v17/nhl_certification_evidence.py`
- Modify: `artifacts/wow-engine/v17/candidate_certification_evidence.py` (dispatch NHL before generic unsupported check)
- Create: `artifacts/wow-engine/tests/test_v17_nhl_certification_evidence.py`

**Interfaces:**
- Consumes: `candidate_certification_evidence._all_rows`, `_hash`, `_identity`, `_iso`; `binary_candidate_lifecycle.BinaryTrainingRow`, `train_binary_candidate`; `model_source_entitlements.source_readiness`
- Produces: `verify_nhl_candidate_certification_evidence(db, candidate_id) -> dict` with `status`, `blockers`, `replay_status`, `source_review_status`, `evidence_sha256`, `probability_publishable=False`, `can_execute=False`

- [ ] **Step 1: Write the failing tests**

```python
# artifacts/wow-engine/tests/test_v17_nhl_certification_evidence.py
from v17 import nhl_certification_evidence as nhl
from v17 import candidate_certification_evidence as entry

def test_research_verifier_never_authorizes_execution_or_publication():
    assert nhl.CAN_EXECUTE is False
    assert nhl.PROBABILITY_PUBLISHABLE is False

def test_dynamic_team_state_generic_path_still_rejects_nhl_without_dispatch(monkeypatch):
    # With dispatch installed, NHL must route to nhl verifier — not CANDIDATE_REPLAY_CONTRACT_UNSUPPORTED
    ...

def test_absent_candidate_blocks_without_receipt_write():
    ...

def test_identity_calibrator_records_replay_block_for_certification_bound_path():
    # Identity method may hash-match but certification-bound NHL evidence must surface
    # NHL_IDENTITY_CALIBRATOR_FORBIDDEN in blockers when claiming certification evidence PASS.
    ...

def test_split_must_be_3676_1226_1226_for_historical_target():
    ...

def test_dispatch_reuses_nhl_verifier(monkeypatch):
    expected = {"status": "CERTIFICATION_EVIDENCE_FAILED", "can_execute": False, "probability_publishable": False}
    monkeypatch.setattr(nhl, "verify_nhl_candidate_certification_evidence", lambda db, cid: expected)
    db = _Database([{"sport": "NHL", "model_family": "NHL_REGULAR_SEASON_LOGISTIC_V1"}])
    assert entry.verify_candidate_certification_evidence(db, "candidate-id") == expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd artifacts/wow-engine && python -m pytest tests/test_v17_nhl_certification_evidence.py -q`

Expected: FAIL (module missing / import error)

- [ ] **Step 3: Implement NHL verifier + dispatch**

`nhl_certification_evidence.py` constants:

```python
CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
VERIFIER_VERSION = "V17_NHL_REGULAR_SEASON_CANDIDATE_REPLAY_V1"
SOURCE_ID = "NHL_PUBLIC_WEB_API"
SOURCE_POLICY = "NHL_PUBLIC_WEB_API"
MODEL_FAMILY = "NHL_REGULAR_SEASON_LOGISTIC_V1"
FEATURE_SCHEMA = "NHL_REGULAR_SEASON_FEATURES_V1"
REQUIRED_TRAINING_ROWS = 3676
REQUIRED_CALIBRATION_ROWS = 1226
REQUIRED_TEST_ROWS = 1226
IDENTITY_CALIBRATION_METHOD = "IDENTITY_RAW_PROBABILITY_V1"
```

Behavior:
1. Load exactly one candidate; inertness checks match soccer/generic
2. Contract: sport NHL, family `NHL_REGULAR_SEASON_LOGISTIC_V1`, source_policy and feature schema match constants
3. Partition fields must equal 3676/1226/1226; corpus `_all_rows` length must equal 6128
4. Validate each training row: manifest hash, temporal as_of < start, `home_win` bool, features finite, `market_features_used is False`, `can_execute is False`
5. `train_binary_candidate` replay; compare dataset hash, artifact checksum, calibrator payload hash, validation metrics, model_artifact_version identity
6. Reject identity calibrator for overall evidence PASS (`NHL_IDENTITY_CALIBRATOR_FORBIDDEN`) while still recording math replay blockers separately if needed
7. `source_review_status` on candidate must be `PASS` for source_pass; otherwise `SOURCE_REVIEW_REQUIRED` / FAIL — engineering never mints PASS
8. Persist to `wow_d1_certification_evidence_receipts`; return `probability_publishable=False`, `can_execute=False`

Dispatch in `candidate_certification_evidence.py` immediately after the soccer branch:

```python
if str(c.get("sport") or "").upper() == "NHL" and str(c.get("model_family") or "") == "NHL_REGULAR_SEASON_LOGISTIC_V1":
    from v17.nhl_certification_evidence import verify_nhl_candidate_certification_evidence
    return verify_nhl_candidate_certification_evidence(db, candidate_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd artifacts/wow-engine && python -m pytest tests/test_v17_nhl_certification_evidence.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add artifacts/wow-engine/v17/nhl_certification_evidence.py \
  artifacts/wow-engine/v17/candidate_certification_evidence.py \
  artifacts/wow-engine/tests/test_v17_nhl_certification_evidence.py
git commit -m "feat(nhl): add NHL-specific certification evidence replay verifier"
```

---

### Task 2: Registry-backed certified scoring adapter (Fix D)

**Files:**
- Create: `artifacts/wow-engine/v17/nhl_certified_scoring_adapter.py`
- Create: `artifacts/wow-engine/tests/test_v17_nhl_certified_scoring_adapter.py`
- Modify (minimal wiring only if an existing entrypoint already selects certified mode): prefer calling adapter from a new function `score_nhl_certified_team_event` without changing candidate scorer defaults

**Interfaces:**
- Consumes: `fitted_team_event_certification.resolve_certification`, `parse_certification`, `validate_prediction_package`; existing NHL feature reconstruction / goalie HOLD helpers from `nhl_fitted_specialist`
- Produces: `score_nhl_certified_event(req, *, db) -> dict` certified envelope or typed fail-closed blockers; never mutates `wow_d1_candidate_artifacts`

- [ ] **Step 1: Write the failing tests**

```python
def test_adapter_fails_closed_without_registry_receipt():
    # resolve raises / ok=false -> TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED
    ...

def test_adapter_rejects_request_asserted_certification():
    # package/request claiming CERTIFIED without matching resolve is ignored
    ...

def test_adapter_rejects_identity_calibrator_even_if_receipt_present():
    ...

def test_adapter_emits_home_and_away_bounds_and_validate_prediction_package_fields():
    # raw_probability, calibrated_probability, lower_bound, upper_bound,
    # model_timestamp, latest_material_update_timestamp, probability_package_valid,
    # independent_verification_status=PASS, calibration_artifact_fingerprint == receipt.calibration_sha256
    ...

def test_goalie_hold_still_blocks_with_valid_receipt():
    ...

def test_adapter_does_not_mutate_candidate_probability_publishable():
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd artifacts/wow-engine && python -m pytest tests/test_v17_nhl_certified_scoring_adapter.py -q`

Expected: FAIL (module missing)

- [ ] **Step 3: Implement adapter**

Required envelope fields (home-oriented generics + explicit away mappings):

```python
{
  "sport": "NHL",
  "controlling_specialist": receipt.controlling_specialist,
  "model_version": receipt.model_version,
  "artifact_id": receipt.artifact_id,
  "calibration_method": receipt.calibration_method,
  "calibration_artifact_fingerprint": receipt.calibration_sha256,
  "independent_verification_status": "PASS",
  "raw_probability": ...,  # home raw
  "calibrated_probability": ...,
  "lower_bound": ...,
  "upper_bound": ...,
  "calibrated_home_probability": ...,
  "calibrated_away_probability": ...,
  "calibrated_home_lower_bound": ...,
  "calibrated_home_upper_bound": ...,
  "calibrated_away_lower_bound": ...,
  "calibrated_away_upper_bound": ...,
  "model_timestamp": ...,
  "latest_material_update_timestamp": ...,
  "probability_package_valid": True,  # only when complete
  "calibration_status": "PASS",
  "calibration_artifact_certified": True,
  "calibration_health_status": "PASS",
  "outcome_space": "HOME_AWAY_INCLUDING_OT_SO",
  "probability_publishable": True,  # only when receipt + envelope + goalie gates clear
  "rank_eligible": False,  # ranking remains a separate gate unless already governed
  "can_execute": False,
}
```

Fail closed when:
- registry resolve fails
- receipt sport/specialist/version/artifact/calibrator mismatch vs loaded artifacts
- calibrator method is identity or bins lack bounds
- goalie status / observed-at / identity snapshot HOLD
- home+away calibrated sum ≠ 1 within 1e-5

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd artifacts/wow-engine && python -m pytest tests/test_v17_nhl_certified_scoring_adapter.py tests/test_v17_nhl_certification_evidence.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add artifacts/wow-engine/v17/nhl_certified_scoring_adapter.py \
  artifacts/wow-engine/tests/test_v17_nhl_certified_scoring_adapter.py
git commit -m "feat(nhl): add registry-backed certified scoring adapter"
```

---

### Task 3: Exact-head regression + PR packaging

**Files:**
- Modify: PR body for implementation branch with `Incident: #1952`
- Optional doc note only if failure codes need a one-line entry in `artifacts/wow-engine/docs/failure_codes.md` for new blocker tokens

- [ ] **Step 1: Run scoped NHL suites**

```bash
cd artifacts/wow-engine && python -m pytest \
  tests/test_v17_nhl_certification_evidence.py \
  tests/test_v17_nhl_certified_scoring_adapter.py \
  tests/test_v17_nhl_fitted_specialist.py \
  tests/test_v17_soccer_multiclass_certification_evidence.py \
  -q
```

Expected: PASS (soccer dispatch regression still green)

- [ ] **Step 2: Push implementation branch and open PR**

Branch: `cursor/nhl-certification-replay-adapter-2d8f`  
Base: `main`  
Body must start with standalone line `Incident: #1952`  
State clearly: engineering Fix C/D only; Fix A/B and independent PASS still required; `WORKING_NOT_COMPLETE`

- [ ] **Step 3: Commit any failure-code doc deltas if new codes were introduced**

---

### Task 4: Operational prerequisites (not closed by engineering PR)

Owner handoffs — no governance bypass:

1. **Fix A (Data Engineering):** rebuild source-pinned 3676/1226/1226 corpus; FULL_SEASON training receipt; current-season hydration; rights fail-closed
2. **Fix B (NHL Model Specialist):** fit non-identity bound-producing calibrator with chronological metrics
3. **Independent reviewer / SIRT:** source-rights + replay verification PASS on the new candidate/receipt
4. **Independent QA:** re-evaluate PR #2798 / implementation PR with `Incident: #1952`
5. **After G5b:** service-role registry insert; post-write RPC proof; separate catalog PR; production deployed-SHA + current-slate Action receipt

---

## Self-review

1. **Spec coverage:** Fix C → Task 1; Fix D → Task 2; Fix A/B/QA sequence → Task 4; G5b/G6/G7/G8 remain post-engineering.
2. **Placeholders:** none intentional; tests and constants are concrete.
3. **Type consistency:** `verify_nhl_candidate_certification_evidence(db, candidate_id)`; `score_nhl_certified_event(req, *, db)`.
)
