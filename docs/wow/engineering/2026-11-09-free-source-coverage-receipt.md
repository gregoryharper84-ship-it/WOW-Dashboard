# Free sports data / research tooling — source coverage receipt (2026-11-09)

Disposition: **AUDIT MERGED** via PR #2541 (2026-11-09) + **BLOCKED_WITH_EXACT_REASON** (live/legal verification).
Invariants untouched: can_execute=false, DRY_RUN_ONLY, no probability/artifact/calibrator promotion, no secrets, no paid services.

## 2026-11-10 primary-public-text follow-up (not training authorization)

Verification boundary: the linked publisher-controlled web pages were read through an indexed web renderer on 2026-11-10. Raw originating response bytes, their SHA-256 hashes, the live API payload/schema, and source-specific contracts were **not** captured. These are **PRIMARY_PUBLIC_TEXT_OBSERVED / RAW_HASH_ABSENT** observations, *not* completed `license_receipts` or `live_receipts`; do not convert them to readiness booleans. Shell DNS egress remains unavailable for direct byte-level capture. No credentials, spending, fitting or execution occurred.

| Source | Primary publisher-controlled reference | Verified finding | Disposition for WOW fitted training |
| --- | --- | --- | --- |
| NHL first-party web/API | https://www.nhl.com/info/terms-of-service (last updated 2025-10-29; sections 1, 7) | NHL's publicly linked service terms limit use to personal, informational, non-commercial purposes and require separate authorization for broader reuse. Whether a particular API has distinct applicable terms remains unverified. | **HOLD**: no affirmative commercial fitted-training grant; retain `RIGHTS_UNDOCUMENTED_FIRST_PARTY`, seek written authorization / independent rights ruling. |
| MLB Stats/API | https://www.mlb.com/official-information/terms-of-use (section 1) | MLB Digital Properties terms limit use to personal non-commercial use absent permission and prohibit automated collection scripts. No separate statsapi-specific agreement was established. | **HOLD**: catalog presence and JSON accessibility are not permission to collect or fit. Review permitted acquisition path and written use rights before training. |
| nflverse historical releases | https://github.com/nflverse (Licensing); https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md ; https://github.com/nflverse/nflverse-data/releases | Publisher explicitly says most released datasets are CC-BY-4.0, while FTN datasets are CC-BY-SA-4.0 as of October 2026; dataset-specific exceptions and upstream provider attribution may apply. Its license explicitly disclaims warranty about third-party rights. This distinguishes data licensing from the Python reader's software license. | **REVIEW PER DATASET/RELEASE**: possible open-licensed avenue, not a blanket entitlement. Check specific files, third-party rights, original provider and share-alike implications; registry is still unclassified. |
| NCEI / NOAA climate history | https://www.ncei.noaa.gov/data-submission ; https://www.ncei.noaa.gov/cdo-web/webservices/v2 ; https://www.ncei.noaa.gov/support/access-data-service-api-user-documentation | NOAA federal data are generally open/public domain unless exempt; NCEI also archives third-party submissions subject to other licenses. CDO v2 specifies an access token and rate limits of 5 requests/second and 10,000/day; the distinct NCEI Access Data Service v1 documents its own endpoint. Neither endpoint was queried in this review. | **DATASET-LEVEL RIGHTS + POINT-IN-TIME HOLD**: verify the selected archive's provenance/license, exact endpoint/quotas, source timestamps, and specialist's pregame-weather need before training. Historical *actuals* are not automatically pregame forecasts. |

Impact: **0/13 sports have newly cleared legal, reproducible training evidence**. Both source-rights and model source-review holds remain in force. A claimed live or license receipt ID must never override a registry gap or undocumented first-party rights; see regression `test_claimed_receipt_ids_never_authorize_undocumented_or_unclassified_training`. The existing `ready_for_candidate_training=True` on `NHL_PUBLIC_WEB_API` and `UFCSTATS_PUBLIC` is a registry-level candidate label, **not** a commercial rights determination or permission to publish a fitted probability. Changing that protected source gate requires separate governed review and exact-head independent acceptance.

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

## Addendum 2026-11-10 — rights receipts (approved WebFetch/WebSearch)
Data: `v17/source_rights_receipts_2026-11-10.json`, loaded via `load_rights_receipts()`. Evidence graded; only RAW_BYTES + PERMITS_TRAINING clears the license-receipt blocker (none do). Prior-session findings (SDV, CFBD, NWS, Open-Meteo) carried with their recorded 16-char hash prefixes; see `claude/2026-11-09-source-rights-and-nhl-crosswalk-receipts.md`.

| Source | Finding (this session) | Effect |
|---|---|---|
| NHL_PUBLIC_WEB_API | schedule endpoint returns JSON (via WebFetch, truncated); no last-updated field, only per-game `startTimeUTC`; nhl.com terms snippet: non-commercial personal use | `TERMS_RESTRICT_NONCOMMERCIAL`, `PUBLICATION_TIMESTAMP_ABSENT` |
| MLB_STATS_API | MLBAM copyright notice; non-commercial per secondary sources; primary notice NOT fetched | `TERMS_RESTRICT_NONCOMMERCIAL` (secondary evidence) |
| NFLVERSE | repo page links CC-BY-4.0 but states no scope/attribution/exceptions; no weather fields on page | `LICENSE_RECEIPT_NOT_PROBATIVE` |
| NOAA_NCEI | fetch failed, nothing retrieved | unmatched receipt (not in registry/catalog) |

Still NOT verified: raw-byte license text for nflverse/NHL/MLB/NCEI; live schema/coverage/quotas; archived-forecast pregame timestamps. Shell egress remains blocked; WebFetch output is summarized, not raw.

Decision needed (owner/SIRT, not engineering): NHL and MLB terms read as personal non-commercial; Decision Edge Lab is public-facing. Registry still reports NHL/UFCStats `ready_for_candidate_training=True` (existing tests assert it). Left unchanged; recommend an explicit rights ruling before any fitted NHL/MLB training uses these feeds.

Correction/overlap note (2026-11-10): the addendum above was originally pushed to #2541 after it merged (6ad19909) and so was not on `main`; this PR ports it. Evidence-grade caveat: the MLB receipt here is SECONDARY-grade. The engineering team reports (#2566 / incident #2020) primary-source MLB and NHL terms restricting commercial use and automated collection, nflverse CC-BY-4.0 vs FTN CC-BY-SA-4.0 split, and NOAA CDO v2 token requirement; those are not re-verified here. Where they conflict with this receipt, the primary-source finding governs and the hold on fitted NHL/MLB training stands.

### Addendum 2026-11-10b — nflverse dataset split, NCEI docs
- nflverse (publisher org page, search-snippet grade): most releases CC-BY-4.0 (commercial use allowed with attribution); FTN datasets (charting; participation from 2023) CC-BY-SA-4.0; dataset-specific licenses are marked per release. Upstream NFL-owner terms are separate. Repo policy `nflverse_dataset_policy_v1.json` already excludes participation/ftn_charting; new test guards that. Dataset-level candidates (schedules, player_stats, players, play_by_play) are consistent with CC-BY, but each release page notice still needs a raw-text receipt. Registry entry for NFLVERSE NOT added (would flip candidate-readiness: owner/WOW decision).
- NOAA NCEI Access Data Service docs (summary grade): HTTPS GET, no token or rate limit stated, not all datasets available, no license/citation terms on page, possible access delays. Dataset-level (daily-summaries/GHCND) availability and terms remain unverified. CDO v2 (token-gated) is a different service.

## Reconciliation note 2026-11-10 (merges #2566 and #2567)

This document combines two sections written independently against the same `main`. They differ in evidence grade, not in disposition: both keep every source on hold, and neither authorizes training.

- Where the "primary-public-text follow-up" table and the "rights receipts" addendum describe the same source (NHL, MLB, nflverse, NCEI), the primary-publisher reading in the first table is the higher evidence grade. The addendum's MLB row (secondary-grade) and NCEI row ("fetch failed") are earlier, lower-grade observations and are superseded in grade only; the machine-readable receipts in `source_rights_receipts_2026-11-10.json` keep their own grades and are unchanged.
- Neither section establishes raw bytes with SHA-256, live schema or an independent rights determination. Result unchanged: 0/13 sports cleared; the license-receipt blocker stays in force.
- The "Decision needed (owner/SIRT)" item on non-commercial terms remains open and is not an engineering decision.

### Evidence-integrity hardening 2026-11-10 (review findings on #2574)
- `load_rights_receipts` now requires an explicit `receipts` list (missing/null/non-list is `SOURCE_RIGHTS_RECEIPTS_COLLECTION_MISSING`, never an empty audit), rejects non-object entries, blank/non-string fields, unknown evidence grades, invalid dates and any excerpt whose SHA-256 does not match `excerpt_sha256`.
- The license-receipt blocker is cleared only by `receipt_is_probative`: `PERMITS_TRAINING` + `RAW_BYTES` + digest re-verified + https URL + `retrieved_at` not future and within 30 days. `build_source_matrix` re-verifies receipts handed in directly, so the loader is not the only gate. Blank receipt ids no longer clear live/license blockers.
- Residual, not closed here: a matching digest proves the stored excerpt is intact, not that it was captured from the cited URL. Authentic capture needs a server-owned capture path. All legal/upstream rights holds are unchanged and nothing authorizes training.
