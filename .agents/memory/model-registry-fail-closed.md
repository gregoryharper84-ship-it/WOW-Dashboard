---
name: Model registry + fail-closed rule
description: gate_engine/model_registry.py maps (sport, stat_key) to versioned model metadata; unsupported combos fail closed with null probability — no Claude fallback.
---

## Rule
`gate_engine/model_registry.py` is the single source of truth for which (sport, prop_type) combos have a codified probability model. Unsupported combos return `NO_REGISTERED_MODEL` and `hit_probability=None`. Claude must never be called as a fallback for probability computation.

## Why
ChatGPT architecture review: "AI acquires and interprets evidence; deterministic registered models produce the probabilities." A Claude-computed Poisson on an unsupported prop produces different numbers on separate runs — calibration cannot be measured and the prediction ledger loses value.

## How to apply
- `model_registry.lookup(sport, stat_key, line)` returns a dict with `model_id`, `model_version`, `calibration_version`, `status`, `minimum_inputs`.
- `status` values: `ACTIVE` (MLB binomial PA v2), `PROVISIONAL` (NBA/WNBA/MLB Poisson — λ=game-log mean, ignores minutes/role), `NO_REGISTERED_MODEL` (NFL, NHL, anything else).
- `hit_probability.compute()` Tier 3 now returns `MODEL_NO_REGISTERED_MODEL` + `hit_probability=None` instead of calling Claude.
- `is_supported(sport, stat_key, line)` is the fast boolean check.
- `probability_bounds(p, sample_size, model_status)` returns (lo, hi) uncertainty band: ACTIVE ±4%, PROVISIONAL ±8%, n<10 ±12%.
- Tests expecting `MODEL_CLAUDE` for NFL/NHL have been rewritten to expect `MODEL_NO_REGISTERED_MODEL`.

## Registered models
- `mlb_hits_binomial_pa_v2` — ACTIVE — H/HITS at line < 1.0
- `mlb_binary_bernoulli_v1` — PROVISIONAL — MLB binary stats (HR, RBI, SB, etc.) at line ≤ 1.5
- `mlb_counting_poisson_v1` — PROVISIONAL — SO, K, IP, Outs, TB
- `nba_counting_poisson_v1` — PROVISIONAL — PTS, REB, AST, STL, BLK, TOV, 3PM, FTM, combos
- `wnba_counting_poisson_v1` — PROVISIONAL — same stat set as NBA
