# WOW generation status

Status effective: 2026-09-02

This document is the repository-level generation authority for lifecycle
classification. It promotes the existing WOW project in place; it does not
create a second product, repository, runtime, database, or Custom GPT pair.

```text
PROJECT = WOW Betting Engine
CANONICAL_REPOSITORY = WOW-Dashboard
CURRENT_GENERATION = V17
CURRENT_GENERATION_STATUS = ACTIVE
LEGACY_GENERATION = V16
LEGACY_GENERATION_STATUS = LEGACY_SUPERSEDED
PRODUCTION_RUNTIME = EXISTING_RENDER_SUPABASE_GOVERNED_STACK
PRIMARY_HOST = WOW_BETTING_ENGINE
TEAM_EVENT_CAPABILITY = LLP_TEAM_BETTING_ENGINE
GLOBAL_TERMINAL_AUTHORITY = V17_TERMINAL_REDUCER
can_execute = false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = true
```

## Lifecycle rules

1. V17 is the only active project generation.
2. V16 documents, configs, schemas, tests, and routes are historical or
   compatibility artifacts unless an active V17 contract explicitly imports
   or preserves them.
3. A V16 filename or internal version string is not current-generation
   authority merely because it remains in the repository.
4. Historical V16 artifacts must be preserved when needed for audit, rollback,
   provenance, immutable hashes, or regression coverage. They must not be bulk
   rewritten solely to replace version text.
5. Current-status documents and user-facing project labels must identify V17
   as active and V16 as legacy/superseded.
6. Existing Render/Supabase resources and existing WOW/LLP Custom GPT identities
   remain canonical. No parallel V17 project is authorized by this promotion.
7. V17 activation does not manufacture fitted-model support. Unsupported exact
   sport/stat/market routes continue to fail closed under their controlling
   contracts.
8. `can_execute=false` and dry-run-only safety are permanent across both active
   and legacy compatibility paths.

## Controlling current-state pointers

- Backend and release status: `artifacts/wow-engine/V17_PRODUCTION_STATUS.md`
- V17 host/runtime alignment: `artifacts/wow-engine/v17/PHASE_A_ALIGNMENT_STATUS.md`
- V17 WOW editor source: `artifacts/wow-engine/WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt`
- V17 LLP editor source: `artifacts/wow-engine/LLP_V17_CUSTOM_GPT_INSTRUCTIONS.txt`
- V17 Action schemas: `artifacts/wow-engine/v17/openapi.wow-betting-engine.v17.yaml`
  and `artifacts/wow-engine/v17/openapi.llp-team-engine.v17.yaml`

Live Custom GPT editor synchronization remains a separate product-configuration
attestation and must not be inferred from repository or backend state.
