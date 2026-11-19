# WOW V17 — PropsMadness one-page public research handoff (2026-11-19)

**State:** opt-in, user-initiated, one public page, in-memory discovery only. No subscription session is used. This is not a production model feature feed or a permission to archive vendor data.

## Reason and path

The existing one-shot public Explore fetcher already checks robots.txt before fetching a single allowlisted page. The existing `build_shared_intelligence_handoff` turns validated parser receipts into `prop_discovery_hints` and `llp_event_research_hints`. Those two components were not connected at the command-line entry point. The `--research-handoff` switch connects them for one invocation only. No service, scheduled task, model route, source registry, SQL table, release rule, or secret changes.

Only when the operator can truthfully attest *personal non-commercial use* under the current vendor conditions, run from `artifacts/wow-engine`:

```sh
python -m v17.propsmadness_multisport_public_explore --sport NBA --market threes --confirm-personal-noncommercial-use --research-handoff
```

This command emits JSON on stdout for the current research session; it **does not store it in a database or file**. Don't redirect output into an archive or persist vendor data unless separate written source rights allow it. If robots policy cannot be verified, the page is inaccessible, or source text cannot be parsed, the command emits a typed HOLD; empty rows are not evidence that the slate is empty. No login, premium automation, API probing, automatic retries, proxies, CAPTCHA bypass, or scheduled scanning are part of this path.

Existing allowlisted sport/market pairs: NBA (points, rebounds, assists, threes), NFL (passing-yards, receiving-yards, rushing-yards, receptions), MLB (strikeouts, pitcher-outs, hits, total-bases), WNBA (points, rebounds, assists, threes). Other markets are not implicitly supported. User-supplied browser-visible Premium one-page research has a distinct existing offline parser and does not enter this public-page flow.

## Source authority and downstream boundary

The only resulting data domains are `DISCOVERY_RESEARCH_ONLY` leads, unverified provider aliases and displayed line/average/odds hints. They **must not** be used as an official player/event match, sportsbook quote, pregame input, predicted hit probability, model training corpus, calibration sample, certified qualifier or execution signal. The handoff explicitly emits `verified_sporting_features=[]`, `model_consumption_receipts=[]`, and false for `source_rights_verified`, `historical_training_authorized`, `prediction_authority`, `probability_publishable`, `production_write_authorized`, `can_execute`. A malformed/forged authority field causes a typed HOLD rather than partial acceptance.

Reference rights: https://propsmadness.com/terms-and-conditions (March 9 2026, Section 5 limits to personal noncommercial use, consent for reproduction); https://propsmadness.com/help/en/articles/11105995-how-do-you-get-your-data (no public API). Lack of an explicit `scraping` prohibition is **not** a grant to copy, retain, commercialize, bulk collect or train.

The actual model lane continues to depend on independently licensed/authorized sporting features and market evidence, a certified fitted specialist, point-in-time identities and calibration, and `V17_TERMINAL_REDUCER`. Only a future reviewed vendor-specific rights receipt may authorize separate automated retained ingestion. Until then the registry `APPROVED_LIVE_SCRAPE_SOURCES={}` must remain empty.

## Reproducible synthetic verification

Run `PYTHONPATH=. python -m pytest -q tests/test_propsmadness_multisport_public_explore.py tests/test_propsmadness_intelligence_handoff.py tests/test_propsmadness_manual_workflow_contract.py`.

The new tests cover: one original public-page fetch reused for exactly one handoff; rejection of forged `model_probability`; no user attestation fails closed. They never request the vendor website, create a market quote, store content or invoke a fitted model.

**Unverified until independently evidenced:** live-site robots response, actual public row coverage, permissions for recurring extraction, accurate canonical identities, production /score-pick-request, and model-performance effect. No claims of successful live PropsMadness data ingestion are made.
