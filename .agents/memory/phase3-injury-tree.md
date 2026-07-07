---
name: Phase 3 injury decision tree
description: WNBA role-dependency rules, classifier cap structure, and the MARKET_CONTRADICTION/cap-placement gotcha.
---

## Rule

`gate_engine/injury_decision_tree.py` evaluates a player's dependency on a teammate's injury status or game-script risk before final classification. The tree can BLOCK FINAL_APPROVED; it cannot CREATE it.

Eight hardcoded DEPENDENCY_RULES in the module. Keyed by:
- `beneficiary_keywords` — substrings in row player name (lowercased)
- `prop_trigger_keywords` — any substring in row prop_type (lowercased)
- `direction` — "MORE" / "LESS" / None

Players: Williams←Miles (assists), Amoore←Citron (pts), McMahon←Citron (pts),
Atkins←Plum+Brink (pts/combos), Hamby←Plum+Brink (pts/combos), Howard←game_script.

**Multi-dependency combining:** most-restrictive wins across all matching rules.
CONFLICT > UNRESOLVED > STALE > SUPPORTS > CLEAR > NO_DEPENDENCY

**Staleness:** `confirmed_at` older than `STALE_THRESHOLD_HOURS` (6h) → ROLE_STATE_STALE regardless of status value.

**dependency_status_payload:** per-row enrichment key `dependency_status_payload` — dict of `{lowercased_player_name: {status, confirmed_at, source}}`. Game-script uses `_game_script` sentinel key.

## Classifier cap placement gotcha

**Why caps must be OUTSIDE the market-verified branch:**

MARKET_CONTRADICTION sets `market_gate.market_status = "MARKET_CONTRADICTION"` and
`confidence_cap = "MODEL_QUALIFIED_HOLD"`. The classifier's `if mkt_status in ("MARKET_VERIFIED", "MARKET_EDGE_DETECTED")` check is False for MARKET_CONTRADICTION, so the row falls through to the MONEY_QUALIFIED fallback — bypassing the confidence_cap read entirely.

**Fix (implemented):** Phase 2 + Phase 3 caps form a unified priority block applied after the EV check, before market routing:

```python
hard_cap = confidence_cap=="MODEL_QUALIFIED_HOLD" OR inj_status==DEPENDENCY_CONFLICT
soft_cap = confidence_cap=="MONEY_QUALIFIED_MAX"  OR inj_status in (UNRESOLVED, STALE)
```

Both blockers are appended if both are triggered simultaneously.

**How to apply:** Any future classifier gate that controls FINAL_APPROVED access must be added to this shared cap block, not nested inside the `if mkt_status in (...)` branch.

## Pipeline wiring

`injury_decision_tree.run(row, dependency_status_payload=enr.get("dependency_status_payload"))`  
— called immediately after `status_role.run()` in the per-row loop.

`build_injury_decision_ledger(rows)` called in `_build_output()`.  
Return dict: `injury_decision_ledger`, `summary.injury_dependency_count`, `summary.unresolved_dependency_count`.
