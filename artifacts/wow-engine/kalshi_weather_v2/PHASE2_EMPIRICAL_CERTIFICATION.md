# Kalshi Weather V2 — Phase 2 empirical certification

Structural V17 compliance is already live-verified. Phase 2 is exclusively about accumulating and validating real pre-settlement weather evidence before probability capability promotion.

## Eight-step certification workflow

1. **Governed hourly shadow cohort collector** — discover only candidate Climate/temperature series from the public Kalshi API, then require the existing exact hourly contract parser to accept each market before capture.
2. **Explicit target registry** — use explicit Weather Index city identity and a separately labeled forecast reference point. The forecast coordinate is never treated as settlement identity.
3. **Narrow first cohort** — begin with Miami hourly Weather Index contracts. Expand only after the first target is producing clean immutable captures and settlements.
4. **Controlled lead-time buckets** — capture at `H0`, `H1`, `H2`, `H3_5`, `H6_12`, `H13_24`, and `H25_PLUS`, with at most one calibration weather sample per target time/bucket.
5. **Threshold-sibling de-duplication** — multiple Kalshi strikes for the same weather target do not increase calibration sample size. The calibration sample key is city + exact target timestamp + lead-time bucket + model version.
6. **Automatic exact settlement** — after a grace period, grade persisted hourly predictions only against the exact canonical Kalshi Weather Index target point. Missing/incomplete/non-final quality stays unresolved rather than inferred.
7. **Candidate calibration + chronological validation report** — require at least 30 unique residuals for a candidate fit. Once at least 10 later samples are also available, report chronological holdout MAE, holdout bias, and one-sigma coverage. Research candidates remain `certified=false`.
8. **Fail-closed promotion gate** — no code in the cohort collector or calibration report may set `KALSHI_WEATHER_PROBABILITY=AVAILABLE`, `certified=true`, `probability_publishable=true`, or `can_execute=true`. Promotion requires a separate governed acceptance decision after empirical evidence is sufficient.

## Free-source policy during Phase 2

The source registry is defined in `free_public_sources.py` and documented in `FREE_PUBLIC_DATA_INTEGRATION.md`.

P0: Kalshi public API, NWS, Open-Meteo deterministic/ensemble/archive, NOAA/NCEI, and IEM ASOS.

P1: MET Norway corroboration.

Optional: NOAA/NCEP NOMADS direct GRIB model data when direct-source verification adds measurable value beyond the governed JSON model sources.

No secondary source may replace the exact settlement source named in the frozen contract.

## Promotion prerequisites

Before any capability promotion is considered, the calibration report must show enough unique target/bucket samples to fit and validate the relevant source-location/lane/lead-time profile. The acceptance review must define and pass explicit out-of-sample calibration thresholds, confirm no look-ahead leakage, confirm settlement completeness, and verify that the promoted profile is immutable and fitted before any prediction that uses it.

Until then:

- `KALSHI_WEATHER_PROBABILITY=UNAVAILABLE`
- `probability_publishable=false`
- `capability_promotion_allowed=false`
- `can_execute=false`
