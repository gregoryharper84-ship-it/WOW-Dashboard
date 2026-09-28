# WOW V17 Free Resource Integration V1

Status: engineering implementation slice for issue #758.  This document does not
promote any sporting model or change production probability behavior.

## Governance rule

Free/public resources enter WOW first as evidence, discovery, or research data.
They do **not** become sporting probability authority merely because they are
available.  Numerical use in a fitted specialist is Class C and must earn
promotion through chronological replay, counterexample review, untouched/forward
validation, calibration/regression checks, and governed review.

Global invariants remain:

- `runtime_generation=V17_ACTIVE`
- `terminal_authority=V17_TERMINAL_REDUCER`
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`
- `can_execute=false`

## Already integrated

### nflverse

Current WOW code already uses nflverse for NFL historical/player-stat hydration,
training adapters, forward settlement support, and the memory-safe interactive
NFL prop cache.  Do not add a duplicate connector.  Preserve source bytes,
version/digest provenance, and the existing canonical NFL identity boundary.

Credential required: **none** for the public release assets currently consumed by
WOW.

### National Weather Service / NOAA + Open-Meteo

WOW already has NWS (`api.weather.gov`) adapters and Open-Meteo fallback/model-
comparison support in the weather stack.  Weather remains context/evidence unless
an exact sport model was historically trained and certified to consume the
feature numerically.

Credential required for NWS: **none**.  Use an identifying User-Agent and bounded
request cadence.

## New implementation slice: football-data.org

`v17/football_data_org_discovery.py` adds a bounded, research-only soccer schedule
adapter for competitions included in the provider's free tier and represented in
WOW's current soccer registry:

| WOW league | football-data.org code |
| --- | --- |
| EPL | PL |
| FRA1 | FL1 |
| GER1 | BL1 |
| ESP1 | PD |
| ITA1 | SA |
| UEFACHAMP | CL |
| FIFA | WC |
| UEFAEURO | EC |

Provider match IDs are aliases only.  They are never promoted to
`official_event_id`; a separate governed identity resolver must bind competition,
teams and kickoff before scoring.

### One-time operator setup

1. Create a free football-data.org account and obtain its API token.
2. Add the token to the secure Render environment for the governed engine using
   **one** supported variable name, preferably:
   `WOW_FOOTBALL_DATA_API_TOKEN`.
3. Never put the token in Git, issue comments, logs, test fixtures, or Custom GPT
   instructions.
4. Redeploy only after the integration PR is reviewed/merged and the runtime
   wiring is enabled.

The adapter also recognizes `FOOTBALL_DATA_API_TOKEN` and
`FOOTBALL_DATA_TOKEN` for compatibility, but the WOW-prefixed name is preferred.

## Next research-only additions

### MAPIE

Use MAPIE only in an offline/Class-C uncertainty challenger.  Initial targets:
MLB 1IP directional lower-bound conservatism and NCAAF publication-bound
research.  Do not replace the current calibrated bound in production without
chronological conformalization + untouched forward validation and independent
review.

No API credential is required.  Pin the package version in the research
requirements/lockfile rather than adding it to the latency-sensitive production
web process by default.

### Evidently

Use Evidently in the engineering/model-health auditor for input missingness,
feature/prediction drift, and data-quality checks.  It may create observability
findings; it does not alter a sporting probability, terminal result, publication
status, or rank eligibility.

No API credential is required for the open-source Python library.  Prefer the
resident auditor/offline analysis environment instead of the production scorer.

## Promotion sequence for any new free feature

`SOURCE_REVIEW -> IMMUTABLE_CAPTURE -> FEATURE_HYPOTHESIS -> CHALLENGER -> CHRONOLOGICAL_REPLAY -> COUNTEREXAMPLE_REVIEW -> UNTOUCHED/FORWARD_VALIDATION -> CALIBRATION/REGRESSION -> GOVERNED_REVIEW -> OPTIONAL_PRODUCTION_PROMOTION`

A source can improve coverage immediately as discovery/evidence while still being
barred from changing model mathematics.
