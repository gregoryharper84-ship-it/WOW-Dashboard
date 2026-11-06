# WOW V17 — Authorized multi-source player-prop acquisition upgrade

**Objective:** eliminate routine manual PrizePicks board extraction while preserving exact
market identities, verified sporting data, fitted sport/stat specialists and V17
terminal governance. Owner: WOW Betting Intelligence (product acceptance);
Engineering/AMRT (implementation); SIRT + independent verification (challenge
and acceptance). Status: **WORKING_NOT_COMPLETE** until live source/Action
receipts and independent acceptance exist.

## First incremental implementation

- Source: independently operated `prizepicksapi.com` documented
  `GET /api/v1/projections` (not an official PrizePicks API).
- Code: `v17/authorized_prop_board_discovery.py`, `v17/authorized_prop_board_route.py`.
- Server route: `GET /v17/prop-board/authorized-discovery?sport=NBA&max_pages=4`,
  installed from `install_daily_snapshot_route` only when enabled.
- Supported research routing: NBA, NFL, NHL, NCAAF (vendor `CFB`), NCAAB,
  WNBA, MLB, Soccer, Tennis. These are vendor-query mappings, **not** claims of
  verified live sport coverage or model capability.
- Auth: inherits the existing FastAPI V17 dependency. No provider credential is
  exposed through route responses.
- Access prerequisites on the backend:
  - `WOW_V17_AUTHORIZED_PROP_BOARD_ROUTE_ENABLED=1` to install the route;
  - `WOW_V17_PROP_FEED_ACCOUNT_USE_AUTHORIZED=1` only after an operator
    independently confirms the third-party account and appropriate permitted use;
  - `WOW_V17_PROP_FEED_API_KEY` as a backend secret, never a Custom GPT prompt.
- All three prerequisites are absent/off by default. No scheduler or training
  corpus is activated by this change.

## Source and licensing boundaries

PropsMadness has useful minutes, role, opponent, matchup and game-log research.
Its help center says **no public API**; its terms require prior written consent
to reproduce/distribute site content. Reuse existing offline/public research
adapters; do not implement covert authenticated scraping or persistent vendor
copying. Seek written permission for automated reuse, retention and training
before enabling that collection.

PrizePicks itself restricts unauthorized automated access. This integration
contacts **only** a separately operated API through its documented endpoint,
using an operator-provided key. The third-party's own collection rights and
availability are **not certified** by an API response or subscription. Do not
misidentify third-party aliases as official league IDs or display quoted lines
as independently confirmed current offers.

Other sources: review the existing The Odds API, TheRundown and SportsGameOdds
integration capabilities for licensed exact sportsbook markets. Odds from
another sportsbook are not exact PrizePicks offers. Official league/game
sources remain the authority for sporting identity, status and settlement.
No odds, hit rates, Scout signals or PropsMadness grades become fitted probability.

Reference pages, inspected 2026-11-06:
- https://propsmadness.com/help/en/articles/11105995-how-do-you-get-your-data
- https://propsmadness.com/terms-and-conditions
- https://www.prizepicks.com/help-center/packs-terms-of-service
- https://prizepicksapi.com/docs
- https://prizepicksapi.com/pricing
- https://sportsgameodds.com/player-props-odds-api

Third-party free quota figures are currently inconsistent across vendor pages.
Check the account's actual quota, rate limits, data rights and API health before
any production cadence or purchasing decision. Do not incur new paid usage
without the user's authorization.

## Discovery receipt contract

Every read produces a bounded, read-only receipt:
- the requested sport, pages attempted, source rows received;
- unique base props and available directional candidates;
- exact provider-displayed line, stat, period, event start and timestamp;
- per-side `MORE`/`LESS` availability and standard/goblin/demon tier;
- per-source-row typed rejection (demo, missing direction, stale, started,
  invalid/unknown identity, incorrect league, unsupported offer);
- pagination cursor-cycle/duplicate/budget failures and a coverage-complete flag;
- `official_event_id=None`, `official_player_id=None`, `probability_publishable=false`,
  `rank_eligible=false`, `can_execute=false` until independent downstream proof.

A successful HTTP request does not prove full-sport board coverage. A row lacking
a verified official identity is still a valid *research lead*; it is NOT a valid
`/score-pick-request` row yet. Neither nonstandard payouts nor an inferred
PrizePicks LESS offer is synthesized.

## Next governed build stages — distinct owner acceptance targets

| ID | Scope | Owner | Acceptance |
| --- | --- | --- | --- |
| PROP-A1 | Optional source adapter and secured read-only route | Engineering | exact-head CI + independent route/authorization/negative tests; branch implementation is not deployment |
| PROP-A2 | Production source entitlement, freshness, quota and full-board paging | Data Integration | permitted source proof; real non-demo source receipt; complete vs truncated coverage independently checked |
| PROP-A3 | Join research leads to official pregame player/event/market identities and current exact offer | Data Integration | per-row official IDs and status; unreconciled offers retain a typed blocker |
| PROP-A4 | Feed verified candidate pool into existing Daily/Scout union and `/score-pick-request` in governed batches | Engineering | all rows exactly once; no hidden sampling, no scorer fallback, Action receipts |
| PROP-B1 | NBA points/rebounds/assists/3PM fitted prop lane | Model Improvement | exact-line fitted specialist + replay + forward calibration/bounds + independent certification |
| PROP-B2 | NHL shots/saves/points supported lanes | Model Improvement | sport/stat-specific fitted models, goalie/workload/settlement and forward validation |
| PROP-B3 | NFL receptions/yardage/TD and NCAAF/NCAAB props | Model Improvement | exact route/line cert and forward cohorts; preserve existing NFL certified lanes |
| PROP-B4 | Final refresh, sportsbook price/offer audit, payout/void and correlation control | Shared core | model probability distinct from money/edge; terminal reducer receipt |
| PROP-C1 | Daily scheduled discovery-to-scoring pipeline + user leaderboard | AMRT/Engineering | successful real full-slate pregame run, stable receipt IDs, current line and zero silent omissions |
| PROP-C2 | Post-settlement immutable grading and learning loop | Model Improvement/SIRT | n, Brier/log loss, calibration bias, lower-bound reliability; chrono replay and regression |

Prioritize currently active **NBA, NFL, NHL and college football/basketball**
over off-season MLB model expansion. Keep ML/team winners separate in LLP and
player/scalar props in WOW_PROP_LANE.

## Independent release criteria

1. Test `v17/test_authorized_prop_board_discovery.py` at the **exact PR head**.
2. Run broader V17 Daily/properties tests; show any changed-file regressions.
3. Confirm the route is absent while disabled, responds with typed
   `PROP_FEED_ACCOUNT_USE_NOT_AUTHORIZED` or `PROP_FEED_CREDENTIAL_MISSING`
   when not usable, and never leaks API secrets.
4. Independently verify real permitted vendor data and pagination with source
   timestamps and capture receipts; absence of keys is **BLOCKED**, not
   `MODEL_UNAVAILABLE`.
5. Only after rights and source quality are verified, wire source leads through
   official identity and fitted scoring. Do not infer that an adapter PR
   completed that work.
6. Verify deploy SHA, live authenticated endpoint, required Action/editor schema
   sync, score/receipt identity, and Daily reconciliation before declaring
   **COMPLETE**.
7. Rollback by disabling the route flag (or reverting its install), preserving
   prior model lanes, Daily reads and `can_execute=false`.

No wagering or order execution is enabled.
