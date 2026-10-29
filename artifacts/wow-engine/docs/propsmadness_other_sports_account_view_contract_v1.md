# WOW V17 PropsMadness NBA / MLB / WNBA signed-in view parity contract

**Status:** offline-only adapter for synthetic layout fixtures; no live signed-in source page for these three sports has been verified in this PR.

NFL account-view baseline: PR #2255 merged at `34439930624fad6a898d0637118e96abce26ca84`. The NFL adapter was tested against a real signed-in page structure and still requires source-rights review before historical retention.

This PR adds a narrow non-authoritative data-transformation path for NBA, MLB and WNBA. It does not add a crawler, automatic authenticated browser, scheduler, session management, bulk vendor extraction, database write, fitted-model input or terminal probability publication.

## Sport and market coverage

| Sport | Four scoped full-game statistics |
| --- | --- |
| NBA | POINTS, REBOUNDS, ASSISTS, THREE_POINTERS_MADE |
| MLB | PITCHER_STRIKEOUTS, PITCHING_OUTS, HITS, TOTAL_BASES |
| WNBA | POINTS, REBOUNDS, ASSISTS, THREE_POINTERS_MADE |

Player text and URL aliases are **provisional**, not verified current PropsMadness selectors. The parser requires a source-specific match to the selected stat and a strict `Game-by-game detail for the chart above` table. If the actual browser page differs, it returns a typed hold rather than inventing data.

## Non-negotiable time provenance

For a chart displaying today's selected threshold beside previous results:
- `observed_stat` is a **candidate historical sporting result** requiring independent confirmation;
- `chart_comparison_threshold` is **not** a historical closing line;
- `historical_closing_line`, `historical_odds`, `historical_book`, `historical_market_timestamp`, `official_event_id`, and `official_player_id` stay null;
- upcoming games and future results never become completed historical observations;
- no source rights or model inference are manufactured from account login.

## Acceptance and activation sequence

1. In the user's signed-in, user-controlled browser, inspect one selected player-and-stat view per sport and verify the actual table header, page labels and URL query without exporting cookies. NFL real-page coverage is a separate existing receipt.
2. Repeat one additional player/market per sport, including an absent or empty history and mismatched stat test; capture only row reconciliation and redacted schema hashes, not any bulk account dataset.
3. Validate historical player and game result against an independently permitted official or licensed record and capture timestamps. A historical game result is not a historical prop price.
4. Before scheduling or persisting vendor content, verify permissions for copying, retention and analytical/model-training reuse. PropsMadness' published site Terms require prior written consent for reproduction of website data.
5. If rights are established, use isolated research-only staging with immutable source timestamps, deduplication, idempotency and audit receipts; require separate approval to connect scoring. If rights are not established, proceed with independently permitted sports sources for historical outcomes and market snapshots.

## Tests

`cd artifacts/wow-engine && python -m pytest -q tests/test_propsmadness_other_sports_account_view_adapter.py tests/test_propsmadness_nfl_account_view_adapter.py`

Source tree and regressions remain independent of `V17_TERMINAL_REDUCER`. No wager or live execution actions are added; `can_execute=false`.

## Verified isolated staging database, 2026-10-29

Connected Supabase project: `wow-engine-validation` (`iczfhsmjrrafhvcpmqhr`). A separately recorded migration `wow_prop_history_research_outcome_staging_20261029` was successfully applied. The new table is `wow_prop_history.research_outcome_snapshots`. Its readback returned **zero rows**, RLS enabled, no `anon`/`authenticated` grants and only `SELECT, INSERT` for `service_role`.

The table's rows require an explicit rights-receipt ID, a nonempty source identifier, a canonical SHA-256 digest and sport/game/stat identity, and fail closed if the record attempts to claim model-training eligibility, verified historic market lines, probability publication or execution. These database constraints are additional staging protections, not independent proof that a rights receipt is genuine. Write admission must continue to check independent source entitlement before any ingestion.

**This is storage infrastructure only.** It does not contain an authenticated PropsMadness corpus, does not create a service writer, does not copy cookies and does not make any existing chart comparison a historical quote. No production scoring schema was changed.
