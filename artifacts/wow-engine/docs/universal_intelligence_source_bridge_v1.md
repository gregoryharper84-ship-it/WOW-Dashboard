# V17 source-entitlement to research shadow bridge — V1

**Scope:** Offline, fixture-tested Class A/B source-evidence reconciliation, not production feature ingestion or model scoring. Owner epic #1252 and previous source-only merges #2059/#2019. `can_execute=false`.

## Source route

`v17/universal_intelligence_licensed_source_bridge.py::compile_registry_source_shadow_bridge` accepts pre-existing, caller-supplied observations, with a bounded batch and an event/cutoff. It does **not** call external sites, persist information, or read credentials.

The bridge consults the existing `v17/model_source_entitlements.py` registry. Its initial origin pins are limited to the SportsDataverse dataset GitHub project (`SPORTSDATAVERSE_ESPN`, `SPORTSDATAVERSE_WNBA_STATS`) and OpenFootball (`OPENFOOTBALL_CC0`). Only a supported sport/source pair and the registry's candidate-readiness result can enter the research snapshot. Other sources are held with typed per-row reasons. Registry `TRAINING_OPEN_LICENSED` is *not* independent legal verification of any uploaded observation, derived work, access method, retention, or intended commercial use.

PropsMadness is NOT an approved source route. No source is scraped, no login/private endpoint used, no bookmaker prices converted to sporting features, and no right to historical vendor data is inferred. Its four-sport parser remains research-only.

## Exact audit behavior

- Requires nonempty claimed event/player/team identity, an allowlisted source project origin, official source record ID, a syntactically valid payload SHA-256 claim, and chronologically ordered observation/first-availability/retrieval times before the event cutoff.
- Records each accepted and held row; one malformed or unsupported row does not halt independently valid research. Duplicate source-record theses are not double-counted.
- Calls the existing 13-sport `compile_intelligence_research_snapshot` and `prepare_shadow_feature_handoff` without modifying their contract. Qualitative context hypotheses cannot become numeric features.
- Records registry-use and license metadata, but explicitly sets `payload_independently_verified=false`, `source_entitlement_independently_attested=false`, `official_event_identity_attested=false`, `historical_training_authorized_by_bridge=false`, `model_consumption_receipts=[]`, `probability_publishable=false`, and `can_execute=false`.

## Boundary to real measurements

The next distinct task must supply independently permitted, authentic *point-in-time* source snapshots, original source bytes/hash, verified official canonical identity and game status, exact time-of-first-publication and timestamped immutable baseline/challenger prediction IDs. SIRT separately audits those receipts. Use existing `universal_intelligence_paired_replay.py` for **descriptive**, exact-line/side/period holdout Brier/log-loss comparison only. Do not label its input as authenticated or a resulting win rate as model calibration. Fitted specialist calibration/coverage and the terminal reducer are separate certified operations. No automatic release of fitted model features or source collector workflows follows from this PR.

## Acceptance

Tests: `python -m pytest -q tests/test_universal_intelligence_licensed_source_bridge.py` from `artifacts/wow-engine`. All existing required checks remain in force; no new `.github` workflow, database migration, schedule or service deployment configuration is added.

**State after source merge only:** `OFFLINE_SOURCE_CONTRACT_BUILT / AUTHENTIC_SOURCE_OBSERVATIONS_NOT_VERIFIED / HISTORICAL_REPLAY_NOT_EXECUTED / MODEL_CONSUMPTION_UNVERIFIED`.
