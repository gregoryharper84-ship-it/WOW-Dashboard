# WOW V17 — ESPN free-core four-sport readback and event relookup repair

**Research / engineering receipt** — 2026-11-11. **Not** an authenticated WOW production runtime Action receipt, a raw-byte preservation receipt, an independent source-license authorization, or model certification. No paid plan, activation, persisted historical dataset, fitted model weight, calibration threshold, terminal gate, or execution change.

## Live source reachability observed externally (not from Render)

Exactly four public ESPN scoreboard URLs were fetched using a live external page-fetch capability on 2026-11-11, with `maxAge=0` on the first fresh calls. Each returned HTTP 200, JSON content type and a valid `events` array. Only the **three nonempty** NBA, NHL and NCAAF responses included observed ESPN provider-local event IDs; the NFL response had zero events and therefore no observed event IDs. No authenticated endpoints or credentials were used. This is source reachability only; endpoint behavior and full-slate completeness are not service-level guarantees.

| Sport | URL date parameter | HTTP | events | Example observed event (provider-local) | Scheduled UTC |
| --- | --- | ---: | ---: | --- | --- |
| NFL | `20261111` | 200 | 0 | Verified empty events array; not provider failure | n/a |
| NBA | `20261111` | 200 | 10 | `401909966` (WSH at IND) | `2026-11-12T00:00Z` |
| NHL | `20261111` | 200 | 6 | `401892690` (TB at BUF) | `2026-11-12T00:00Z` |
| NCAAF | `20261111` | 200 | 3 | `401866461` (BUFF at BALL) | `2026-11-12T00:30Z` |

Provider endpoint template: `https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard?dates=20261111`, using `football/nfl`, `basketball/nba`, `hockey/nhl`, and `football/college-football` respectively. These values are data from external live probes, not persisted source bytes, official league event IDs, verified pregame athlete statuses, model inputs or forecast receipts. The external fetch succeeded whereas this session's direct container DNS resolution and standard web-open attempts could not reach `site.api.espn.com` (DNS resolution / tool rejection). There was no live Render backend canary.

## Reproducible defect and minimal change

The shared existing `v17/scout_secondary_source.py` does ESPN moneyline discovery and secondary read-only event lookups across the seven existing scoreboard sport keys (includes NFL/NBA/NHL/NCAAF). When the caller omitted `_wow_secondary_scoreboard_dates` and supplied only an event start time, the lookup selected the UTC calendar day via `_date_key` rather than the Eastern ESPN scoreboard partition. The 2026-11-11 live response proves that this fails for valid U.S. evening games starting on 2026-11-12 UTC.

Also, the old `_event_matches` accepted any same-team ESPN event even when an explicit `espn-...` ID disagreed, with no start-time comparison. This could attach odds evidence to the wrong game.

Corrective behavior:
- Reconstruct the Eastern scoreboard partition for a UTC kickoff only when the original provider dates are missing. Preserve the original source date list when present; do not replace it.
- Exact ESPN-prefixed IDs remain exact and must agree with each provided home, away, and kickoff field (including partial contexts). Any supplied kickoff must carry an explicit timezone; a naive timestamp is not imputed to UTC.
- Non-ESPN external IDs may match by teams only when both identities and a timezone-aware kickoff within 30 minutes are present. If multiple different event IDs match, fail with `ESPN_EVENT_RELOOKUP_AMBIGUOUS` (HTTP 409), not an arbitrary first match.
- A missing or contradictory event remains `SECONDARY_SOURCE_EVENT_NOT_FOUND`; do not relabel it as `MODEL_UNAVAILABLE`.
- A missing ESPN `odds.lastUpdated` remains an unknown quote timestamp, never backfilled from event kickoff. This permits downstream freshness checks to return `UNAVAILABLE`, rather than misclassifying a future kickoff timestamp as a fresh sportsbook quote.
- Preserve existing market research-only tagging, `prediction_authority=false`, `can_execute=false`, and independent official-ID crosswalks.

## Regression and acceptance targets

Run in existing offline test environment:

```sh
cd artifacts/wow-engine
python -m pytest -q tests/test_v17_823_bounded_espn_provider_acquisition.py tests/test_v17_scout_secondary_source.py test_v17_scout_secondary_window.py
```

Tests cover all four live-observed sport keys with simulated ESPN day partition, exact-ID mismatch, wrong teams/kickoff, duplicate same-team event ambiguity, and valid NFL empty-slate response. Fixtures are synthetic approximations of the observed JSON structure; no ESPN raw historical corpus is stored.

**Required before production COMPLETE:** exact-head CI + independent SIRT and QA/Release checks as applicable; current deployed SHA equality; authenticated backend compact ESPN discovery / sports-specific identity/hydration receipts; official league identity and pregame source freshness; source-specific allowed-use review; numerical specialist and calibration acceptance separately. A health endpoint or working ESPN URL never certifies model capability.

**Status:** SOURCE_HTTP_REACHABILITY_OBSERVED; SOURCE_DATA_BYTES_NOT_ARCHIVED; ENGINEERING_REPAIR_PR_PENDING_REVIEW; MODEL_PROBABILITY_UNCHANGED; can_execute=false.


## Additional full-slate discovery audit — 2026-11-14

A fresh external ESPN `football/college-football/scoreboard?dates=20261114&limit=1000` probe returned HTTP 200 with exactly **25** events and no `total`/`count` field. `groups=80&limit=1000` returned the same 25 event IDs; `offset=25` and `page=2` yielded the same first/last IDs, not new pages. This does **not** establish either a real server-side 25-row hard cap or complete FBS coverage. An actual all-school schedule/authoritative event union is required to prove the full slate.

The existing ESPN source now carries `_wow_provider_completeness_unverified=true` when an NCAAF daily response contains exactly 25 events, has no explicit total, and was requested with a limit above 25. This is a **conservative evidence flag**, not a claimed numeric source quota. Full-slate `secondary_for_request` holds with the existing typed `ESPN_SCOREBOARD_COMPLETENESS_UNVERIFIED`; the authenticated read-only compact diagnostics preserve all 25 source rows, set `status=DISCOVERY_COMPLETENESS_UNVERIFIED`, and `coverage_complete=false`. Verified short/empty boards, explicit total counts, and all other sports retain their former behavior. The heuristic must be challenged against official NCAAF fixtures and future ESPN schema changes; it cannot substitute for verified completeness.

The Sunday `20261115` NFL endpoint also returned HTTP 200 with 12 scheduled events. This provided a non-empty NFL event-shape check in addition to the verified empty Wednesday response.

Regression additions: `tests/test_v17_823_bounded_espn_provider_acquisition.py::test_ncaaf_twenty_five_without_total_cannot_certify_complete_slate` and `::test_ncaaf_small_or_explicit_total_board_not_marked_cap_25`. Do not infer source permits/rights from endpoint reachability and do not promote these research events to an official schedule or prediction.


## Read-only pregame hydration endpoint probes (2026-11-11)

The following endpoints were probed externally with fresh requests and returned HTTP 200 JSON; this confirms only access and top-level response structure. No data retention, authenticated source attestation, production WOW hydration receipt, fitted training or model promotion occurred.

| Sport | Source endpoint | Observed top-level structure | Valid role / limit |
| --- | --- | --- | --- |
| NFL | `https://site.api.espn.com/apis/common/v3/sports/football/nfl/athletes/4241457` | `athlete`, `season`, `league`, `playerSwitcher`; athlete id `4241457` | Player identity research only; does not alone prove today's injury/QB status |
| NBA | `https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries` | `timestamp`, `status`, `season`, `injuries`; array of 29 top-level entries | Injury candidate feed, no guarantee of official or exhaustive player availability |
| NHL | `https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/summary?event=401892690` | `header`, `goalies`, `injuries`, `gameInfo`, `boxscore`; scheduled ESPN event `401892690` | Pregame context research only; goalie structure does NOT establish confirmed starter without independent evidence |
| NCAAF | `https://site.api.espn.com/apis/site/v2/sports/football/college-football/summary?event=401866461` | `header`, `gameInfo`, `boxscore`, `leaders`; scheduled ESPN event `401866461` | Event summary context only; no asserted comprehensive injury evidence or CFBD official ID |

These are provider-local ESPN IDs; preserve exact sport/player/event mapping and source timestamps. Reuse existing `nfl_prop_auto_hydration`, `basketball_event_hydration_runtime`, `nhl_event_identity`, `ncaaf_event_identity`, and sport-specific pregame model gates. A follow-up live backend canary must prove an ESPN observation actually reaches a given accepted hydration field without importing unavailable or future information. This PR does not add alternate collectors or bind ESPN `goalies` to a confirmed lineup automatically.

The Render public `/v17/discovery/espn-compact` endpoint returned HTTP 401 with `Missing or malformed Authorization header` when probed without credentials. This **preserves** an authenticated boundary, but no authorized production readback was available from that request; do not bypass authentication. Last observed Render `live` deployment at audit: `6a6c4b1f105dea14fc6d08bb31a247ca74e94491`, predating this PR.


## Independent CodeRabbit challenge — corrections on 2026-11-11

Three independently surfaced issues on PR #2612 are addressed:
1. **Receipt precision:** the NFL empty-slate response did not supply an observed event ID; the four-response statement above now distinguishes all valid arrays from the three with observed IDs.
2. **Eastern midnight cross-partition match:** when original ESPN lookup dates are absent, a timezone-aware kickoff's permitted ±30-minute identity tolerance is converted into both Eastern-date partitions, if the range crosses midnight. Saved provider dates are never guessed or broadened; a single Eastern date remains one request.
3. **No uniqueness from partial boards:** the external-ID alias path can infer a unique team/time match only if *every* searched scoreboard has explicit non-truncation and no completeness-unverified flag. Source truncation returns `ESPN_SCOREBOARD_PROVIDER_LIMIT_REACHED`; any missing or unverified completeness returns `ESPN_SCOREBOARD_COMPLETENESS_UNVERIFIED`. Exact ESPN-prefixed provider IDs remain exact-ID lookups, not cross-provider uniqueness proofs.

Regression tests cover both ±20-minute Eastern-midnight directions, explicit ESPN ID on an incomplete board, external alias hold on truncated, 25-row/no-total and unknown-completeness boards, and mixed-completeness adjacent partitions. These changes neither certify canonical official event IDs nor change live fitted model, quotation freshness rights, or terminal authority. Exact-head hosted CI and non-author acceptance are required again.
