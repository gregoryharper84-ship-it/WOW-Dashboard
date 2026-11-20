# WOW V17 — Additional all-sports news and structured-data sources

## Scope and current status

This is an **additive research-source registry and offline editorial normalizer**.
It does not activate a provider, obtain keys, create an ingest scheduler,
write fitted inputs, rerun a specialist, promote a model, or modify trading.
Existing GDELT discovery, Scout, SportsDataIO league adapter, model ownership,
and `V17_TERMINAL_REDUCER` stay unchanged. `can_execute=false`.

| Source | Purpose | Access / evidence gate | Actual enabled state |
|---|---|---|---|
| GDELT | Public article discovery | Existing collector; verify current nonempty receipt and 429 recovery | Existing collector, live health not established by this PR |
| ESPN RSS | Supplemental publisher headlines | Document permissible automated/RSS use and verify feed accessible without circumvention | Not enabled |
| NewsAPI | Supplemental global/sports headlines | Free Developer plan is **non-production**; production license + key required | Not enabled |
| Highlightly All Sports | Scores, fixtures, player/team, lineups where entitled | Provider key, actual sport/field coverage, quota and license check; free tier advertises 100 daily requests | Not enabled |
| SportsDataIO Global | Broader structured data | Separate Global API entitlement and real-data contract; existing league API key does not prove this | Not enabled |
| AllSportsApi | Additional structured sports | Official provider terms and endpoint; vendor advertises **14-day trial**, not permanent free production | Not enabled |
| RSS.app | Optional RSS-to-JSON conversion | API key and usage terms; converter grants no publisher reuse rights | Not enabled |

References checked 2026-11-20:
- https://newsapi.org/pricing and https://newsapi.org/s/us-sports-news-api
- https://highlightly.net/sport-api/ and https://highlightly.net/sport-api/documentation/coverage/
- https://sportsdata.io/global-api and https://sportsdata.io/developers/apis
- https://allsportsapi.com/
- https://rss.app/en/docs/api/feeds
- https://www.espn.com/espn/rss/news (automated access challenge observed; no bypass)

## Contracts

1. The source inventory is a capability **candidate register**, not an uptime or entitlement receipt.
2. Editorial data are headlines, URLs, untrusted publication-date claims and provenance only.
   The parser does not download or store articles. It requires valid source URLs and dates,
   rejects XML entity/DTD payloads, and labels every record evidence-only.
3. Source statuses remain independent. A GDELT 429 is not repaired by claiming ESPN or
   NewsAPI coverage. Source errors are `SOURCE_*`/native acquisition statuses, never
   `MODEL_UNAVAILABLE`.
4. Do not convert provider injury labels or articles into official facts. An independent
   league/team/publisher check and canonical event ID are required.
5. A material verified development requires an up-to-date permitted input snapshot,
   **exact controlling specialist** rerun, genuine immutable pregame forecast, calibration,
   bound and independent production readback. News has no probability authority.
6. Preserve existing cost directive: no paid signups, no metered use, no new
   Custom GPT Action or credential distribution without explicit authorization.
7. Coverage for NFL, NBA, NHL, NCAAF, NCAAB, MLB, WNBA, soccer, tennis,
   golf, MMA, boxing and cricket must be reported by available route and source;
   no vendor is presumed to cover every requested sport.

## Follow-on engineering acceptance (not claimed complete)

- **A. Rights and coverage**: document allowed publication/redistribution/ML use,
  exact supported sport/market, permitted payload and time-correct attributes.
- **B. Provider adapters**: implement one bounded, allowlisted, credential-safe
  endpoint at a time; never scrape a blocking/challenge page; opt-in per provider.
- **C. Independent acquisition proof**: scheduled run receipt with provider,
  article/record counts, error, HTTP status, timestamps and sport/event links.
- **D. Research integration proof**: one eligible future official event linked to
  an independently verified material fact, with contradictions and duplicates handled.
- **E. Model-loop proof**: new fitted-specialist call and immutable before/after
  receipts; compare input timestamps and ensure no pregame hindsight leakage.
- **F. Independent acceptance**: source safety/rights, protected release
  checks, exact SHA, production readback and forward reliability metrics.

Local offline tests:

```bash
cd artifacts/wow-engine
python -m pytest -q test_v17_multi_source_news_registry.py
```

This PR meets only the registry/normalizer preparation portion of B.
It does **not** satisfy acquisition, live coverage, news-based model updates
or any production-complete acceptance.
