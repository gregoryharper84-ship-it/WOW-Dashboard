# WOW V17 — NBA verification receipt pre-deploy candidate (2026-11-10)

**CLASS P (deployment config / schema); DRAFT ONLY. Do not merge or apply the migration without independent exact-head acceptance and confirmed production DB identity.**

## Mechanism / falsifiable prediction

The live Render web service auto-deploys on `checksPass` independently of the GitHub deployment workflow. A gate confined to that workflow does not cover the active deployment path. Its paid instance is `1c-2g` while the versioned Blueprint still declares `free`. Therefore a schema-incompatible candidate could reach startup without the new receipt contract being checked. A Render `preDeployCommand` that makes a read-only authenticated RPC call must fail before startup when the database lacks the immutable receipt schema.

- The proposed migration creates an NBA **SHADOW evidence-only** receipt ledger; it does not touch `wow_team_event_specialist_certifications`, model parameters, thresholds, publication or execution.
- The schema RPC verifies the versioned contract, 15 expected columns, RLS, expected constrained unique identity, enabled immutable UPDATE/DELETE trigger, and service-role SELECT/INSERT-only grants.
- The Python pre-deploy fails closed on missing secrets, unavailable/malformed RPC, wrong contract version, or any suggestion of publishability or execution.
- `render.yaml` matches the already-observed paid live plan and adds the pre-deploy command. **Source YAML does not prove the live Render service picked up that configuration.**

## Tests actually run

1. `PYTHONPATH=artifacts/wow-engine pytest -q artifacts/wow-engine/tests/test_nba_verification_schema_predeploy.py` in a local isolated source copy: **5 passed**. The positive/negative Python RPC cases use fakes, not an actual PostgREST round trip.
2. Read-only `wow-engine-validation` PostgreSQL inventory: `wow_nba_team_event_verification_receipts` absent, original D1 receipts and immutable trigger function present. This project's production authority is **not** established.
3. A **single explicit transaction** executed the candidate DDL in `wow-engine-validation`, then verified the schema RPC reported compatible, appended and read back one exact-bundle test receipt, and rejected UPDATE, DELETE, and a duplicate bundle. Result: `MIGRATED_SCHEMA_ACCEPTED_INSERT_IMMUTABLE_DUPLICATE_REJECT`.
4. That transaction ended in **ROLLBACK**. A fresh read-only check showed **the new table and RPC both absent again**, and the old D1 table still present. No lasting schema change.
5. Render `get_service` readback: web `branch=main`, `autoDeployTrigger=checksPass`, and paid compute plan `1c-2g`. The actual pre-deploy command was **not independently observed as configured**.

## Remains unverified / not authorized

- Missing November 10 **uncommitted** `nba_team_event_verification.py` verifier/wiring. This branch is a separately constructed candidate, not recovered original code. Do not assert each fit archives receipts yet.
- Training artifact/calibrator digest and fit-end values for an actual new SHADOW run; historical records cannot be manufactured.
- Exact migration contract match to verifier V1–V12, authenticated service-role PostgREST round trip, production database identity, independent SIRT semantic approval, protected native/hosted CI and Release Authority at exact head, Blueprint promotion, live release parity, and end-to-end immutable receipt.
- Because the migration is absent from current production, **merging the pre-deploy setting prematurely would intentionally halt new deployments**. Keep this a draft until migration authorization and source reconciliation are complete.

Safety invariants: `custom_gpt_identity=WOW_BETTING_ENGINE`; `runtime_generation=V17_ACTIVE`; `terminal_authority=V17_TERMINAL_REDUCER`; `can_execute=false`; `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`.
