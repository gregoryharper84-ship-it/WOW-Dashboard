# V17 Probability Calculator Evidence Binding

This file binds `V17_DETAILED_EVIDENCE_COLLECTION_AND_PREDICTION_CONTRACT.md` and `v17/contracts/detailed-evidence-envelope.v17.schema.json` to the shared `V17_CERTIFIED_NUMERICAL_ENGINE` workflow.

## Required pre-inference sequence

For every supported pregame player/scalar prop or team/event outcome request:

1. Resolve and freeze canonical event/candidate identity.
2. Hydrate the detailed evidence envelope using the applicable sport adapter.
3. Preserve source, `as_of`, sample size, data quality, certainty and `feature_status` for each material item.
4. Route each item as exactly one of `MODEL_INPUT`, `REGIME_INPUT`, `CALIBRATION_INPUT`, `MARKET_EVIDENCE`, or `EVIDENCE_ONLY`.
5. Keep `MARKET_EVIDENCE` outside the sporting feature vector unless the exact certified model/calibration contract explicitly declares a market-prior input.
6. Send only certified numeric features/regimes to the controlling specialist.
7. The controlling specialist selects the certified model family and inference method; the shared numerical engine executes it.
8. Apply required failure-path integration, independent numerical verification, dynamic calibration and lower/upper bounds.
9. Submit the resulting package to `V17_TERMINAL_REDUCER`.
10. Perform exact-line market/value/risk/card analysis only after a valid sporting probability package exists.

## Selection behavior

- Analyze all supported candidate markets; do not prioritize one market family merely because it is easier to model.
- Return **up to three qualified predictions per game/event** when requested, not an unconditional three.
- Never add filler to satisfy a count target.
- Market diversity is secondary to qualification. A weaker candidate cannot displace a stronger qualified candidate solely to create market variety.
- Specialty markets (corners, cards, Asian handicap, exact score, first scorer, period/half markets, etc.) require their own certified route.
- Probability-only ranking uses the governed calibrated lower bound when the lane contract requires it.
- Value/edge ranking requires fresh exact-line evidence and remains downstream.

## Sport adapter expectations

Adapters should map the common evidence families to sport-specific numeric features without changing terminal semantics. Priority discovery coverage includes NBA, WNBA, NFL, College Football, College Basketball, MLB, Tennis, Boxing, MMA and major soccer competitions. This is a discovery priority, not a probability prior.

## Prematch / live separation

Prematch is the default. A live request requires a separately certified live model/evidence/calibration route. Pregame probabilities may not be heuristically transformed into live probabilities.

## Risk / bankroll separation

Risk labels and bankroll sizing are downstream decision-support outputs. They must never alter the immutable sporting model probability merely because payout, correlation or duplicate-thesis exposure differs. Fractional-Kelly or unit sizing requires both a valid governed probability package and fresh exact price. `can_execute=false` always.

## Failure semantics

- Missing controlling fitted artifact: `MODEL_UNAVAILABLE`.
- Insufficient certified inputs: `MODEL_INPUTS_INSUFFICIENT`.
- Invoked scorer/computation failure: preserve the typed scorer/computation failure.
- Invalid numeric package: `MODEL_OUTPUT_INVALID` or exact typed output error.
- Required independent verification conflict/failure: preserve `COMPUTATION_VERIFICATION_CONFLICT` / `COMPUTATION_VERIFICATION_FAILED`.
- Do not rewrite a research-data gap as a model result unless it actually prevents required certified inputs.

## Governance invariants

Evidence can change certified inputs, explicit regimes, uncertainty and calibration. Evidence-only narrative cannot directly change probability. Market odds cannot become governed sporting probability. Exactly one controlling specialist owns the sporting probability. `V17_TERMINAL_REDUCER` is sole terminal authority. `can_execute=false`.
