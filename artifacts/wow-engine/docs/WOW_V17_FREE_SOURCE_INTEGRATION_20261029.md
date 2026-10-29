# WOW V17 unified free-source catalog — source-only integration

**Current implementation:** `v17/free_source_catalog.py` and `tests/test_free_source_catalog.py`. Read-only, pure metadata; does not request external data, install a scraper, access a login, write production history, or invoke a betting Action. `can_execute=false`. This is a Class A/B research-inventory improvement, not model certification or an external-source license grant.

## What already exists (preserve, do not recreate)

- NFL: nflverse historical adapters and the governed NFL specialist.
- NBA/NCAAB/WNBA: SportsDataverse/hoopR-derived release assets, existing basketball hydrators and the frozen WNBA snapshot.
- NCAAF: existing credentialed CFBD research adapter.
- MLB: official MLB Stats API plus existing Statcast/pybaseball fallback work (some reference-site endpoints have returned 403).
- Exact market/value: existing The Odds API multi-key quota management and TheRundown failover. Neither supplies governed sporting probability.
- **PropsMadness: preserve the current NBA/NFL/MLB/WNBA offline/public Explore parser, shared research handoff, identity crosswalk, and signed-in account-view parsers.** The NFL authenticated view was observed, but the other three sport layouts remain synthetic-contract-only. This is currently *research/discovery evidence*, not a certified historical training feed.

## Why PropsMadness requires an explicit boundary

The signed-in NFL chart can compare previous games against **today's** selected prop threshold. It does **not** establish historical closing lines, contemporaneous bookmaker prices, or independently verified historical game identities. `PROPSMADNESS_ACCOUNT_VIEW` is therefore exposed to the `HISTORICAL_RESEARCH` inventory but **never** promoted to `HISTORY` fitted-model training or `MARKET` exact-line authority. Current login access is not consent to unattended collection, historical retention, redistribution, or model-training reuse; those rights remain unverified. No automatic browser, proxy, credential handling, collection scheduler, or database writes are added.

## Best next acquisition order, reusing the existing integrations

1. NFL: audit nflverse datasets against the existing NFL input schema, timestamped snapshots, official injury/QB refresh and immutable prediction records.
2. NBA and NCAAF: audit SportsDataverse + CFBD feature availability, chronology, and rights/credential state without creating competing collectors.
3. MLB and WNBA: reuse official Stats/Statcast and the existing WNBA SHA-frozen snapshot approach.
4. PropsMadness: compare sanitized NFL account-view outcomes to official NFL outcomes for identity/stat verification; separately verify NBA/MLB/WNBA real-page layouts. Require a vendor reuse grant before recurring extraction, storage, or training.
5. OddsPapi: an **unverified experiment** for exact historical bookmaker/market/line/period/time mapping, data depth, quotas and contractual rights. Do not replace The Odds API until independently validated.

## Contracts

`compile_free_source_plan(sport, purpose)` accepts `HISTORY`, `STATUS`, `DISCOVERY`, `HISTORICAL_RESEARCH`, or `MARKET`. It lists candidate routes and registry training *preflight* when available. **All routes return live health NOT_PROBED and no entitlement/feature/probability authority.** A registry training candidate is not a certified per-event input. No model weights, probabilities, calibration, governance thresholds, or terminal decisions change. `V17_TERMINAL_REDUCER` stays sole global terminal authority.

Required evidence before a source may become a fitted challenger input: independent rights review, genuine source snapshot and SHA-256, official event/player identity, point-in-time first-availability and retrieval, anti-leakage chronological replay, holdout Brier/log-loss/lower-bound comparison, independent validation and existing protected release. Market-price data is evaluated downstream and remains distinct from sporting probability.

Run: `cd artifacts/wow-engine && python -m pytest -q tests/test_free_source_catalog.py`.

**Completion state:** SOURCE_INVENTORY_ADDED; AUTHENTIC_SOURCE_COLLECTION_NOT_CHANGED; PROPSMADNESS_TRAINING_NOT_AUTHORIZED; ODDSPAPI_AUTHENTIC_COVERAGE_NOT_VERIFIED; MODEL_PROBABILITY_UNCHANGED.
