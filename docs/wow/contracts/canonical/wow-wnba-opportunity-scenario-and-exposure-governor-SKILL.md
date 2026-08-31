# WOW WNBA Opportunity, Scenario, and Exposure Governor Skill v1

## Skill ID
`wow.wnba-opportunity-scenario-and-exposure-governor:v1`

## Required when
`sport=WNBA` — any WNBA row in the scoring request.

## Purpose
This skill governs the orchestration and interpretation of three legacy platform-enforced
engine modules for WNBA props. It does NOT calculate, fabricate, or estimate
any value — all scoring, stability scores, and exposure checks are computed
by legacy platform and returned in the API response.

## Maximum standalone ceiling
`MODEL_QUALIFIED_HOLD`

The GPT may not upgrade a WNBA row above MODEL_QUALIFIED_HOLD based on
its own research or judgment. All ceiling upgrades require legacy platform engine
evidence.

## Required evidence blocks
Every WNBA row response must include all of the following. If any are absent,
the row is NOT scoreable — return NO_PLAY for that leg:

### 1. opportunity_audit
```json
{
  "gate_passed": true,
  "opportunity_stability_score": 72,
  "minutes_stability_score": 74,
  "role_confidence": 0.83,
  "role_state": "SECONDARY_CREATOR",
  "archetype": "FLOOR_DRIVEN_PRIMARY_PRA",
  "rotation_volatility_score": 34,
  "blockers": []
}
```

### 2. role_state
A non-ROLE_UNKNOWN role_state from the opportunity_audit.
ROLE_UNKNOWN means the engine cannot confirm role — maximum ceiling is
MODEL_QUALIFIED_HOLD regardless of other evidence.

### 3. primary_teammate_dependency (for PRA props)
Must be resolved (not empty or unresolved) for PRA composite props.
Unresolved teammate dependency → WNBA_HOLD_ROLE_UNCERTAIN ceiling.

## Gate outcomes you must respect

| Gate label | Meaning | Action |
|---|---|---|
| WNBA_REJECT_UNSTABLE_OPPORTUNITY | OSS < threshold or minutes stability < 60 | NO_PLAY — do not present |
| WNBA_REJECT_ROTATION_VOLATILITY | Rotation volatility > 80 | NO_PLAY — do not present |
| WNBA_HOLD_ROLE_UNCERTAIN | role_confidence < 0.80 or insufficient data | Cap at MODEL_QUALIFIED_HOLD |
| REJECT_CROSS_SLIP_CONCENTRATION | Same player+stat_family already in session | NO_PLAY — duplicate distribution |
| REJECT_DUPLICATE_THESIS | Same player+stat+direction already in session | NO_PLAY — identical thesis |

## Call sequence for WNBA rows

1. GET /wow/engine/health → confirm `ok=true`
2. GET /wow/governance/status → confirm governance hash
3. POST /wow/wnba/opportunity-audit → get opportunity_audit per row
4. POST /gate-engine/run (response_mode=slim) → final scoring with all gates enforced

Step 3 is informational — step 4 is the enforcing call. Do not skip step 4.

## Evidence the GPT must supply (in candidates array for /wow/wnba/opportunity-audit)

Each candidate must include:
- player (string)
- sport = "WNBA"
- prop_type (string)
- line (number)
- direction ("MORE" | "LESS")
- slate_date (YYYY-MM-DD)
- board_source (string)
- game (string)
- game_log (array) — per-game stat objects from the last 10+ games

Each game_log entry: `{"MIN": 32, "PTS": 18, "REB": 4, "AST": 3, "FGA": 12}`

## Failure protocol

If legacy platform returns an opportunity_audit gate_passed=false, you MUST:
1. Record the gate_label and blockers in your session log
2. Return NO_PLAY for that prop
3. NOT attempt to override the gate with your own analysis

If legacy platform returns ROLE_UNKNOWN, you MAY research teammate availability
but you MUST resubmit to legacy platform for re-evaluation with updated dependency data.
You may NOT directly upgrade the ceiling yourself.

## can_execute
Always false. No monetary execution under any circumstances.
