# WOW V17 — Typed Failure Code Registry (incremental)

The table records newly introduced **Trigger.dev read-only runtime-monitor** codes.
It does not retrospectively certify or replace the much larger existing backend
and fitted-model failure taxonomy. Existing backend errors remain authoritative
for their own lane and are never relabeled by these monitoring codes.

| Code | Source / scope | Meaning | Owner |
|---|---|---|---|
| `BACKEND_LIVENESS_TRANSPORT_FAILURE` | Trigger sentinel / BACKEND_RUNTIME | GET /health could not connect or complete in 7 seconds | AMRT |
| `BACKEND_LIVENESS_INVALID_JSON` | Trigger sentinel / BACKEND_RUNTIME | GET /health did not produce JSON | AMRT |
| `BACKEND_LIVENESS_HTTP_{status}` | Trigger sentinel / BACKEND_RUNTIME | GET /health returned a status other than HTTP 200; actual status retained in receipt | AMRT |
| `BACKEND_LIVENESS_CONTRACT_INVALID` | Trigger sentinel / BACKEND_RUNTIME | HTTP 200 health body missing `status=ok` or claims `can_execute=true` | AMRT |
| `BACKEND_READINESS_TRANSPORT_FAILURE` | Trigger sentinel / BACKEND_RUNTIME | GET /health/ready could not connect or complete in 7 seconds | AMRT |
| `BACKEND_READINESS_INVALID_JSON` | Trigger sentinel / BACKEND_RUNTIME | GET /health/ready did not produce JSON | AMRT |
| `BACKEND_READINESS_HTTP_{status}` | Trigger sentinel / BACKEND_RUNTIME | Unexpected HTTP response (including untyped 503), actual status retained | AMRT |
| `BACKEND_READINESS_CONTRACT_INVALID` | Trigger sentinel / BACKEND_RUNTIME | Missing/contradictory status or component values, or `can_execute=true` | AMRT |
| `BACKEND_NOT_READY` | Trigger sentinel / BACKEND_RUNTIME | Typed HTTP 503 `status=not_ready`; preserves database, queue, and worker-registry fields | AMRT |
| `BACKEND_READY_MODEL_UNVERIFIED` | Trigger sentinel / BACKEND_RUNTIME | Informational: health and readiness are OK, but no fitted model or Action receipt was tested | Betting Intelligence / AMRT |

The sentinel also returns `incident_key` for stable grouping; the key is **not**
a permanent issue or deduplicated database record. `durable_incident_written=false`.

**Critical separation:** `BACKEND_NOT_READY` does not mean
`MODEL_UNAVAILABLE`, `MODEL_SCORER_FAILED`, or an Action failure. The
model/Action lanes must be tested and classified by their authoritative owner.
This registry is a bounded first entry; global V17 backfill remains separate.
