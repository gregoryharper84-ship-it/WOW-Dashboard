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

## 2026-11-12 historical-odds source triage — reference only

**Mechanism.** A public scraper's code license is not a license for the
upstream odds database. Cataloguing such a scraper as a production MARKET or
HISTORY source would conflate technical reachability with legal authorization
and verified as-of market evidence. This predicts an apparent new data
dependency with no permitted collection contract, and potential leakage if
historical closing lines are interpreted as quotes available before a pick.

| Candidate | Relevant sports / useful data | Rights and evidence decision | V17 use |
|---|---|---|---|
| [OddsHarvester](https://github.com/jordantete/OddsHarvester) / [OddsPortal terms](https://www.oddsportal.com/terms/) | NFL, NBA, NHL, MLB, soccer, tennis; game-level moneyline/handicap/total and claimed historical quote movement | MIT code only; upstream terms restrict automated scraping, commercial use and substantial database extraction without permission. Odds-history timestamps can be timezone-naive, and historical identity/pagination failures are documented by the project. | REFERENCE_ONLY_NO_ACQUISITION. No browser automation, proxy rotation, collection, storage, training or production wiring. |
| [nflverse data](https://github.com/nflverse/nflverse-data) | NFL game results, historical closing spreads/totals, partial moneylines | Existing NFL historical close-proxy adapter; the close is generally **not** a verified pre-pick-observed quote. Dataset-specific/upstream licensing still needs independent review (see source-rights receipts). | Existing history / research benchmark. Not pregame CLV or a fitted model probability. |
| [Football-Data](https://football-data.co.uk/data) | Soccer results and historical bookmaker opening/closing odds | Free files are expressly intended for private individuals, not commercial or data-training products using automated bots/scrapers/AI. | REFERENCE_ONLY_NO_ACQUISITION; no model training or unattended collection. |
| [The Odds API historical endpoint](https://the-odds-api.com/historical-odds-data/) | Cross-sport timestamped historical markets | Historical endpoints are paid-only; no new spend approved. | Not in the zero-cost acquisition plan; preserve existing current-market fallback only. |
| Existing [ESPN close research PR #1567](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/1567) | Forward-only NBA/NFL/MLB-style market snapshots, depending on observed sport/book fields | No retrospective pregame backfill; test exact capture/prediction ordering and event identity before CLV claims. PR #1581 owns late-capture/lookahead repair. | Reuse and reconcile the existing measurement lane, not a second collector. |

**Source priority:** (1) review the exact nflverse release/dataset rights
and continue its existing close-proxy benchmark; (2) verify forward captured
quote provenance and source terms in the existing measurement lane;
(3) seek written source permission only if a legally usable multi-sport
historical archive is demonstrably needed. No purchasing or source access
is authorized by this table.

**Required acceptance for any historical market dataset:** attributable
rights; immutable raw source hash; official event/participant identity;
book/market/period/line/outcome/settlement; timezone-aware UTC kickoff and
observed_at; distinguish provider_updated_at from first_seen_at; quote observed
no later than the frozen prediction for CLV; separate closing-line research
from pregame inputs; source-specific reconciliation and no missing-event
silence. If any of these are absent, retain research-only/unavailable status,
never infer a fitted probability or claim market-time accuracy.

**Regression:** `tests/test_free_source_catalog.py` pins the two
reference-only providers outside `MARKET`/`HISTORY`, preserves nflverse
close-proxy-only status, and proves all candidate routes remain non-executable
and unlicensed. No existing adapter, calibration, qualifying threshold,
terminal reducer or production deployment changed.

