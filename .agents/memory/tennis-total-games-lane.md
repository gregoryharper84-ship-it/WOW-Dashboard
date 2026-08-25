---
name: Tennis Total Games lane
description: WOW v16 Clean Core tennis Total Games modeling lane — exact Markov chain, three-outcome (More+Exact+Less=1), surface-adjusted baselines, full classification pipeline.
---

## Architecture

**New files:**
- `gate_engine/tennis_total_games.py` — core model (exact Markov chain simulation; ~1270 lines)
- `gate_engine/tennis_total_games_gate.py` — pipeline gate; runs in second per-row loop after wnba_composite_gate, before classifier.classify()
- `tests/test_tennis_total_games.py` — 71 tests, all passing

**Modified files:**
- `gate_engine/normalizer.py` — added "total games", "total_games", "match total games", "match games", "game total" → TOTAL_GAMES in TENNIS _STAT_KEY_MAP
- `gate_engine/model_registry.py` — added ("TENNIS","TOTAL_GAMES") PROVISIONAL entry (model_id: tennis_total_games_markov_v1)
- `gate_engine/tennis_game_log.py` — added TOTAL_GAMES to valid stat keys; fetches match total games (my_games + opponent_games) from Sackmann data
- `gate_engine/hit_probability.py` — added "total_games" / "total_game" to _TENNIS_GAUSSIAN_STATS (Gaussian baseline fallback)
- `gate_engine/pipeline.py` — import tennis_total_games_gate; call tennis_total_games_gate.run(row) after wnba_composite_gate.run(row)

## Key design decisions

**Markov chain math (exact, not Monte Carlo):**
- `_game_win_prob(p)` = p⁴(1+4q+10q²) + 20p³q³ · p²/(p²+q²) — verified: G(0.5)=0.5
- `_tb_win_prob(p)` = Σ C(6+k,k)p⁷qᵏ + C(12,6)p⁶q⁶p²/(p²+q²) — verified: T(0.5)=0.5
- `_set_score_distribution()` — forward DP over (a,b) states; (6,6) handled as tiebreak meta-state
- `_bo3_total_games_distribution()` / `_bo5_total_games_distribution()` — convolves set distributions over all valid match orderings

**Three-outcome simplex invariant:**
- Raw and calibrated More+Exact+Less stored WITHOUT 6dp rounding to prevent FP drift
- `_calibrate_triple()` enforces `cl = max(0, 1.0 - cm - ce)` as exact complement
- Half-point lines: EXACT=0 always; integer lines: all three outcomes populated

**Surface baselines** (when player-specific serve% not provided):
- Hard ATP=0.635, WTA=0.595; Clay ATP=0.610, WTA=0.565; Grass ATP=0.660, WTA=0.625

**Pipeline placement:**
- Gate runs in second per-row loop; no-op for non-TENNIS or non-TOTAL_GAMES rows
- PROVISIONAL ceiling: MODEL_QUALIFIED_HOLD unconditionally
- can_execute=False unconditional at both module level and gate level

**Stress test direction:**
- MORE side: shrink serve advantage 20% (shorter matches → adverse for MORE)
- LESS side: inflate serve advantage 20% (longer matches → adverse for LESS)
- Lower bound = calibrated stress probability (not fixed haircut)

**Classification:**
- Strong: lb≥0.60 AND stress_drop≤0.05
- Qualified: lb≥0.55 AND stress_drop≤0.08
- Marginal: lb≥0.52 AND stress_drop≤0.10
- Fragile: lb<0.52 OR stress_drop>0.10
- Reject: calibrated<0.51 OR data stale OR invalid line

**Why:**
The user specified a probability-only model (no 1–10 confidence score) with full match-state simulation, three-outcome contract, mandatory stress test, dependency audit, failure-path audit, and market prior capped at governance ceiling.
