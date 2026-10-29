# PropsMadness NFL account-view research adapter — V1

**State:** read-only, offline contract on a feature branch. Authenticated browser access and visibility of the NFL game-by-game table were separately observed on 2026-10-29; no authenticated ingestion, production session collector, scheduler, database write or source reuse grant is established.

## Source facts and parser mismatch

The existing public Explore reader consumes player links and market summaries from \`/nfl/props/<market>\`. The logged-in player breakdown uses \`/nfl?market=featured&player=<alias>&match=<alias>\` and presents a visible \`Game-by-game detail for the chart above\` table. A connected browser view showed 7 Bryce Young passing-yard chart rows: six completed, one upcoming. The repeated **252.5** column is the selected chart comparison threshold, **not proven historical closing odds/lines**.

## Pure transformer

\`v17.propsmadness_nfl_account_view_adapter.normalize_nfl_history_view\` accepts sanitized, caller-supplied browser-visible TEXT, the public-facing player page URL, the capture timestamp and declared player/market scope. It reads **no** credentials, cookies, sessions, hidden endpoints or other services; it does not store the full page. It rejects malformed, duplicate or temporally impossible historical records. It extracts completed game outcomes as **unverified**, distinguishes future/upcoming games, and always leaves \`historical_closing_line\`, \`historical_book\`, \`historical_odds\`, \`historical_market_timestamp\`, and \`official_event_id\` null.

For the allowed four NFL full-game market families the operator must supply the exact selected market: \`PASSING_YARDS\`, \`RECEIVING_YARDS\`, \`RUSHING_YARDS\`, or \`RECEPTIONS\`. This contract does not turn an account tab, navigation marker, provider alias, or apparently signed-in UI into source-rights or official-identity attestation. The browser view may contain unrelated sections; it is bounded to the selected NFL history table.

## Next acceptance stage, separately gated

1. An independently permitted, user-controlled browser workflow can provide a minimal sanitized view (never passwords or session cookies). Confirm actual layout and exact row reconciliation across at least two different NFL player/stat pages and an empty-history case.
2. Independently verify official player/event/date/stat/outcome labels from a suitable permitted sports source. Preserve source chronology, corrections and content hashes.
3. Confirm vendor/provider rights for recurring collection, caching, historical reuse and fitted-model training before enabling any automated or retained vendor extraction. A signed-in account is **not** itself that grant.
4. Keep any first future snapshots separate from historical lines. Only real snapshots originally captured before a given game's kickoff can establish that game's pregame observations; retrospective chart comparison thresholds cannot.
5. Consider isolated Supabase staging and scheduling only under their separate review, with no production feature writes, no fitted probabilities, and \`can_execute=false\`.

## Verification

Offline targeted suite: \`cd artifacts/wow-engine && python -m pytest -q tests/test_propsmadness_nfl_account_view_adapter.py\`.

No new workflow, API operation, secret, database migration, network collector, model route or terminal reducer change is introduced. The only global terminal authority remains \`V17_TERMINAL_REDUCER\`.
