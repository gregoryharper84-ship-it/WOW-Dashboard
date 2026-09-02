# WOW V17 Research + Market Context Skill

skill_id=WOW_V17_RESEARCH_MARKET_CONTEXT
owner=V17_SHARED_RESEARCH_MARKET_CONTEXT
can_execute=false
probability_authority=NONE
terminal_authority=NONE

## Trigger
Invoke automatically before final ranking for `WOW_V17_DAILY_PICKS`, `WOW_V17_BEST_PROPS`, `WOW_V17_ML_WINNERS`, and `WOW_V17_SCREENSHOT_REVIEW` whenever current external context can materially affect identity, fitted inputs, evidence quality, market interpretation, or final risk reporting. It may also be called directly with `Run V17 Research Context`.

## Purpose
Refresh and reconcile the evidence around a candidate without creating a new model layer. Research may hydrate certified model inputs and audit model output; it may never substitute for the controlling fitted model, invent a probability, or add an uncoded narrative probability penalty.

## Evidence contract
For each material candidate, gather when relevant and available:
1. exact schedule/event identity, opponent, venue and start time;
2. confirmed/probable starters, lineups, rotations/rosters and participant availability;
3. injuries, suspensions, scratches, team changes and status updates;
4. role, workload, opportunity and recent usage;
5. relevant recent form plus an appropriate longer-run historical sample;
6. opponent/matchup and handedness/style/surface/context splits when sport-specific;
7. rest, travel, back-to-back/fatigue context and venue/environment/weather where applicable;
8. current exact-line market prices across credible books when available;
9. opener/current movement and consensus/no-vig context when available;
10. source/provenance and an as-of timestamp for every material item.

## Temporal provenance
- Record when each material input became knowable.
- Do not use lineup, injury, closing-line, settled-result, or other future information that was unavailable at the decision timestamp.
- If evidence is stale, contradictory, or unavailable, record that condition instead of guessing.

## Market evidence typing
Every external market observation must be classified as one of:
- `EXACT_LINE`: same participant/event/stat/line/side identity as the candidate; may support exact-line no-vig/economics analysis.
- `ADJACENT_LINE`: related but different threshold/side/market identity; context only, never exact-line authority.
- `NO_MARKET`: no suitable current comparable market found.

Sportsbook implied probability, market consensus, external projections, rankings, records, hit rates, or narrative judgment are not governed model probability.

## Model interaction
- Where the certified specialist has a fitted input for refreshed evidence, ensure that input is supplied through the governed hydration/scoring path.
- Where no fitted coefficient/input exists, preserve the evidence as supporting/contradicting context only.
- Never manually apply a second numeric penalty after the backend has already incorporated the same evidence.
- Missing odds may block value/edge/economics publication but must not erase a completed sporting probability where the backend contract preserves it.
- Unsupported fitted routes remain fail closed even when market consensus strongly favors a side.

## Output contract
Return a compact context package containing:
- identity/status freshness
- material supporting evidence
- material contradictions
- evidence as-of timestamp(s)
- source/provenance summary
- market evidence type (`EXACT_LINE`, `ADJACENT_LINE`, `NO_MARKET`)
- exact-line market/no-vig context when available
- opener/current movement when available
- whether each material item is `MODEL_INPUT`, `EVIDENCE_ONLY`, or `ECONOMICS_ONLY`

The downstream ranked leaderboard still uses the governed fitted-model probability package and calibrated lower bound where required.

## Safety
- No probability fabrication.
- No market-probability substitution.
- No look-ahead leakage.
- No new model layer.
- No wager placement.
- `can_execute=false` always.
