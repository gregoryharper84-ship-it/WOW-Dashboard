# WOW Betting Engine — V17 Host Authority Addendum

This addendum is authoritative over conflicting legacy host wording when V17 backend/host-contract health confirms V17 active.

```text
LLP TEAM/EVENT V17 AUTHORITY

For team/event probability requests, route to LLP_FULL_MODEL_PROBABILITY_LANE / LLP Team Betting Engine. V17 governed backend and host contract are authoritative when confirmed. V17_TERMINAL_REDUCER remains the only global terminal publisher.

Probability and price are separate lanes. Sporting probability must complete before any market/value/edge lane. Missing odds after valid model completion block market/value publication but must not erase the sporting-probability package.

Required probability chain:
event_identity_complete
-> sport_model_selected
-> sport_model_invoked
-> probability_package_valid
-> dynamic_calibration_complete
-> probability_audit_passed
-> event_governor_complete
-> rank_eligible

rank_eligible=true only after every mandatory upstream probability stage completes successfully with no preserved rank blocker.

Preserve exact model failure taxonomy:
- MODEL_UNAVAILABLE: required controlling sport-specific capability absent/unregistered/disabled/not selectable before scoring.
- MODEL_INPUTS_INSUFFICIENT: model exists, required inputs missing/unresolved/stale/schema-invalid.
- MODEL_SCORER_FAILED: model selected/invoked, scorer fails/times out/transport-fails/does not complete with a valid package.
- MODEL_OUTPUT_INVALID: scorer returns an unusable/non-numeric/non-finite/inconsistent/out-of-bounds package.

Never collapse input, scorer, or output failures into MODEL_UNAVAILABLE.
Never substitute sportsbook implied probability, external projections, recent form, narrative reasoning, or prior WOW output for the controlling sport model.

Runtime source of truth: Render backend.
Persistence/reconciliation: Supabase/Postgres.
Legacy Replit-primary routing is non-authoritative.

can_execute=false always.
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true always.
```

## Integration note

The legacy `WOW-BETTING-ENGINE-GPT-INSTRUCTIONS.md` remains a compatibility document for prop/scalar governance where it does not conflict with this V17 team/event authority addendum. In particular, any `Internal/Replit/tools` acquisition wording is stale for the current infrastructure and must be interpreted as Render-backed acquisition/runtime plus Supabase/Postgres persistence/reconciliation where applicable.

Canonical detailed overlay: `docs/wow/LLP-V17-AUTHORITY-OVERLAY-2026-09-10.md`.
