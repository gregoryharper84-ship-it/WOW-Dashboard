# V17 Evidence and Diagnostics

Status: **ACTIVE**. This package adds diagnostics and source coverage, not another model layer.

## Controls

- `MODEL_DISAGREEMENT_MONITOR` compares the model with opener, decision-time consensus, and close. Persistent gaps create review work; they never suppress or alter a probability automatically.
- `TEMPORAL_FEATURE_PROVENANCE` records publication, first-knowable, capture, and use times for every model input. Any future-known input fails closed.
- `HYPOTHESIS_CHANGE_LEDGER` requires sporting rationale, affected feature, expected direction, non-overlapping train/untouched holdout windows, and before/after calibration. It cannot auto-promote a model.

## Connector package

The initial evidence adapters cover NWS stadium forecasts, NHL schedules/rosters/play-by-play, NBA batch statistics/opportunity data, nflverse participation/snap counts, and credit-capped The Odds API snapshots.

Every capture records source identity and access classification, request and source publication times, event/player mapping, schema fingerprint, freshness and completeness, allowed model/evidence fields, fallbacks, fail-closed behavior, and a content-addressed immutable raw snapshot.

New connector fields are evidence-only by default. A field becomes model-authoritative only through an explicit hypothesis-ledger entry, prospective training and untouched holdout evaluation, calibration evidence, and separate certification. Connector availability never creates execution authority.
