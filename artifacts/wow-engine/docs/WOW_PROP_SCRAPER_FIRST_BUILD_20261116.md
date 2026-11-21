# WOW V17 - Scraper-first player props build plan (2026-11-16)

Source owner: existing PropsMadness feasibility issue #2018. Reuse #1933 for multi-source rights, #2499 for candidate handoff, #1988 for NBA POINTS, #2668 for model factory and #1563 for independent SIRT assurance. No parallel scorer, review council or terminal authority.

## A. Acquisition boundary - this PR

- Introduce a one-shot public HTML capture core that accepts only explicitly code-reviewed permitted source/URL pairs. The live source registry is empty until reviewed data rights exist.
- Exact HTTPS URL allowlist and independently approved rights receipt required. No username, password, cookies, session replay, login flow, premium content, reverse-engineered endpoints or CAPTCHA bypass.
- Robots.txt is read first; absent/malformed/disallow means HOLD. One HTML request maximum. Redirects, 401/403/429, unsupported content types and oversized pages fail closed with typed blockers. No rate-limit retries.
- Restrict outputs to in-memory display-only player/stat/line research hints, provenance hash and capture time. Unknown player/event/price/period authority remains unverified. Never generate fitted probabilities from hit rates.
- Targeted fixture tests do not access any external vendor. No Supabase migration, collection scheduler, production Scorer or trading change in this PR.

## B. Sports and target features

Pilot NBA full-game POINTS, REBOUNDS, ASSISTS, THREE_POINTERS_MADE and NFL PASSING_YARDS, RUSHING_YARDS, RECEIVING_YARDS, RECEPTIONS. Add MLB PITCHER_STRIKEOUTS, PITCHING_OUTS, HITS, TOTAL_BASES and WNBA full-game PTS/REB/AST/3PM after reusable adapters are accepted.

Scraper modules: player game logs; minutes and opportunity; injury/lineup and with/without states; opponent matchup and schedules; exact market line and book price; official result and corrections. Scraped odds, hit rates and publisher matchup grades are research evidence, not sporting-model probabilities.

## C. Permission-first live adapters

1. For each specific candidate, independently confirm automated access, collection scope, storage, derived features/model training, reuse/distribution and retention terms. Existing free-source catalog is a candidate inventory, not permission.
2. PropsMadness explicitly says no public API. Its current terms do not grant unrestricted automated collection, retention or derivative model training. Preserve its existing offline/public personal-use discovery adapter. Do not put PropsMadness in the live scraper registry absent documented applicable permission.
3. Build source-specific HTML and freely published downloadable-asset parsers only where permitted. Prefer existing SportsDataverse/official historical feeds when they have independently verified redistribution and training rights. A repository license cannot automatically authorize restricted upstream source use.
4. Run read-only canaries on three settled historical dates and two upcoming slates, with at least 100 reconciled observations where source coverage permits. Verify timestamps, canonical player/event/stat identity, corrected official outcomes, source freshness and quality. Zero scraped rows are NOT proof of zero games.

## D. Historical evidence and models

- Record as-of source timestamp, fetch time, event start, revision, checksum and permissions. A current page never proves a prior pregame line, injury status, minutes projection or market snapshot.
- Distinguish SPORTING labels from MARKET quote evidence. Resolve DNP/void/push/OT/retirement and exact game periods.
- NBA: model minutes, usage, attempts, rate, opponent pace and 3P variance with hierarchical distributions and lineup scenarios.
- NFL: opportunity models for targets/carries/dropbacks, snaps/routes, pace and scripts, opponent coverage/run front, injuries, travel/rest.
- Fit each exact sport/stat model using chronological train/calibration/test splits; compare against L10/season/market baselines; calibrate probabilities and valid lower bounds.
- Independently forward-grade immutable predictions. A fitted candidate or green CI is not certification.

## E. Daily WOW integration and acceptance

Flow: permitted scrape -> rights/source health -> normalized research observations -> independent official IDs and pregame chronology -> frozen feature snapshot -> fitted controlling prop specialist -> calibration and lower bound -> existing score-prop and V17 terminal reducer -> research rankings -> official result grading.

For every candidate, label RESEARCH, SHADOW, MODEL_QUALIFIED or HOLD. Correlated slip research requires joint-probability testing; multiplication of unsupported marginal picks is not a qualified slip.

TRIPLE D:
- DEBUG denied source, access/robots, redirects, 429, stale quote, post-start feature, inconsistent identity, schema drift, duplicates, source conflict and missing fitted artifact as separate failures.
- DISSOLVE reuse existing owners/issues, no competing database writers, new paid sources, schedulers or universal approval gates.
- DEPLOY only after exact-head CI, authentic independent QA/Release and source/model SIRT acceptance. Verify deployed Render SHA and genuine Action -> persisted immutable receipt -> V17_TERMINAL_REDUCER readback for any later production-qualified route.

This Phase A build is a development branch, NOT an approved source entitlement, production connector, trained model, or verified daily card. Retain can_execute=false and no wager/order actions.
