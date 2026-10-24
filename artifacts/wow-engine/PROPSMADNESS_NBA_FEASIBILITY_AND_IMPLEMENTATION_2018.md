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

## WOW interface

Scout discovery would receive research candidates with exact player-event/stat/period/line identity and separate market/sporting sources, never a ranked model probability. Private historical staging needs separate security and source-rights approval. The NBA POINTS model work remains owned by issue #1988; usage/on-off/DvP challenger #1863 remains post-restoration. Any calibrated probabilities require the certified controlling specialist and V17 terminal reducer.

**Decision economics:** pursue a feed only if it adds (a) true historical pretip line histories WOW lacks, (b) historical injury/usage snapshots with timestamp proof, or (c) independently useful licensed player history filling a measured data gap. Otherwise prefer independent licensed/official NBA feeds and use PropsMadness as manual discovery.

Status: OFFLINE_FIXTURE_BUILT / ACCESS_UNVERIFIED / TRAINING_RIGHTS_UNKNOWN / NBA_MODEL_UNCHANGED / NO_EXECUTION.
