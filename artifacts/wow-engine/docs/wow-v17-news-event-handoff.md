# V17 news discovery to research rescore request

**Scope:** follow-on read-only contract for the public GDELT Project collector in PR #2693.
The collector is an independent acquisition step; this bridge **never downloads
publisher articles**, authenticates source claims, changes fitted features, or
invokes a registered scorer.

## Event flow

1. The independently released GDELT discovery job emits a source receipt with
   provider, sport, observed timestamp and discovered article URLs.
2. An *external*, independently authorized verifier must inspect the publisher
   and official fixtures and supply a reviewed-fact record. A GDELT seen date
   is **not** a publisher publication time; an article headline is **not** an
   injury, goalie or lineup fact.
3. `reconcile_news_handoff` requires an exact canonical event ID, canonical
   sport, future kickoff, valid publisher/verification timestamps, source
   freshness and the article's exact discovery URL. It collapses duplicate
   source groups and quarantines conflicting facts. For high-impact facts,
   secondary sources require independent corroboration.
4. `sport_scout_enrichment.enrich_candidate` attaches the resulting
   `news_research_handoff` to an existing team-event or prop candidate
   **only when** `news_discovery_receipt` is supplied. No new independent
   scorer, scheduler or database is introduced.
5. A material reviewed change produces
   `research_status=NEWS_RESEARCH_RESCORE_REQUESTED`, not a model change.
   The controlling model owner must fetch authoritative current inputs,
   rerun the certified market-specific specialist, freeze a new pregame
   model receipt and obtain an independently verified terminal readback.
   The original pregame receipt must remain immutable.

## Explicit safety/acceptance

- Every research handoff has `prediction_authority=false`,
  `model_feature_eligible=false`, `model_rescore_verified=false`,
  `calibrated_probability=null`, `can_execute=false`.
- `research_rescore_requested` is **not** proof that rescoring ran.
- Metadata supplied as `reviewed_facts` is an external assertion. This
  module only checks its consistency; it does not authenticate signatures,
  inspect the publisher URL, or independently establish event truth.
- Post-event discovery, a stale/naive/missing source timestamp, ambiguous
  event, source conflict, unconfirmed high-impact report, or malformed
  source receipt fails closed. Frozen historical features never backfill
  from information first seen post-kickoff.
- Source collector PR #2693 must pass its own independent review and protected
  merge before the feed itself can be called active. A passing unit test for
  this bridge does **not** demonstrate end-to-end real news acquisition or
  fitted scoring.
- Future live acceptance: artifact from the scheduled fetch; authorized
  reviewed event mapping; material feature update; genuine controlling-model
  rescore and immutable pregame receipt; exact deployed SHA; QA and Release
  acceptance; independent model improvement measurement.
- There is no new paid service, database, provider credential, wager execution,
  or synthetic probability. V17_TERMINAL_REDUCER remains the sole terminal
  authority.

Targeted test command:

```bash
cd artifacts/wow-engine
python -m pytest -q test_v17_news_event_research_handoff.py tests/test_v17_sport_scout_teams.py
```
