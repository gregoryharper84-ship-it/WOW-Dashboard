# Kalshi Weather V2 — R2 replay certification accelerator

This document freezes the historical-replay policy for `KALSHI_WEATHER_V2_OM_SINGLE_RUN_R2` **before the first R2 certification decision**. It supplements, but does not weaken, `EMPIRICAL_ACCEPTANCE_THRESHOLDS.md`.

## Scope

R2 is an hourly temperature probability model for the exact `H13_24` lead-time bucket. It is deliberately replayable: the weather central estimate is the deterministic median of two explicit Open-Meteo Single Runs model members:

- `gfs_seamless`
- `ecmwf_ifs025`

Market price, order book, Kalshi implied probability, and the settlement value are never model inputs.

## No-lookahead decision policy

For each historical weather target:

- synthetic decision time = exact Kalshi rule observation time minus **18 hours**;
- model run initialization must be the latest 00/06/12/18 UTC cycle no later than **decision time minus 6 hours**;
- the 6-hour availability lag is deliberately conservative for global-model publication latency;
- the exact run URL and response hash are frozen with each replay sample;
- any row whose safe availability timestamp exceeds decision time is ineligible.

The same run-selection policy is used by the live R2 runtime, using the real analysis time instead of a synthetic decision time.

## Settlement regime identity

R2 certification accepts only current-rule hourly events whose controlling Kalshi rule explicitly names **Synoptic Data** and whose exact settled value is present in Kalshi's settled-market `expiration_value` field.

Older AccuWeather-settled events are a different settlement regime and are excluded. Threshold siblings for one event timestamp count as one weather-target residual; the siblings are retained only to replay exact probability predicates.

## Frozen empirical gates

The controlling residual gates remain those in `EMPIRICAL_ACCEPTANCE_THRESHOLDS.md`:

- 50 unique targets total;
- first 30 chronologically earlier targets for training;
- next 20 chronologically later targets for untouched holdout;
- absolute holdout mean error <= 0.75°F;
- holdout MAE <= 2.5°F;
- one-sigma coverage between 50% and 85%;
- no source-identity, settlement-quality, or lookahead blocker.

For the previously qualitative probability-level requirements, R2 freezes these machine-review thresholds before the first certification decision:

- candidate holdout Brier score may not exceed the uncalibrated baseline Brier by more than **0.01**;
- a probability bucket is treated as severely miscalibrated only when it contains at least **10** exact predicates and its absolute predicted-vs-observed gap exceeds **0.25**;
- market-price blindness must be explicitly true.

These thresholds are not evidence that the model has passed. They define the test that the future data must pass.

## Promotion separation

The replay collector and certification report generator never set `certified=true`, never set `KALSHI_WEATHER_PROBABILITY=AVAILABLE`, and never set `probability_publishable=true`.

A separate V17 ratification path may promote one exact `(settlement_location_code, lane, lead_time_bucket, model_version)` profile only after the immutable R2 certification report passes and the live R2 runtime proves the same model version can score a real eligible contract. `can_execute` remains false permanently in this Weather analytical runtime.
