---
name: Governance resilience module
description: gate_engine/governance_resilience.py — UNAVAILABLE vs MISMATCH taxonomy, snapshot cache, run pinning, structured error contracts, /wow/engine/health endpoint. Precedence 80, ENGINE v16.5.
---

## Canonical location
`gate_engine/governance_resilience.py`

## Core rule: UNAVAILABLE ≠ MISMATCH
These two error codes are never interchangeable:
- `GOVERNANCE_UNAVAILABLE` — no comparison was made (endpoint unreachable, no cache)
- `GOVERNANCE_MISMATCH` — comparison was made and failed (caller has stale hash)

Conflating them was the architectural defect that caused all "RUN_INVALID" generic failures.

## Error code → label ceiling table
| Error code | Max terminal label |
|---|---|
| GOVERNANCE_UNAVAILABLE | RESEARCH_INTEREST |
| GOVERNANCE_CACHED_DEGRADED_RUN | MODEL_QUALIFIED_HOLD |
| GOVERNANCE_MISMATCH | None (run invalid) |
| GOVERNANCE_CONTRACT_INVALID | None (run invalid) |
| GOVERNANCE_FULL_ATTESTATION | FINAL_APPROVED (normal) |
| SCAN_UNAVAILABLE_DEGRADED_RUN | MODEL_QUALIFIED_HOLD |

## GovernanceSnapshot singleton
- Module-level `_governance_snapshot = GovernanceSnapshot()` — one per gunicorn worker
- Refreshed eagerly at app.py module load (never raises — wrapped in try/except)
- Refreshed again on every successful `GET /wow/governance/status` call
- `is_fresh(max_age_seconds=300)` — default 5-min TTL
- `as_cached_response()` — annotates snapshot with `source: "cached_snapshot"`, `live_verified: False`

## RunGovernancePin singleton
- `_run_pin = RunGovernancePin()` — one per gunicorn worker, max 1000 pins (LRU eviction)
- `pin(run_id, governance_state)` called at handshake success in `/gate-engine/run`
- Stores: `run_id, master_spec_version, governance_hash, engine_code_version, active_patch_ids, pinned_at_iso`
- Mid-run outage cannot invalidate already-verified governance

## gate-engine/run new behavior (missing hash path)
```
if no expected_governance_hash:
    if snapshot.is_fresh():
        → GOVERNANCE_CACHED_DEGRADED_RUN, HTTP 200, ceiling=MODEL_QUALIFIED_HOLD
        (run proceeds in degraded mode)
    else:
        → GOVERNANCE_UNAVAILABLE, HTTP 409
        (run blocked, no partial output)
```

## make_error_contract() fields
All failures now return:
```json
{
  "ok": false,
  "error_code": "...",
  "stage": "governance_handshake",
  "http_status": 409,
  "retryable": true/false,
  "retry_after_seconds": 0/2/5,
  "governance_verified": false,
  "governance_hash": "...",
  "cached_snapshot_available": true/false,
  "cached_snapshot_age_seconds": N,
  "can_execute": false,
  "label_ceiling": "MODEL_QUALIFIED_HOLD" or null,
  "recovery_path": "..."
}
```

## Retry guidance
- Retryable: 429, 502, 503, 504, None (unknown)
- Non-retryable: 400, 401, 403, 404, 405, 409, 422
- Delays: attempt 1=0s, attempt 2=2s, attempt 3=5s

## GET /wow/engine/health
- No auth required, no external HTTP calls, sub-millisecond response
- Reports: ok, service, uptime_seconds, worker_pid, governance.{loaded, master_spec, engine_version, hash_prefix, active_patches}, snapshot.{available, age_seconds, is_fresh, max_age}, db_env_configured, can_execute=false
- Safe to call as pre-flight before every run

## Current patch identity
- Patch ID: `WOW-PATCH-2026-07-15-GOVERNANCE-RESILIENCE-AND-ERROR-CONTRACT`
- Precedence: 80
- ENGINE_CODE_VERSION: v16.5
- Governance hash: `2a74d11e20ee24526de6db9401f39b4a9228c0f46948d07cea72dc4a21111908`

**Why:** A single ClientResponseError from the governance endpoint was collapsing the entire analysis to RUN_INVALID — no distinction between "couldn't reach endpoint" and "hash mismatch". This made the governance control plane a single point of failure.

**How to apply:** Any new governance failure mode must be assigned one of the six error codes. Never add a new generic "RUN_INVALID" catch-all. The make_error_contract() builder is the single exit point for all structured failures from governance/scan gates.
