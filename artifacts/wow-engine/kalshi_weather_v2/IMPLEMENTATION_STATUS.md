# Kalshi Weather V2 / V17 implementation status

## V17 structural/governance status

**Status: `V17_STRUCTURAL_COMPLIANCE_LIVE_VERIFIED`**

Implemented and live-verified:
- Kalshi Weather is a first-class in-process V17 controlling specialist (`KALSHI_WEATHER_MARKET_EXPERT`), not a parallel global-terminal host.
- `V17_TERMINAL_REDUCER` is the sole global publication authority.
- the local Kalshi Weather terminal governor is audit-only and can never set `rank_eligible`, `probability_publishable`, or `edge_publishable` true.
- the canonical Render/Supabase production entrypoint mounts Weather behind `WOW_KALSHI_WEATHER_V2_ACTIVE=1`.
- V17 Weather governance, analyze, immutable-capture, publication-audit, and settlement routes are defined on that canonical app.
- every declared Weather family routes to the Weather specialist; lanes without a certified end-to-end runtime fail closed instead of substituting generic weather reasoning.
- immutable prediction persistence precedes every V17 probability-publication attempt.
- V17 publication revalidates exact rule/ticker/settlement identity, timezone/window, temporal provenance, probability coherence, certified station/lane/lead-time calibration identity, calibration as-of timing, certification evidence, source snapshot existence, and no market-price substitution.
- market/fee/orderbook holds cannot silently erase a completed independent weather probability.
- V17 edge reconciliation uses immutable market snapshots; raw executable/pre-fee edge may be reported separately, while uncertainty-adjusted ranking remains blocked unless fees/friction are verified.
- `can_execute=false` remains invariant; no order placement/cancel/modify interface exists.

Live production attestation on 2026-09-11:
- protected PR workflows passed on the final implementation revision before merge.
- protected `main` governed-backend workflow passed after merge.
- production merge commit: `1414b878724e6be0b63d37dd6880259db75d0e8b`.
- canonical Render service: `wow-governed-probability-engine` / `srv-da7sa9gu01pc73brt80g`.
- verified live deploy: `dep-dahut9nqj5pc73alc2pg`.
- live startup emitted `WOW_V17_RUNTIME status=ACTIVE global_terminal_authority=V17_TERMINAL_REDUCER can_execute=false`.
- live startup emitted `WOW_KALSHI_WEATHER_V2_RUNTIME status=ACTIVE mode=SHADOW probability_publishable=false can_execute=false`.

## Weather-model/runtime coverage

Implemented end-to-end in shadow form:
- `HOURLY_TEMPERATURE` / Kalshi Weather Index contract semantics
- exact decimal-threshold probability treatment
- NWS + Open-Meteo exact-target forecast fusion
- canonical Kalshi Weather Index settlement acquisition
- immutable prediction/outcome grading

Implemented evidence/model components but not yet an end-to-end certified publication runtime:
- `DAILY_HIGH_TEMPERATURE` evidence fusion using NWS target-local-date hourly maximum, official max-so-far, Open-Meteo model disagreement, contract timezone, and temporal-provenance checks

Declared but intentionally fail-closed until their own governed model/runtime/calibration exists:
- `DAILY_LOW_TEMPERATURE`
- `PRECIPITATION_DAILY`
- `PRECIPITATION_MONTHLY`
- `HURRICANE_TROPICAL`
- `CLIMATE_RECORD`
- `HEATWAVE_EXTREME`
- `SNOW_SKI_RESORT`
- `NATURAL_DISASTER_WEATHER`
- `OTHER_WEATHER`

## Empirical certification gate

`KALSHI_WEATHER_PROBABILITY` must remain `UNAVAILABLE / IMPLEMENTATION_NOT_CERTIFIED / probability_publishable=false` until real immutable shadow evidence supports promotion.

Promotion is not an implementation flag flip. It requires, at minimum:
- persisted pre-decision Weather predictions
- settled outcomes from the exact contract settlement authority
- fitted station/source-location + lane + lead-time calibration profiles
- non-empty certification evidence for each certified profile
- forward shadow acceptance and settlement reconciliation
- capability evidence explicitly ratifying probability publication

No fixed sample-size, Brier-score, or edge threshold is invented here; those acceptance thresholds must be established from the governed calibration/acceptance process rather than guessed.

## Current compliance statement

Kalshi Weather is **structurally V17-compliant and live in production in fail-closed mode**. The remaining empirical certification work is model-readiness work, not a V17 governance exception. With no certified calibration evidence, the V17-compliant behavior is `NO_PLAY_DATA_INSUFFICIENT`, `probability_publishable=false`, and `can_execute=false`; publishing a fabricated or flag-promoted probability would itself violate V17.
