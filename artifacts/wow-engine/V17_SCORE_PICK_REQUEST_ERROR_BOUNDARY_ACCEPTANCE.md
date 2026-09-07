# V17 score-pick-request typed error-boundary acceptance

This repair is intentionally narrow. It does not alter sporting-model inference,
calibration, ranking, evidence gates, exact-line handling, or terminal-reducer
authority.

## Frozen regression row

The live failure that motivated the patch used this immutable row only as a
regression identity:

- player: Dylan Cease
- sport/stat: MLB / PITCHER_STRIKEOUTS
- direction/line: MORE 7.5
- event: Toronto Blue Jays at Athletics
- frozen start: 2026-09-08T02:05:00Z
- caller row key: cease-k-more-7p5-20260907
- caller retry request id: wow-single-row-test-20260907-cease-k-7p5-retry1

The source event identifier observed in the live attempt is not certified here
as an MLB-issued `gamePk`. Tests must not convert it into official MLB identity.

## Acceptance contract

1. A completed sporting-model row remains a completed sporting-model receipt if
   downstream portfolio/card governance throws.
2. The downstream failure is typed `PORTFOLIO_GOVERNANCE_UNAVAILABLE`, blocks
   downstream portfolio evaluation, and preserves `can_execute=false`.
3. Raw/model/calibrated probabilities, calibrated lower bound, ranking fields,
   and sporting terminal status are not mutated by that downstream failure.
4. A scorer-owned unexpected exception remains `MODEL_SCORER_FAILED`; it is not
   rewritten as `MODEL_UNAVAILABLE`.
5. Backend HTTP details that already carry a typed V17 failure remain intact,
   with `scoring_attempted=true` once `score_prop` was actually called.
6. No missing/unverified official MLB event identifier may be fabricated.

A production live Action replay is still required after merge/deployment. A
repository test pass alone is not `FIXED_VERIFIED`.
