---
name: Phase 1 pipeline degradation test pattern
description: How to write tests for DEGRADED_ENGINE_RUN and other pipeline-level behaviors that require rows to reach critical modules.
---

## Rule
Minimal test rows (player/prop/line/direction/sport/game_date/platform only) get
`SLATE_PURGE:NO_SLATE_DATE` from `slate_validation.run()` and `continue` before
reaching `l5_l10_ledger`, `market_gate`, or `ev_gate`.

To write a pipeline degradation test that reaches those modules:
1. Patch `gate_engine.slate_validation.run` to a no-op `_noop_slate`
2. Add `skip_data_contract=True, skip_health_gate=True, skip_settlement_check=True`
3. Use a context manager to restore the original after the test

```python
@contextlib.contextmanager
def _patch(module_path, fn_name, replacement):
    import importlib
    mod = importlib.import_module(module_path)
    original = getattr(mod, fn_name)
    setattr(mod, fn_name, replacement)
    try:
        yield mod
    finally:
        setattr(mod, fn_name, original)

with _patch("gate_engine.slate_validation", "run", lambda *a, **k: None):
    result = run_pipeline(rows, skip_data_contract=True, ...)
```

**Why:** `slate_validation` checks `game_date` against a live slate lookup; minimal
test rows have no confirmed slate entry, so they terminate immediately.

## Ceiling placement
DEGRADED_ENGINE_RUN ceiling enforcement (FINAL_APPROVED → MODEL_QUALIFIED_HOLD) lives
in `_build_output`, NOT in `run_pipeline`. This makes it testable by calling
`_build_output(rows, ledger, run_status="DEGRADED_ENGINE_RUN", ...)` directly.

`run_pipeline` still computes `run_status` and passes it to `_build_output`.
