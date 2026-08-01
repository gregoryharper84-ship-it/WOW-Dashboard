---
name: Aug 1 postmortem patches
description: Three new WOW patches shipped 2026-08-01; modules, governance hash change, and test count update.
---

# 2026-08-01 Postmortem Patches

## Three new ACTIVE patches (precedence 93/94/95, total now 16)

| Patch ID | Precedence | Module |
|---|---|---|
| WOW-PATCH-2026-08-01-CROSS-SLIP-DUPLICATE-GUARD | 93 | gate_engine/portfolio/slip_exposure_ledger.py |
| WOW-PATCH-2026-08-01-1IP-EFFICIENCY-GAP-ENFORCE | 94 | gate_engine/mlb/first_inning_efficiency.py |
| WOW-PATCH-2026-08-01-PITCH-COUNT-DIRECTIONAL-ASYMMETRY | 95 | gate_engine/mlb/first_inning_efficiency.py |

## CROSS-SLIP-DUPLICATE-GUARD key design

- New table: `wow_session_thesis_exposure` (DDL in slip_exposure_ledger.py)
- Exact duplicate key: `player|stat|line|side` (all four must match)
- Distribution-family key: `player|stat|side` (direction-inclusive, line-agnostic)
- Tiers: TIER_0=PASS, TIER_1=PASS_WITH_DISCLOSURE(0-20%), TIER_2=HOLD(dist>20% or unknown denom), TIER_3=HARD_STOP(exact dup>20%)
- TIER_3 cannot be overridden

## 1IP-EFFICIENCY-GAP-ENFORCE key design

- 7 Tier-1 metrics, weights: P/BF(0.20), pitches/start(0.20), walk_rate(0.15), 1st-pitch-strike(0.15), zone_rate(0.10), BB_rate(0.10), CSW(0.10)
- Each metric scored 0.0/0.5/1.0
- Tier-2 modifiers (WHIP, hard-hit, chase): up to 0.10 extra
- Fewer than 4 of 7 Tier-1 metrics → EFFICIENCY_SCORE_INCOMPLETE → cap at MODEL_QUALIFIED_HOLD
- Bands: STABLE(<0.30), MILD_DETERIORATION(0.30-0.50, -0.02 haircut), MATERIAL_DETERIORATION(0.50-0.70, cap=HOLD), SEVERE_DETERIORATION(>=0.70, cap=WATCH)
- ERA/xERA are contextual only — no weight

## PITCH-COUNT-DIRECTIONAL-ASYMMETRY key design

DFS = 0.35*three_batter_less_dependence + 0.30*extended_inning_loss_rate + 0.20*right_tail_mass + 0.15*min(1, gap/0.10)

- LOW(<0.55), MODERATE(0.55-0.70, -0.02 lower bound), HIGH(0.70-0.80, cap=HOLD), SEVERE(>=0.80, cap=WATCH)
- Hard override: tbl>=0.80 AND eilr>=0.70 → SEVERE unconditionally
- apply_lowest_ceiling() enforces: event-tree → efficiency → directional → market → slip → cross-slip → final

## Skill files in skills/

- `wow-mlb-first-inning-pitch-count-expert-SKILL-v3.md` — v3 (controls 1IP lane, failure-path supplies priors only)
- `wow-slip-probability-optimizer-SKILL-v3.md` — v3 (refreshed from handoff)

## Test count

1728 passed, 6 skipped after these patches. Patch count assertions updated to 16 in two test files.

**Why:** Both the 1IP gate and DFS gate must appear in the governance hash so the Custom GPT's expected_governance_hash must be refreshed after deploy.
