---
name: WOW-PATCH-2026-08-10 registry design
description: Registry-driven probability-bearing label classification and outlier threshold reuse patterns established in this patch.
---

# WOW-PATCH-2026-08-10 Registry Design

## Probability-bearing label registry (prob_ledger_enforcer.py)

Two-layer frozenset, built at import time:
- Layer 1 (`_PROB_BEARING_PROP_LABELS`): PropLabel enum members with qualifying semantics — MODEL_QUALIFIED_HOLD, MARKET_VERIFIED_HOLD, MONEY_QUALIFIED, FINAL_APPROVED.
- Layer 2 (`_PROB_BEARING_EXTENDED`): Labels identified in taxonomy audit that exist as string literals but are not yet PropLabel members — MARKET_VERIFIED_HOLD_STALE, FINAL_CONFIDENCE_HIGH, FINAL_LOCK, EDGE_QUALIFIED.

Union: `PROBABILITY_BEARING_LABELS = frozenset({lbl.value for lbl in Layer1} | Layer2)`

Enforcement: `label in PROBABILITY_BEARING_LABELS` — never if/elif on specific label names.

**Why:** The codebase had 4+ inconsistent qualifying-label sets (route_registry, cross_sport_ranker, app.py, prob_ledger_enforcer) with different members. The two-layer approach ties enforcement to the canonical PropLabel enum while allowing extension without modifying the enum.

**How to apply:** Adding a new qualifying label: add to _PROB_BEARING_PROP_LABELS (if it's a PropLabel member) or _PROB_BEARING_EXTENDED (if it's a new string literal). No enforcement logic changes needed.

## Outlier threshold reuse (outlier_recompute.py)

`GAP_THRESHOLD` and `ASSIST_VOL_THRESHOLD` are imported from `gate_engine.outlier_gate`, not re-defined. The prior Stage A version invented its own thresholds — this was the key defect fixed in this patch.

**Why:** If the gate threshold changes, the recompute engine must change automatically. Re-defining it creates a silent divergence.

## Evidence-backed exclusion contract

The engine re-verifies each flag's criterion from raw `l10_games` data. It does NOT trust the flags dict. If flags claim `season_high_outlier=True` but `max(games) <= l10_avg * 1.5`, no candidate is identified and the result is UNRESOLVED.

**Test pattern:** Supply a row with manipulated flags but clean data → assert UNRESOLVED with `NO_EVIDENCE_BACKED_CANDIDATE` reason.
