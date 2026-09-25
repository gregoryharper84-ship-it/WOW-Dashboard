# PR #661 current-main revalidation — 2026-09-25

## Defect contract

**Expected behavior**

A team/event sporting probability can become rank/publish eligible only when an exact fitted specialist artifact, calibration artifact, independent verification, input contract, and immutable certification receipt all match. Runtime bridge registration by itself must never create certification. `V17_TERMINAL_REDUCER` remains the sole global terminal authority and `can_execute=false` remains invariant.

**Observed behavior**

Current `main` still permits selected multisport runtime registrations to self-resolve as `CERTIFIED` when the registered controlling-specialist identity matches a declared identity. The connected WOW Supabase project does not yet contain the immutable `wow_team_event_specialist_certifications` registry or `wow_v17_active_team_event_certification` resolver RPC. NCAAF discovery succeeds, but its governed team/event path remains unavailable; the legacy fitted-artifact table has no certified artifact and the newer `NCAAF_DYNAMIC_TEAM_STATE_LOGIT_V2` evidence remains an inert candidate.

**Reproduction evidence**

1. `v17/team_event_model_registry_audit.py::certification_state` promotes an exact registered bridge using `ACTIVATABLE_TEAM_EVENT_CERTIFICATIONS`.
2. `v17/multisport_team_event_governance.py::reduce_multisport_team_event` trusts that registration-derived state as terminal certification.
3. Production startup evidence on the current deployed service reports NCAAF model/artifact unavailability rather than a publishable governed probability.
4. Connected Supabase schema inspection shows no team/event specialist certification table/RPC.

**Affected components**

- V17 multisport team/event certification and terminal governance
- Supabase certification-receipt persistence/resolution
- NCAAF promotion prerequisite chain

**Severity:** High governance/reliability defect.

**Change class:** Class B infrastructure/governance foundation. This change must not alter sporting model mathematics, fitted coefficients, calibration mathematics, qualification thresholds, or `can_execute=false`.

## Done criteria

- Registration alone cannot self-certify a team/event specialist.
- Immutable certification registry and service-role resolver exist in repository migration source.
- Resolver and package validation preserve typed certification/freshness failures rather than rewriting them to `MODEL_UNAVAILABLE`.
- `V17_TERMINAL_REDUCER` remains sole global terminal authority.
- Market probability and generic reasoning remain prohibited substitutes.
- `can_execute=false` remains enforced by code and database constraints.
- Required current pull-request checks pass against current `main`.
- After merge, the migration is applied and validated in the connected WOW Supabase project before any certification receipt is written.
- Existing NCAAF candidate remains nonpublishable until its separate Class C source-review, independent replay, dynamic-bound, current-event hydration/scoring, and governed promotion evidence pass.

This revalidation artifact intentionally changes no sporting probability behavior and exists to force the current required CI suite to evaluate PR #661 against the current repository state before merge.
