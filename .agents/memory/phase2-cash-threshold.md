---
name: Phase 2 cash threshold enforcement
description: How market_gate validates sportsbook lines against pp cash_threshold, not displayed_line; classifier confidence_cap enforcement.
---

## Rule
`market_gate._validate_cash_threshold()` compares `sportsbook_line` against `pp_thresholds["cash_threshold"]`, not the displayed PP line. Tolerance = 0.5 units.

| Scenario | cash_threshold_status | confidence_cap |
|---|---|---|
| sb within 0.5 of cash_threshold | EXACT_VERIFIED | None |
| sb within 0.5 of displayed (whole-number line) | CASH_THRESHOLD_NOT_VALIDATED | MODEL_QUALIFIED_HOLD |
| sb within 0.5 of displayed (half-point line) | ADJACENT_CONTEXT_ONLY | MONEY_QUALIFIED_MAX |
| no sportsbook line | MARKET_UNVERIFIED_EXACT | MODEL_QUALIFIED_HOLD |
| MARKET_CONTRADICTION | SOURCE_CONFLICT (override) | MODEL_QUALIFIED_HOLD |
| no pp_thresholds on row | NO_PP_THRESHOLDS | None (backward compat) |

Key example: MORE 5 (whole), cash=6, sb=4.5 → |4.5−6|=1.5 → not exact; |4.5−5|=0.5 → adjacent whole → `CASH_THRESHOLD_NOT_VALIDATED` → `MODEL_QUALIFIED_HOLD`. Same line with sb=5.5 → |5.5−6|=0.5 → EXACT_VERIFIED.

**Why:** A sportsbook 4.5 OVER market tells you the sportsbook thinks the player is 50/50 around 4.5. It says nothing about whether the player will get 6+ (what you need to cash PP MORE 5). Treating 4.5 as market validation was the Courtney Williams MORE 5 assists bug.

**How to apply:**
- `market_gate.run()` reads `row.get("pp_thresholds")` (attached by Phase 1 `pp_thresholds.run_batch`)
- `classifier.classify()` reads `market.get("confidence_cap")` before routing to FINAL_APPROVED/MONEY_QUALIFIED
- `pipeline._build_output()` emits `market_validation_ledger` with one entry per row
- Backward compat: rows without pp_thresholds get `NO_PP_THRESHOLDS` / `confidence_cap=None` — no behavior change
- MARKET_CONTRADICTION detection: delta must be in range (−0.5, −0.04) to reach CONTRADICTION; delta ≤ −0.5 → SEVERE_DRIFT (not CONTRADICTION)
