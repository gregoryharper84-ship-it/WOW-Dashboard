---
name: Governance finalization checklist
description: 10-item proof pattern used to finalize WOW-PATCH-2026-07-15; reuse for any future governance patch activation.
---

## The 10-item checklist

### 1. Mandatory handshake — fail closed
`/gate-engine/run` must return HTTP 409 when `expected_governance_hash` is absent, null, or empty-string. No partial scoring output on mismatch. Remove any "backward compat" bypass.

### 2. effective_at / expires_at in status
`GET /wow/governance/status` must return `effective_at` and `expires_at` (from the highest-precedence active patch). These are derivable from the patch registry at module load; never runtime-computed.

### 3. Clock injection in tests
All tests must pass explicit date parameters (`as_of=`, `target_date=`) — never rely on the real system clock. A test using today's date string without a fixed `target_date` parameter will break on the next calendar day.

**Why:** The spec review caught a test using `"2026-07-15"` as slate_date without an explicit `target_date=date(2026,7,15)` — it would fail the day after by triggering slate_validation's future-date purge.

### 4. Black-box API tests (Flask test client)
Create a minimal Flask test harness in `gate_engine/tests/test_governance_api.py` that re-implements the two governance routes using the real gate_engine modules (no app.py import). Test:
- Status endpoint shape (all 8 required fields)
- Correct hash → 200 with prop_ledger
- Missing hash → 409, no scoring output
- Wrong hash → 409, server_hash in response
- Wrong patch list → 409
- Scoring response echoes governance_hash byte-for-byte with status endpoint

### 5. Hash determinism
Tests must prove: same hash across 10 sequential calls, 64 hex chars, `_LOADED_AT` NOT in hash input, status endpoint hash == `compute_governance_hash()`.

### 6. Lowest-ceiling propagation
Three tests: (a) gate stamp order, (b) no downstream gate restores rejected label, (c) full pipeline run confirms REJECT label survives to output.

### 7. Session exposure persistence — `existing_ledger` parameter
`run_pipeline()` accepts `existing_ledger: ExposureLedger | None = None`. App.py maintains `_SESSION_LEDGERS: dict` (LRU-evict at 200 entries), looks up by `session_id`. Two tests prove: shared ledger catches cross-call duplicate; independent calls (no ledger) don't share state.

### 8. market_adverse: sportsbook_line/consensus_line only
`best_available` must NOT be passed to `market_adverse.run()`. It reflects best available pricing, not adversity reference. Passing it causes spurious PUSH_LOSS on valid lines that happen to have a lower-price book.

### 9. Governance echo in every scoring response
Every successful `/gate-engine/run` response must include: `governance_hash`, `patch_ids_applied`, `can_execute=False`, `governance_handshake="GOVERNANCE_MATCH"`.

### 10. Deployment manifest
Generate from live module (not from code comments):
```python
from gate_engine.governance import compute_governance_hash, get_governance_status, ...
manifest = {
  "git_commit": ...,
  "governance_hash": get_governance_status()["governance_hash"],
  "can_execute": False,
  "patch_status": "PATCH_IMPLEMENTED_NOT_ACTIVATED",
  ...
}
```

## Current manifest (WOW-PATCH-2026-07-15)

```json
{
  "git_commit": "b9d7dc8ee9e3407a8a37257a1452c3f1ad12c9e1",
  "engine_code_version": "v16.2",
  "master_spec_version": "WOW-v16",
  "governance_hash": "045f3f97602ccb997b0e876c24ef2f1671d685402a5f5b43cdba72c49eefe51f",
  "active_patch_ids": [
    "WOW-CORE-v16",
    "WOW-PATCH-2026-06-27-SHARP-ANCHOR",
    "WOW-PATCH-2026-07-07-JS-STYLE",
    "WOW-PATCH-2026-07-10-COMBO-SETTLEMENT",
    "WOW-PATCH-MANDATORY-RECONSTRUCTION-v1.0",
    "WOW-PATCH-2026-07-15-PROP-CALIBRATION-EXPOSURE-AND-SLIP-GOVERNANCE"
  ],
  "effective_at": "2026-07-15",
  "expires_at": null,
  "can_execute": false,
  "patch_status": "PATCH_IMPLEMENTED_NOT_ACTIVATED"
}
```

**Why:** `can_execute=False` is a governance invariant — it means the engine classifies but human approval (WOW/LLP operator) is always required before any stake is placed. The hash is the authoritative sync point between GPT side and Replit side.

## How to activate

1. Both sides (GPT config + Replit engine) must carry the same `governance_hash`.
2. Restart both sides.
3. Perform one dry-run scoring request — verify returned `governance_hash` matches byte-for-byte.
4. Only then change `patch_status` from `PATCH_IMPLEMENTED_NOT_ACTIVATED` to `ACTIVE`.
