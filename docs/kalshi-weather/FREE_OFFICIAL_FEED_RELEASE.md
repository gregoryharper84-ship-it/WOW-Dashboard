# Free official feed resilience

## Approved scope
Use official free feeds for reliable shadow collection. NWS is the required hourly forecast. Open-Meteo is optional corroboration, enabled only with `WOW_KALSHI_WEATHER_OPEN_METEO_ENABLED=1` when usage is eligible. Default is disabled. Existing AWC, IEM and tokenless NCEI adapters remain available for exact-station observations and historical research; they are not substitute settlement authorities for composite hourly index contracts. Direct NBM/HRRR integration remains subsequent work.

## Implementation and verification
1. Acquire NWS point metadata and exact-target hourly forecast through the existing read-only client; validate the returned forecast host/path.
2. When explicitly enabled, acquire Open-Meteo. On HTTP acquisition failure, preserve NWS and record a typed warning. Never synthesize secondary values.
3. Persist available source snapshots and shadow predictions. A single provider remains incomplete and cannot load a certified calibration profile or publish probability. Preserve frozen contract settlement identity and `can_execute=false`.
4. Verify simulated 429, primary failure, untrusted URL and actual runtime persistence without secondary evidence, plus the full weather/API regression suite.
5. Protected merge and deploy the isolated weather service. The separate authenticated scheduler repair (#2393) must also be merged/configured before scheduled collection can be verified. Health GET is not collection.

## Release acceptance
Record the deployed SHA; configure matching private collector credentials without printing them; verify authenticated POST and a new immutable prediction beyond 21 baseline rows (latest decision 2026-11-01T14:02:21Z). Verify subsequent unattended capture, source freshness and memory. No calibration promotion or actionable recommendation is authorized by this engineering change.

## Validation
Weather package and isolated API: 338 passed, 6 subtests passed. Fourteen new regressions include two runtime tests that failed on the prior unconditional Open-Meteo dependency and passed after the repair. Full repository regression results must be assessed separately; local package success is not protected CI acceptance or proof of deployment.

Review hardening: NWS requests refuse redirects, malformed secondary target/unit data degrades to NWS-only evidence, and invalid NWS metadata retains a typed diagnostic.
