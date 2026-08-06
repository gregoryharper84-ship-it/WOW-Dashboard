---
name: WNBA acquisition audit-semantics invariant
description: routes_attempted and fallback_routes_attempted may contain ONLY providers where request_made=True; non-HTTP sources route to separate audit buckets.
---

## The invariant

`routes_attempted` (normalized list[dict]) and `fallback_routes_attempted` (list[str])
must contain ONLY providers where an actual outbound HTTP request was made
(`request_count > 0`, `request_made=True`).

Non-HTTP sources go to four separate audit fields:
- `routes_skipped_by_policy` — blocked by ToS/robots.txt (e.g. basketball_reference)
- `routes_not_implemented` — configured in priority table but no handler (e.g. statmuse_reconstruction_query, dedicated_conflict_scan)
- `routes_unavailable` — HTTP called but source was unreachable
- `routes_auth_required` — AUTH_REQUIRED returned (no key, no HTTP request issued)

`adapters_called` must also contain only providers where `request_count > 0`.

## Where it lives

- `fallback_router.py` — each `_attempt_*` handler populates `route_records`, `routes_skipped_by_policy`, `routes_not_implemented` on `RouteAttemptResult`; `routes_attempted` on the result stays HTTP-only.
- `evidence_acquisition.py` — `_build_acquisition_audit()` aggregates and enforces the invariant before writing to the output dict.

**Why:** Old code appended in-pipeline source names (enrichment_box_score_log, basketball_reference) to `routes_attempted`, making it impossible to tell from the audit whether a real request was ever made. The correction enables accurate telemetry and reliable GPT postmortem.

**How to apply:** Any new `_attempt_*` handler must:
1. Call `_not_impl_records_for(category)` and/or `_policy_skipped_records_for(category)` at the top.
2. Call `_adapter_route_record(adapter)` for each real HTTP adapter result.
3. Set `routes_attempted = [adapter.provider] if adapter.request_count > 0 else []`.
4. Populate `route_records`, `routes_skipped_by_policy`, `routes_not_implemented` on the returned `RouteAttemptResult`.
5. Never put in-pipeline source names in `routes_attempted`.

Regression tests 20-25 in `test_wnba_evidence_acquisition.py` enforce all invariants.
