# LLP Multisport Spread Research Card — Design (2026-11-12)

**Intent.** Extend the existing LLP team/event spread lane to produce auditable exact-line full-slate research cards and independent-result retrospectives across sports. Winning against the spread is a different event from winning the game; no moneyline or market-implied probability becomes a fitted spread probability.

**Owner.** LLP_TEAM_BETTING_ENGINE; umbrella #2076; existing fitted spread margin challenger, spread_card_admission, spread_certification_decision, #1563 independent SIRT assurance. This addition is a research audit/presentation consumer, not a new fitted-model scorer or a new terminal authority.

**User outcome.** One explicit HOME/AWAY spread/puck/run/handicap thesis for each playable official event where a frozen exact line exists; otherwise a visible typed HELD event. Show the full scheduled denominator, official verification status, real-source freshness, independent settlement grades, and sport breakdown. Never hide losses, pushes, quarter-line splits, missing games, or ungraded selections.

## Scope

- Support US full-game point spreads: NFL, NBA, WNBA, NCAAF, NCAAB (overtime included).
- Support NHL puck line (overtime and shootout in official final), MLB run line (extra innings), soccer regulation Asian handicap (quarter-line split), and tennis match games/sets handicap with completed match, no retirement. Other sports/markets are visible as unsupported, not inferred.
- A selection is always chosen by the caller from source-frozen evidence. This helper cannot predict a side, create probabilities, rank, certify, place bets, or auto-submit Pick'em.
- Caller supplies canonical official-event manifest, pregame pick receipt, exact market-line snapshot and optional independently finalized settlement. Caller-provided attestation is NOT direct proof of independent source authenticity.
- Market snapshot must match official sport/event/teams/start/market/period and signed home line, have a receipt and source timestamps pre-freeze and pre-kickoff. It is never a model probability; stale quotes fail closed for the exact-line research card.
- Valid timestamps require timezone offsets. Duplicates and event/opponent swaps hold all ambiguous records. Scores, integer line pushes, quarter handicap half outcomes and tennis retirement need explicit settlement semantics.
- Complete pregame card requires independently attested manifest and one validated pregame selected side and line per playable event. Retrospective complete requires independently attested official settlement per playable event; unverified or pending outcomes suppress headline accuracy.
- Presentation retains status `PROVISIONAL_RESEARCH_ONLY` and `probability_publishable=false`; no fitted probabilities are emitted. Uncertified research never enters governed calibration or promotion. Existing `spread_card_admission` remains the only rank boundary for a separate verified fitted receipt.
- Do not duplicate #2547's existing *winner* retro or mutate its PR. Do not change production APIs, database, reviewer workflows, terminal reducer, model coefficients, or source sheet. No new paid providers.

## Approaches considered

1. **Retune/extend the existing fitted margin challenger to all sports now** — wrong: tennis, soccer and NHL settlement/data regimes differ, and no independent historical calibration/certification is established.
2. **New competing predictor/scorecard** — wrong: duplicates LLP ownership and risks mixing provisional narratives into fitted calibration.
3. **Chosen: small additive LLP research-card and settlement contract** — reuses the established scorer/line-eligibility boundary, gives dependable full-slate visibility and grading, and defines typed gaps for sport-specific fitting later.

## Acceptance

- Adversarial tests for duplicated/reused IDs, unverified/missing official slate, opposite sides, wrong lines, stale/late quotes, missing/future finals, NBA overtime, NHL shootout, MLB extras, soccer quarter-line half outcomes, tennis set-vs-game metrics and retirements, unsupported formats, integer pushes, huge numbers.
- TDD red/green locally where possible; CI from exact GitHub head; independent semantic SIRT and QA/Release for protected merge; deployed readback before any claim of live integration.
- `can_execute=false`, `V17_TERMINAL_REDUCER` sole terminal authority. `automatic_certification=false`, `automatic_promotion=false`.
