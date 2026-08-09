---
name: WOW-PATCH-2026-08-08-MLB-SP-SCRATCH — MLB starter-change retro patch
description: Aug 8 Rays-Mariners postmortem. Replaces fixed scratch penalty with ERA-based quality delta + separate uncertainty expansion.
---

## Problem
Late SP scratch (Griffin Jax) triggered automatic ~10pp downgrade regardless of replacement plan quality. Double-counted uncertainty as both quality deterioration and calibration penalty.

## Architecture

### New module: `gate_engine/moneyline/mlb_starter_change.py`
- `analyze_mlb_starter_change(row, enrichment) → StarterChangePlan`
- Only fires for MLB; all other sports return NO_CHANGE_DETECTED
- Four classifications: KNOWN_DOWNGRADE, ROUGHLY_NEUTRAL, KNOWN_UPGRADE, UNRESOLVED_REPLACEMENT
- Three architectures: SINGLE_REPLACEMENT, OPENER_BULK, BULLPEN_GAME — all legitimate, none auto-penalized

### Quality delta (point estimate)
- `era_delta = replacement_era - original_era` (positive = worse replacement)
- `probability_adjustment = -era_delta × 0.025`; cap ±0.08
- Away scratch → inverted (away pitcher worse → positive home-perspective adjustment)
- Applied to `fp_result.adjusted_win_prob` at stage 5.5 — NOT to `independent_prob_post_sim` (stage 6 reads `fp_result.adjusted_win_prob`, so must adjust that object in-place)

### Uncertainty expansion (separate effect, no double-counting)
- Architecture component: OPENER_BULK +0.015, BULLPEN_GAME +0.025, SINGLE_REPLACEMENT +0.005
- Workload component: TAXED +0.01, DEPLETED +0.02, UNKNOWN +0.015
- No-leverage-arms component: +0.01 when False
- Capped at 0.05; injected as `starter_change_uncertainty_expansion` in enrichment before calibration

### Pipeline wiring (`pipeline.py`)
- Stage 5.5: between failure_path (stage 5) and candidate extraction (stage 6)
- UNRESOLVED → early return MODEL_QUALIFIED_HOLD before candidate extraction
- `enrichment = dict(enrichment)` shallow copy before injecting uncertainty key

### Calibration wiring (`dynamic_calibration.py`)
- New helper `_starter_change_uncertainty(enrichment)` reads `starter_change_uncertainty_expansion`
- Named component `starter_change_uncertainty` in `CalibrationResult.uncertainty_components`
- Added to `raw_uncertainty` sum (separate from quality delta)

### Enrichment contract (GPT sends these fields)
- `sp_change_detected: bool`
- `sp_change_side: "home" | "away"`
- `sp_original_era: float` (optional; defaults to MLB avg 4.10)
- `sp_replacement_plan_era: float` (absence → UNRESOLVED)
- `sp_replacement_architecture: "OPENER_BULK" | "SINGLE_REPLACEMENT" | "BULLPEN_GAME"`
- `sp_bullpen_workload: "FRESH" | "TAXED" | "DEPLETED" | "UNKNOWN"`
- `sp_late_trigger: str`
- `sp_leverage_arms_available: bool`
- `sp_replacement_arms: list[{role, era, expected_innings}]` (optional weighted-average fallback)
- `sp_bullpen_aggregate_era: float` (BULLPEN_GAME fallback)

## Critical bug fix found during implementation
`independent_prob_post_sim` != `fp_result.adjusted_win_prob`. Stage 6 reads `fp_result.adjusted_win_prob` directly. Quality delta must be applied to `fp_result.adjusted_win_prob` (mutable dataclass field), not `independent_prob_post_sim`, or the adjustment is silently discarded.

## Tests
`gate_engine/tests/test_mlb_starter_change.py` — 60 tests (SC01–SC20), all pass.
Pre-existing failures: 9 (hit_probability + wnba_evidence_acquisition — unrelated).
