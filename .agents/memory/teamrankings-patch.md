---
name: TeamRankings Secondary Enrichment Patch
description: WOW-PATCH-2026-08-08-TEAMRANKINGS-SECONDARY-ENRICHMENT — architecture, access model, weight governance, contradiction logic, and wiring points in the moneyline pipeline.
---

# TeamRankings Secondary Enrichment

**Patch ID:** WOW-PATCH-2026-08-08-TEAMRANKINGS-SECONDARY-ENRICHMENT  
**Source role:** SECONDARY_ENRICHMENT / PUBLIC_PREDICTIVE_MODEL  
**Sports:** NBA, WNBA, MLB, NFL, NCAAF, NCAAB  

## Access model
TeamRankings has no authorized public API. All data must be supplied by the GPT operator in `enrichment["teamrankings"]`. If absent → DATA_UNOBTAINABLE (base model unaffected).

## Files
- `gate_engine/moneyline/teamrankings_adapter.py` — adapter, data classes, weight/contradiction logic
- `gate_engine/moneyline/sport_model.py` — `_teamrankings_predictive()` submodel + ensemble cap
- `gate_engine/moneyline/pipeline.py` — stage 2.5 (extraction+injection) + stage 6.5 (contradiction fill)
- `gate_engine/moneyline/types.py` — `teamrankings` field in `MoneylineResult` layers
- `gate_engine/tests/test_teamrankings_enrichment.py` — 51 tests, all 8 governance invariants

## Weight governance
- Default: 7.5% of sport model ensemble (TR_WEIGHT_DEFAULT)
- Hard ceiling: 10% (TR_WEIGHT_MAX), enforced post-normalization in ensemble
- Zero when: STALE (>4h), DATA_UNOBTAINABLE, PROXY_ONLY, SOURCE_CONFLICT
- Requires direct `matchup_win_prob_home` — raw ratings CANNOT be converted without calibrated mapping

## Contradiction logic
- Fires when TR matchup_win_prob_home differs from core model by ≥ 8pp (home perspective)
- OPPOSITE_SIDE: TR favors opposite winner → TEAMRANKINGS_CONTRADICTION_REVIEW blocker added (non-terminal)
- DISCREPANCY: same-direction diff ≥ 8pp → contradiction_flag=True, uncertainty widens
- TEAMRANKINGS_CONTRADICTION_REVIEW is in `_NON_TERMINAL_REVIEW_PREFIXES` — will never cause DATA_CONTRACT_FAIL

**Why:** Contradiction review flag must lower confidence but never flip the pick or block qualification.

## Key invariants (tested)
1. TR cannot override lineup/participant blocker (fires before TR stage 2.5)
2. Missing TR → DATA_UNOBTAINABLE, base model unchanged
3. Stale TR → zero weight, not injected
4. `display_odds` stored in team records only — NEVER in clean_enr or sportsbook_odds
5. TR lives in sport model layer, NOT market_weight — market cap unchanged
6. No raw rating → probability without calibrated mapping
7. OPPOSITE_SIDE → TEAMRANKINGS_CONTRADICTION_REVIEW in result.blockers (non-terminal)
8. can_execute=False unconditional in all new modules

## Pipeline wiring
- Stage 2.5 (after participant lock): `extract_teamrankings_enrichment()` + `inject_tr_features_into_clean_enrichment()`
- Stage 6.5 (after candidate-side prob extraction): `tr_enr.fill_contradiction(prob_home)` + update `result.teamrankings`
- Contradiction annotation added to disagreement_audit["notes"]
- `result.teamrankings` initialized immediately after injection so early-return paths carry it too

## Follow-up tasks
- #144: Update moneyline LLP skill with enrichment["teamrankings"] schema
- #145: E2E GPT session confirmation
- #146: Calibrated rating→probability mapping for NFL/NCAAF/NCAAB
