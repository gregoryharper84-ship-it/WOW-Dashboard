# WOW V17 — NFL Pick'em point-in-time pool strategy replay

Status: RESEARCH_PIPELINE_ONLY (no promotion). Issue #1517.

## Scope

A paired historical evaluation of the existing `MAX_EXPECTED_CORRECT` pick'em card versus the fixed `POOL_WIN_EQUITY_SHADOW` heuristic. This is **not** a new fitted NFL win model, a certified first-place-equity probability, or a live pool recommendation.

Implementation:
- `artifacts/wow-engine/v17/nfl_pickem_pool_equity_replay.py`
- `artifacts/wow-engine/tests/test_v17_nfl_pickem_pool_equity_replay.py`

## Required evidence per independent settled week

- Unique `week_id`, a predeclared `fold` (DISCOVERY/HOLDOUT), immutable `manifest_id`, and a precise pool `lock_at`.
- Exactly one already governed `PICKEM_READY` row per official NFL event, with canonical fitted-specialist identity, both normalized calibrated probabilities, source prediction ID, source snapshot ID, and a model timestamp no later than lock.
- Opponent-only pool ownership per event: normalized two-sided shares, independently identifiable `source`, `snapshot_id`, time `observed_at <= lock_at`, and `audience=OPPONENT_ENTRIES`. The user's own pick must not be mixed into the opponent distribution.
- Actual opponent cards as recorded by `submitted_at <= lock_at`, stable receipt IDs, full event-set matching, and exact pool-size reconciliation.
- Final event winner identities plus authoritative settlement source/time. Settled outcomes may be read **only for replay scoring**, not for pregame strategy selection.

Missing, contradictory, late, malformed, or duplicate evidence fails the **entire replay**, not just its affected row. Typed blocker codes identify the exact failure. No synthetic fixtures qualify as historical evidence.

## Paired metrics

On identical events and identical opponent cards, the replay separately measures baseline vs research-shadow:
- Average correct selections and expected-correct sacrifice.
- Sole first-place finishes and first-or-tied finishes.
- Equal-split first-place tie-share **proxy** and rank by correct selections.
- Deliberate pick differences and per-week deltas.
- Separate DISCOVERY and untouched HOLDOUT cohorts.

For your league, actual tied-week payouts may be decided by a separate Monday-night points tiebreaker. This replay intentionally **does not fabricate** competitors' tiebreaker guesses or present the equal-split proxy as actual award probability.

## Exact governance boundary

```text
SERVING_MODE=RESEARCH_REPLAY_ONLY
PRODUCTION_OBJECTIVE=MAX_EXPECTED_CORRECT (unchanged)
CHALLENGER_OBJECTIVE=POOL_WIN_EQUITY_SHADOW (research heuristic)
automatic_promotion=false
production_pick_mutation_allowed=false
sporting_probability_modified=false
can_execute=false
```

No odds, public consensus, pool percentages, or final results may replace or recalculate V17 sporting probabilities. The shadow code cannot auto-publish a pick.

## Next evidence gate

Acquire **multiple distinct, pre-lock weeks** of opponent ownership and immutable governed NFL prediction receipts; assign untouched holdout weeks before reading outcomes; run historical replay, counterexample review, owner-independence sensitivity, and forward shadow validation. Retain the precise cohort assignment receipt and artifact revision.

A fixture passing CI, a single successful Week 4 result, a green deploy, or a tie-share proxy is **not proof** of actual weekly pool-winning improvement. An independent governed review is required before recommending production strategy adoption.
