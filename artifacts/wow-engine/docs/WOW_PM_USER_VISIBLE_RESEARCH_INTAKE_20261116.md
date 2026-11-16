# WOW V17 — PropsMadness selected-page reader (2026-11-16)

## Purpose and rights
This is the narrowest immediately usable Premium-member workflow: read the **single page the user opens**, extract a few display-only research values, and analyze them alongside independently permitted data. It is **not unattended scraping**.

The vendor's March 9, 2026 Terms grant personal, noncommercial access and restrict reproduction or distribution without written permission: https://propsmadness.com/terms-and-conditions . Its Help Center states there is no public API: https://propsmadness.com/help/en/articles/11105995-how-do-you-get-your-data .

Keep ownership in existing source-rights #1933, feasibility #2018, and first NFL fitted production demonstration #2285.

## Data flow
1. The user opens a player page in their existing signed-in browser; ChatGPT reads the rendered single-page context on their direction.
2. The pure parser function parse_user_visible_player_page accepts the selected URL, rendered page text, expected player and explicit timestamp. Initially handles only the fixture-tested NBA POINTS UI layout. Other routes HOLD.
3. An ephemeral receipt reports only displayed line, book badge, O/U prices, count/rate, basic displayed stats, capture time and page hash. No raw page, cookies or session tokens are written to any database or repository.
4. All provider player and match IDs are aliases, not canonical identities. A displayed hit rate is not a sporting-model probability, and bookmaker prices are unverified display hints.
5. Any later WOW model use requires separately permitted source rights, independent official identity/line/time verification, fitted calibration and pregame evidence, and V17_TERMINAL_REDUCER authority.

## Fail closed
HOLD for unsupported URL/market, duplicate player panels, mismatched player, malformed lines, prices or hit rates, ambiguous timestamp or overlarge page. Missing rows do not prove no games. All outputs preserve false source_rights_verified, historical_training_authorized, probability_publishable, production_write_authorized, and can_execute. There is no network, storage, scheduler, credential handler, or score route in this module.

## Tests
Local TDD: 7 expected failures from an unimplemented stub, then 14 fixture tests passed. Code review reproduced 4 additional failures involving cross-player panel contamination and truncated odds; after the bounded parser repair 18 tests passed; a further Windows CRLF regression failed before normalization and then brought the suite to **19 fixture tests passed** (2026-11-16). No actual vendor page is in the test corpus. Hosted CI and protected merge are separate.

Run from artifacts/wow-engine:
PYTHONPATH=. python -m pytest -q tests/test_propsmadness_visible_page_research.py

## Later automated source path
The Premium membership does not alone establish bot/export, retention or model-training rights. A separate draft request exists for PropsMadness to document approved data export or automated access terms. If authorized in writing, attach the vendor receipt in #1933, then extend the existing rights-gated source capture module prop_scraper_capture_v1.py with exact URLs, fields, approved authentication, frequency, attribution, storage and training limits. Do not build a duplicate scraper or bypass vendor access controls.

No bets, purchases, trades or automated orders; can_execute remains false.
