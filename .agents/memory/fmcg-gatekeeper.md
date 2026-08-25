---
name: Full Model Contract Gatekeeper (FMCG)
description: WOW-FMCG-v1.0 — fail-closed candidate-level enforcement contract for FINAL_APPROVED; Patch #25, Precedence 104.
---

## Contract identity
- Module: `gate_engine/full_model_gatekeeper.py`
- Patch: `WOW-PATCH-FMCG-v1.0`, precedence 104, `can_execute=False`
- Tests: `gate_engine/tests/test_full_model_gatekeeper.py` — 60 tests, all pass

## What it does
- Validates already-computed specialist outputs (never recomputes probability, no-vig, push prob, etc.)
- Returns `full_model_status` (COMPLETE | INCOMPLETE | INVALIDATED) + `qualification_result` (PASS | HOLD | REJECT)
- Downgrade-only: if `terminal_label == FINAL_APPROVED` and qualification_result != PASS → `MODEL_QUALIFIED_HOLD`
- Attaches full structured result to `row["gatekeeper"]`

## Gate checks (in order)
1. upstream_ceiling — entry label recorded; gatekeeper cannot upgrade
2. invalidation — INVALIDATION_SIGNALS on row → STATUS_INVALIDATED
3. full_model_completeness — 7 required gate dicts + calibrated_probability must exist
4. market_identity — player, sport, prop_type, line, market_status all non-null
5. role_status — DEPENDENCY_CONFLICT → FAIL; STALE/RECHECK → HOLD
6. l10_evidence — l5l10 must pass AND market + ev also pass (no sole-qualifier); line mismatch → FAIL
7. calibrated_probability — present, in [0,1], ACTIVE model; PROVISIONAL → HOLD; NO_REGISTERED_MODEL → REJECT
8. calibrated_lower_bound — if present: lb < cal_prob (masquerade check); FS model requires lb
9. no_vig_exact_line — exact_market_no_vig_prob must be present; adjacent-only → HOLD
10. push_rules — whole-number lines require resolved push_prob; half-point → SKIP
11. contradiction_audit — SOURCE_CONFLICT or MARKET_CONTRADICTION → FAIL
12. freshness — staleness blockers detected in row → HOLD
13. source_grade — UNOBTAINABLE or RECONSTRUCTED → FAIL
14. specialist_failure_path — failure_path or hp_gate.error → FAIL

## Wiring (three routes)
- **gate-engine** (`pipeline.py`): `_fmcg.apply_gatekeeper(row)` inserted AFTER `classifier.classify(row)`, BEFORE `route_registry.enforce_route_completion(row)`
- **CC** (`command_center/orchestrator.py`): `_fmcg.verify_cc_envelope(env)` loop inserted at Step 5.5, BEFORE `enforce_batch_ceilings`; checks engine_result["gatekeeper"] for PASS
- **v16** (`app.py` /wow/v16/run): `_fmcg_v16.verify_v16_result(result)` called after `orch.run(context)`, checks skill_results for any skill with gatekeeper PASS

## Invalidation signals (set on row to trigger STATUS_INVALIDATED)
material_status_change, lineup_finalized_after_score, starter_changed, goalie_changed, qb_changed, event_started, settlement_status_changed, price_age_exceeded, weather_material_change

## API surface
- `evaluate(row, governance_hash)` → pure dict, read-only
- `apply_gatekeeper(row, governance_hash)` → in-place, attaches row["gatekeeper"]
- `apply_gatekeeper_batch(rows, governance_hash)` → in-place, returns summary dict
- `verify_cc_envelope(envelope)` → CC path, modifies envelope on downgrade
- `verify_v16_result(result)` → v16 path, modifies result on downgrade

**Why:** FINAL_APPROVED had no uniform pre-output enforcement; each route had only classifier + settlement ceiling, leaving gaps for PROVISIONAL model rows, adjacent-line no-vig, point-prob masquerading as lower bound, and undetected invalidation signals to survive.
