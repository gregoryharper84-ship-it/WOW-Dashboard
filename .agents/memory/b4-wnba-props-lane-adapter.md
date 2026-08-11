---
name: B4 WNBA/NBA Props Lane Adapter
description: Architecture, governance constraints, and lessons for the B4 Universal Agent Core lane adapter and its satellite layers.
---

# B4 WNBA/NBA Props Lane Adapter

## Module tree

```
gate_engine/universal_agent/
  lanes/wnba_props/
    __init__.py           # public API: WnbaPropsAdapter, WnbaPropsAdapterResult, AdapterStatus
    validation.py         # AdapterInputError; sport guard (WNBA/NBA), h2h keyword rejection
    field_map.py          # deterministic extractors, build_source_coverage, derivation helpers
    role_inputs.py        # 6 B1 role builders with _checked() guard, RoleInputBuildError
    adapter.py            # WnbaPropsAdapter.adapt(), WnbaPropsAdapterResult (frozen dataclass)
    game_script/
      __init__.py, game_environment.py, player_state.py, minutes_distribution.py
      conditional_hit_prob.py, unconditional_aggregator.py, script_fragility.py
      shadow_gate.py      # GameScriptShadowGate; ceiling MODEL_QUALIFIED_HOLD; PATCH ID below
  model_validation/
    __init__.py, feature_store.py, model_manifest.py, champion_challenger.py
    calibration_scoreboard.py, drift_monitor.py, health_state.py
    learning_schedule.py, promotion_gate.py, walk_forward.py, validation_wrapper.py

gate_engine/tests/
  test_wnba_props_adapter.py       # 95+ tests
  test_game_script_distribution.py # 55+ tests
  test_model_validation.py         # 80+ tests
```

## Key design decisions

- **Player_id = None** in EvidencePacket: WNBA rows have no numeric player IDs.
- **Row wins on key collision**: `combined = {**enrichment, **row}`.
- **Game-script shadow is best-effort**: `_run_game_script_shadow()` catches all exceptions → None, never blocks adapter.
- **Poisson CDF in pure Python**: no scipy/numpy; `P(X≥ceil(line)) = 1 - CDF(floor(line), λ)`.
- **Drift monitor uses shared histogram range**: `_histogram_shared(values, lo, hi)` across both ref+cur combined; per-distribution normalization causes non-overlapping distributions to look identical.
- **object.__setattr__ bypasses frozen dataclass**: tests must use direct attribute assignment (`r.x = y`) not `object.__setattr__` to probe frozen enforcement.

## Governance invariants (unconditional everywhere)

- `can_execute = False`
- `PRODUCTION_AUTHORITY = False`
- `USER_OUTPUT_AUTHORITY = False`
- `CAPITAL_AUTHORITY = False`
- `NO_AUTO_PROMOTION = True` (champion_challenger + promotion_gate)
- `CEILING = "MODEL_QUALIFIED_HOLD"` (game-script layer + validation_wrapper)
- No wiring into app.py — offline/shadow scope only.

## Patch IDs

- Adapter: WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4
- Game-script shadow: WOW-PATCH-2026-08-11-GAMESCRIPT-SHADOW
- Model validation: WOW-PATCH-2026-08-11-MODELVAL

## Outstanding blockers

- FOLLOWUP_193: settlement worker backoff/heartbeat — required before authoritative decision integration.
- FOLLOWUP_195: regression fixtures — required before B4 closure.
- Explicit governance resolution required before any live external-model canary.
