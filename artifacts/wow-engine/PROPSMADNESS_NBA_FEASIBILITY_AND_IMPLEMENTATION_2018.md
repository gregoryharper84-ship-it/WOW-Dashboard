# PropsMadness NBA — Historical Prop Data Feasibility & Integration Gate

WOW issue #2018. Status: synthetic offline contract implemented; provider API/data-feed rights not verified; production integration prohibited.

## Why this could matter

PropsMadness visually exposes NBA full-game Points/Rebounds/Assists/3PM, prior player-game results, minutes, usage, FGA, injury/teammate with-without context, shot zones/play types, opponent defensive context and sportsbook comparisons. These help discover candidate props but are not fitted probabilities.

**Most valuable historical deliverables, if vendor-authorized:**

1. Player-game labels (PTS/REB/AST/3PM) with event/game/player IDs, finalization and correction history. These can independently corroborate SPORTING labels instead of replacing independent/licensed final box scores.
2. Pregame context snapshots (minutes expectation, usage/FGA history, teammate availability, opponent defense and role) with first-known-before-tipoff timestamps, historical revisions and metric definitions. Current retrospective with/without splits are not automatically pregame-available evidence.
3. Exact historical sportsbook line/price snapshots (book, market, direction, period, line, odds and UTC capture/effective time), only where archival depth and retention rights are expressly granted. Such data are MARKET evidence, not sporting model probability.
4. Corrections/quality: DNPs, traded players, schedule changes, renamed stats, source hashes, conflicting records and field lineage.

**Critical unknown:** historical odds availability. Current Explore/odds UI and per-player game logs do not prove exportable historical line archives.

## Offline code already built (zero PropsMadness calls)

- NBA synthetic fixture source adapter: artifacts/wow-engine/v17/propsmadness_nba_offline_adapter.py
- Negative tests: artifacts/wow-engine/tests/test_propsmadness_nba_offline_adapter.py
- Four launch stats: POINTS, REBOUNDS, ASSISTS, THREE_POINTERS_MADE; full game only.
- Three types: MARKET_DISCOVERY, PREGAME_CONTEXT, FINAL_OUTCOME. MARKET and SPORTING facts never share a probability contract.
- Both observation and retrieval for pregame context/market must precede tipoff. Historical final outcomes must follow finalization, and cannot be repackaged as pregame context.
- Even a valid fixture always emits source_provenance_attested=false, training_ready=false, storage_write_authorized=false, model_certified=false, probability_publishable=false and can_execute=false.
- No API calls, cookies, login, network, sessions, browser scraping, database writes, scheduled fetch or changed source entitlements.

## Vendor license/delivery approval, before real data

PropsMadness help officially says NO PUBLIC API. Its active March 9, 2026 terms grant personal noncommercial access and require written consent for copying/reusing content/data. Paid Pro is not a data licensing right.

Contact: hello@propsmadness.com

Request written answers for:
- Approved mode: vendor feed, scheduled structured CSV/JSON export or sanctioned research dataset; no hidden API reverse engineering
- Commercial/internal analysis, historical storage, derived feature training, publication/redistribution and sportsbook-odds reuse as **separate use grants**
- Historical season coverage, exact player market types, source first-available timestamps and corrections
- Player/official event identifiers, source provenance and attribution
- Point-in-time archived book, line, odds and timestamp availability versus only currently displayed odds
- Retention/deletion requirements, source revision SLA, request budget, credential scope and price

**GO:** signed specific rights plus supported export and independent reviewer acceptance; then separate real-source adapter PR.
**LIMITED:** manual visual research only if automated reuse is disallowed.
**NO GO:** unauthorized scraping, login/session replay, reversing private endpoints, CAPTCHA/rate-limit/403/429 evasion.

## Future authorized canary

Read-only, three completed historical NBA dates and two upcoming slates, >=100 player/event/stat observations where permitted. Validate canonical identity, settled result comparisons against independent permitted box scores, exact pre-tip book/line/price against licensed market data, immutable hashes, source schema drift, gaps and cost. This is an engineering target, not a claim of provider coverage.

Never invent historical pregame snapshots from retrospective filters. Missing observations stay missing; scoreboard outcomes are not pregame model features.

## Owner's updated approach: no vendor email; controlled public-page research

The owner prefers public-page extraction to vendor outreach. The prior email draft was not sent. The code includes **no** vendor API authentication, undocumented endpoint access or privileged browser automation.

- Public free NBA Explore example: https://propsmadness.com/nba/props/points. A visible page can show player labels, lines, display odds and recent averages. It does **not** verify book-specific odds, original historical timestamps, official player/game identities or a historical archive.
- A **one-shot** public HTML extractor now exists in `v17/propsmadness_nba_public_explore.py`. It supports only four NBA public Explore URLs; first checks live `robots.txt`, rejects unknown/disallowed rules, 403/429, changed response origin, payload over 2 MB and unrecognized HTML. No proxies, login, CAPTCHA, private API, retries, scheduled collection, cookies, database writes or source-entitlement edits.
- CLI, only where personal noncommercial research use is consistent with the site's current conditions: `python -m v17.propsmadness_nba_public_explore --market points --confirm-personal-noncommercial-use`. This acknowledgement is not a license for WOW commercial analytics, republishing, automated bulk collection, retention or model training. Do not misrepresent the intended use to activate it.
- Parser outputs discovery-only, null canonical identities, null sportsbook and null official kickoff, as well as `historical_line_evidence=false`, `pregame_verified=false`, `historical_training_authorized=false`, `probability_publishable=false`, `can_execute=false`. It never invents missing history or promotes displayed odds to governed probability.
- CI uses **synthetic** sample HTML only; actual live robots permission, schema suitability and successful website fetch are not yet verified. Unknown robots policy means do not fetch the target page.

**Historical limitation:** An observation collected now is not a historical pregame observation for a prior game. Retrospective grades, hit rates and displayed closing lines do not prove an original timestamp or the exact sportsbook quote. Genuine model training needs documented pregame snapshots and license-compatible reuse. Existing NBA Stats/CDN game-log and reviewed SportsDataverse sources are better candidates for historical label backfill while PropsMadness usage remains restricted.

---

## Four-sport public Explore extension (build increment)

The supported research discovery surface expands to all **four vendor-advertised leagues**: NBA, NFL, MLB, and WNBA. Official four-league coverage: https://propsmadness.com/help/en/articles/11104394-will-you-be-adding-other-sports ; public Explore description: https://propsmadness.com/help/en/articles/17694010-explore-every-player-at-once . Off-season leagues may show no rows.

New sidecar: `v17/propsmadness_multisport_public_explore.py`, using the existing public HTML parser without modifying NBA fitted model lanes or the historical source rights manifest. Provider player and match IDs are **unverified aliases**.

| Sport | Four initially allowlisted markets | Typed stat |
| --- | --- | --- |
| NBA | points, rebounds, assists, threes | POINTS, REBOUNDS, ASSISTS, THREE_POINTERS_MADE |
| NFL | passing-yards, receiving-yards, rushing-yards, receptions | PASSING_YARDS, RECEIVING_YARDS, RUSHING_YARDS, RECEPTIONS |
| MLB | strikeouts, pitcher-outs, hits, total-bases | PITCHER_STRIKEOUTS, PITCHING_OUTS, HITS, TOTAL_BASES |
| WNBA | points, rebounds, assists, threes | POINTS, REBOUNDS, ASSISTS, THREE_POINTERS_MADE |

The **16 definitions** are public-route/fixture support, not 16 verified active feeds or permitted recurring collectors. One invocation performs at most two requests: robots and one allowlisted public HTML page. Live access is off unless the operator truthfully confirms **personal noncommercial use** consistent with the provider's terms. Login, CAPTCHA circumvention, session harvesting, hidden API access, 403/429 retrying, crawl pagination and schedules are excluded. CI runs only synthetic fixtures.

Manual example, only for actually permitted personal/noncommercial use:

    python -m v17.propsmadness_multisport_public_explore --sport NFL --market passing-yards --confirm-personal-noncommercial-use

Other sport examples: MLB strikeouts, WNBA points and NBA assists. Live robots policy and page/schema availability are unverified. Zero rows or locked/offseason rows must remain missing, never manufactured.

All outputs are discovery-only with no verified sportsbook, official player/event identity, kickoff, exact-line authority, retrospective pretip snapshot, stored historical archive or model probability. MLB PITCHING_OUTS is distinct from innings-pitched/1IP; NFL stat families remain non-interchangeable. This PR does not confer market or sporting model authority.

For historical model features, use independently verified, legally usable NBA/NFL/MLB/WNBA final outcomes and true licensed pregame snapshots. A present-day public page cannot retroactively establish last season's original lines or injury context. Future historical retention or training requires separate source rights and certified model acceptance.

---

## WOW interface

Scout discovery would receive research candidates with exact player-event/stat/period/line identity and separate market/sporting sources, never a ranked model probability. Private historical staging needs separate security and source-rights approval. The NBA POINTS model work remains owned by issue #1988; usage/on-off/DvP challenger #1863 remains post-restoration. Any calibrated probabilities require the certified controlling specialist and V17 terminal reducer.

**Decision economics:** pursue a feed only if it adds (a) true historical pretip line histories WOW lacks, (b) historical injury/usage snapshots with timestamp proof, or (c) independently useful licensed player history filling a measured data gap. Otherwise prefer independent licensed/official NBA feeds and use PropsMadness as manual discovery.

Status: OFFLINE_FIXTURE_BUILT / ACCESS_UNVERIFIED / TRAINING_RIGHTS_UNKNOWN / NBA_MODEL_UNCHANGED / NO_EXECUTION.


## October 26 exact-head acceptance and integration gate

The unrelated cross-sport NHL synthetic-identity test defect was repaired in existing PR #1744, merged to main as `e9f70b5792d7666cd6e8631a2222b7b691ff0262`. The repair changes a single test fixture, not the production NHL resolver.

The PropsMadness PR requires **new** pull-request CI after this merge; 97 focused fixture passes on the prior head alone do not prove its full run green against updated main. Preserve all four-sport/16-market row coverage, no silent source deletions, identity holds, negative source-rights cases, and fail-closed `can_execute=false`.

The expected output remains a human-reviewable **research-only** contract. This PR must not enable any scheduled or recurring vendor collection, allow unverified aliases to become official events/players, store an unauthorized historical corpus, or register fitted model/probability routes. Neither matching publicly displayed lines nor a successful robots request grants training or republication rights. A separately authorized real-source canary and exact official identity reconciliation are independent acceptance gates, followed by chronology-correct shadow evaluation and route-specific fitted certification before any production probability usage.


## October 27 review scope: vendor workflows deferred

The two owner-authored `.github/workflows/` proposals were removed from this PR.
The proposed merge contains **offline Python source, test fixtures and documentation only**.
There is no GitHub Actions `workflow_dispatch` endpoint installed by this PR,
no schedule, no configured live scraper, and no source-rights grant. The old
one-page workflow described above is a **historical draft design**, not an
installed operation; it must not be invoked as if it existed.

The existing repository-wide regression checks continue to run on the PR.
The optional vendor probe workflow, should one become permitted, requires a
separate appropriately reviewed change with a documented use grant. Removing
this proposed workflow does not weaken any existing protected CI workflow.

No data from the public website has been acquired or certified through this PR.
Official identities, fitted-model consumption and terminal publication remain
separate contracts. `can_execute=false`.
