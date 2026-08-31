# WOW v17 Phase A — Alignment Status

As of: 2026-08-31

This is a candidate/shadow status record. WOW v17 is not active and production remains governed by WOW v16 Clean Core.

## Resolved in this Phase-A branch

```text
RENDER_REPOSITORY_SHA_PARITY = PASS
  main/live = 4ef39405702ce38682b28733f767206ebf28a2d5

CUSTOM_ENGINE_LANE_OWNERSHIP = IMPLEMENTED_CANDIDATE
  PLAYER_PROP -> WOW_BETTING_ENGINE
  TEAM_EVENT / MONEYLINE / OUTRIGHT_WINNER / UPSET -> LLP_TEAM_BETTING_ENGINE

REQUESTER_HOST_VS_CONTROLLING_ENGINE_SEPARATION = IMPLEMENTED_CANDIDATE
  WOW or PROJECT_CHAT may originate a team/event request without becoming the
  controlling team model; the backend resolves LLP_TEAM_BETTING_ENGINE.

HOST_LOCAL_TERMINAL_AUTHORITY = FALSE
GLOBAL_TERMINAL_AUTHORITY = V17_TERMINAL_REDUCER

GENERIC_TEAM_EVENT_INGRESS = IMPLEMENTED_CANDIDATE
  POST /score-team-event
  MLB -> existing governed MLB event adapter
  unsupported sports -> MODEL_UNAVAILABLE, fail closed

V17_CANDIDATE_APP_ISOLATION = IMPLEMENTED
  api_v17_candidate:app is distinct from api_ncaaf_acceptance:app
  importing candidate code does not add v17 routes to the accepted v16 app

RECOMMENDATION_LEDGER_ROUTE_PARITY = IMPLEMENTED_CANDIDATE
  /record-recommendations
  /settle-recommendations
  mounted from existing audited ledger implementation

CANONICAL_WOW_ACTION_SCHEMA = PREPARED_CANDIDATE
CANONICAL_LLP_ACTION_SCHEMA = PREPARED_CANDIDATE
  same eventual governed Render origin
  same bearer auth family
  LLP schema contains no prop-scoring route
  WOW schema delegates team/event requests to LLP controlling engine

LEGACY_REPLIT_PRIMARY_ROUTING_ALLOWED = FALSE
CAN_EXECUTE = FALSE
```

## Still blocking v17 cutover

```text
B01 WOW live Custom GPT editor configuration has not been directly re-attested.
B02 LLP live Custom GPT editor requires post-contract re-attestation.
B03 api_v17_candidate has not yet completed CI + shadow service acceptance.
B04 LLP direct-vendor Actions must be reclassified/proven EVIDENCE_ONLY or removed;
    prior auth gaps must not be assumed fixed.
B05 Independent review of the implementation branch is still required before merge.
```

## Explicitly not blocked by unsupported sports

The generic team/event ingress being cross-sport does not mean every sport has a fitted team/event model. Each unsupported sport correctly terminates its lane as `MODEL_UNAVAILABLE` until a certified sport adapter/model is registered. This is expected fail-closed behavior, not permission for market-implied or qualitative fallback.

## Next certification sequence

```text
1. Candidate CI
2. Candidate Render shadow deployment (separate from production)
3. Route/health/host-contract acceptance on candidate service
4. Independent code review
5. WOW Custom GPT editor attestation
6. LLP Custom GPT editor attestation + vendor Action cleanup proof
7. Update attestation records to PASS only from direct evidence
8. Merge Phase-A contract/implementation if review passes
9. Keep v17 cutover false until final shared acceptance and migration approval
```
