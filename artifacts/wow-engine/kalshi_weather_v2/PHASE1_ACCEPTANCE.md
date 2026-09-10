# Kalshi Weather V2 — acceptance contract

The engine remains analytical only: `can_execute=false`.

## Shared acceptance gates

1. Exact contract identity is acquired and frozen before weather scoring.
2. Exact settlement source/location is verified from the governing contract evidence.
3. Market price is never used as a weather-model input.
4. Source retrieval/issuance timestamps must not be later than analysis time.
5. Missing authoritative evidence fails closed rather than being inferred.
6. Executable market price is derived from live orderbook semantics, not displayed chance/midpoint.
7. Probability publication and edge publication remain separate gates.
8. No order-placement, cancellation, modification, or capital-allocation surface exists.

## Hourly temperature

The existing hourly shadow path remains governed by exact target-time semantics, independent NWS/Open-Meteo forecast evidence, exact settlement-index reconciliation, calibrated probability availability, and immutable persistence.

## Daily high temperature

Daily-high evidence is acceptable only when:
- `lane=DAILY_HIGH_TEMPERATURE`;
- contract timezone is explicit and valid;
- NWS hourly forecast contains the target local calendar date;
- max-so-far is reconstructed from official station observation rows for that same contract-local date;
- settlement source and settlement location identity are already verified by the contract layer;
- Open-Meteo daily model aggregation, when used, is requested in the exact contract timezone;
- Open-Meteo is disagreement/corroboration evidence and cannot overrule official settlement evidence;
- all source timestamps are decision-time safe;
- evidence metadata explicitly records `market_price_used_as_input=false`.

Daily-high evidence fusion by itself does not certify daily-high probability publication. A full daily-high shadow runtime, station/lead-time calibration certification, capability ratification, forward acceptance, and exact settlement reconciliation are still required.
