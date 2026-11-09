# Free sports data / research tooling — source coverage receipt (2026-11-09)

Disposition: **PR_CREATED** (audit tooling) + **BLOCKED_WITH_EXACT_REASON** (live/legal verification).
Invariants untouched: can_execute=false, DRY_RUN_ONLY, no probability/artifact/calibrator promotion, no secrets, no paid services.

## What changed
- `v17/source_coverage_audit.py` — read-only matrix derived from the EXISTING `model_source_entitlements` registry and `free_source_catalog`. No competing registry, fetcher, or authority.
- `test_v17_source_coverage_audit.py` — 12 tests: determinism, no-authority, catalog≠authorization, receipts remove only their own blockers, unclassified flagged, URL≠adapter, unknown-sport fail-closed, AST tool-usage scan.
- Registry + adjacent tests: 43 passed. Registry/catalog behaviour unchanged.

## Verified from repo (offline, reproducible: `build_source_matrix(".")`)
- MLflow, DuckDB, Evidently, Optuna: declared in `requirements-research.txt` only; **zero imports anywhere** (AST scan). No isolated research harness exists. Not added: nothing demonstrates a need yet and none could be validated against a real run here.
- nflverse: adapter + dataset policy JSON exist (CC-BY-4.0 claimed in policy; excludes FTN/participation). **No entitlement-registry entry** -> `NOT_IN_ENTITLEMENT_REGISTRY`.
- NHL_PUBLIC_WEB_API, UFCSTATS_PUBLIC: registry says `ready_for_candidate_training=True` with `CANDIDATE_FIRST_PARTY_UNDOCUMENTED` and no license metadata. Existing tests deliberately assert this (candidate-only, certification review required). Left unchanged; now surfaced as `RIGHTS_UNDOCUMENTED_FIRST_PARTY`.
- MLB_STATS_API, PYBASEBALL_STATCAST, THE_ODDS_API, PropsMadness, SDV ESPN CFB box score: catalogued, **unclassified in the registry** (fail-closed).
- ODDSPAPI: URL only, no adapter, unverified.
- CFBD: registry entry present; credential absent in this environment.
- NOAA/NCEI, NWS, Open-Meteo (incl. previous-runs), IEM ASOS: adapters exist only under `kalshi_weather_v2` for Kalshi weather. No NFL/NCAAF/MLB game-weather adapter, and no evidence found that a controlling specialist consumes weather features.
- Across all 13 sports: **0 sources** satisfy legal, reproducible fitted training (every row lacks a live-endpoint receipt and a license-verification receipt; most also lack certification review).

## NOT verified (no receipts exist)
Live endpoint availability, schema, historical coverage, quotas, and license text for every source. Sandbox egress blocks NHL/NOAA/Open-Meteo/CFBD/MLB hosts and GitHub raw; WebFetch approval was not granted. Nothing here claims live monitoring.

## Exact blockers to legal reproducible fitted training
1. License-verification receipt (URL + retrieved date + hash of terms) per source - absent for all.
2. Live endpoint/schema/timestamp receipt per source - absent for all.
3. Certification source review - required for every registry source.
4. Registry classification decision for NFLVERSE, MLB_STATS_API, PYBASEBALL_STATCAST, SDV ESPN CFB box, THE_ODDS_API.
5. NHL/UFCStats rights: undocumented first-party; owner/WOW decision whether candidate-training readiness should stay True.
6. Credentials absent for CFBD, BALLDONTLIE; entitlements unconfirmed for football-data, Live Tennis, Data Golf, Boxing.
7. Weather: need specialist requirement + verified archived-forecast pregame timestamps before any adapter (leakage risk if actuals are used).

## Smallest next action
Run the receipt capture from an environment with allowed egress (or approve WebFetch for the listed hosts), save receipts, pass them into `build_source_matrix(live_receipts=..., license_receipts=...)`.
