---
name: WNBA Generative Probability Model
description: Architecture and invariants for the WNBA role-regime Poisson mixture model and its pipeline gate
---

## Rule
`gate_engine/wnba/generative_model.py` is the canonical WNBA probability engine.
`gate_engine/wnba_generative_gate.py` wires it into the pipeline — it runs *before* `wnba_composite_gate.run(row)`, after `_mlb_pa_gate`.

**Why:** WNBA props require a generative model (role-regime mixture + Poisson PMF) rather than a static confidence tier because minutes/usage volatility is the dominant probability driver; the composite gate's ceiling still applies on top.

## Key invariants — must never be broken

1. **`can_execute = False`** — unconditional at module level and in every return path of `score()` and `gate.run()`.
2. **Simplex contract** — raw and cal More/Exact/Less stored at full float precision (no 6dp rounding). Last element enforced as `1 - (first + second)` to prevent drift.
3. **Market weight hard cap** — `market_prior_weight ≤ 0.25`; `independent_model_weight ≥ 0.75` when market data present; `independent_model_weight = 1.0` when absent.
4. **`cal_lower_bound ≤ cal_selected`** always — LB comes from the stress scenario (adverse regime mix), not a fixed haircut.
5. **YES_MODEL_QUALIFIED floor = 0.65** (`_YES_QUALIFIED_FLOOR` constant) — 53% or 64% LB → HOLD, not Qualified.
6. **L5/L10 is diagnostic-only** — never drives probability; divergence > 0.15 flagged in output but does not alter the triple.

## Pipeline wiring pattern
```python
# first per-row loop (after slip_structure.run_single):
row["_enr"] = enr           # stash for second loop

# second per-row loop (before wnba_composite_gate):
wnba_generative_gate.run(row, enr=row.get("_enr") or {})
```

## Six dependency outputs (all in [0, 1])
`minutes_dependency`, `efficiency_dependency`, `close_game_dependency`,
`teammate_absence_dependency`, `overtime_dependency`, `three_pa_dependency`.
`dominant_dependency_share` = `max(above six)`.

## model_status values
- `PROVISIONAL` — all WNBA stats (calibration is provisional until 20 settled games)
- `UNSUPPORTED_STAT_KEY` — stat not in `SUPPORTED_STAT_KEYS`
- `INVALID_LINE` — line cannot be cast to float

## How to apply
Any new WNBA scoring path must route through `gate_engine/wnba/generative_model.py::score()`. Never add a second WNBA probability engine. New stat key support requires adding to `SUPPORTED_STAT_KEYS` and `_STAT_KEY_ALIASES` only.

## Test file
`tests/test_wnba_generative_model.py` — 87 tests across 20 test classes (T01–T20 + InternalMath). All pass in ~0.76s offline.
