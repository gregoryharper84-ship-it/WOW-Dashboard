# WOW V17 — Optional Sports News Discovery (research only)

Related work: umbrella #2076; Scout acquisition #1481; source health #1484; free-core #1452; continuous intelligence #2164.

## Contract

This additive adapter reads explicitly configured licensed JSON feeds into the existing Scout source snapshot store. It does not scrape publisher websites, acquire a vendor subscription, confirm a lineup/injury or identify an official event.

- Scope: all 13 sports recognized by the existing web intelligence sport profiles; scheduled Scout defaults now include all 13 for optional news, while structured SportsDataIO requests remain only for configured supported capabilities.
- Source classification: PUBLIC_WEB_RESEARCH (not league-official verification).
- Research authority only: prediction_authority=false, can_execute=false.
- No changes to LLP team/event probabilities, player props, calibration, eligibility, terminal reducer or betting.
- Story publication timestamps are publisher claims, NOT proven pre-lock availability. All stories have historical_pregame_eligible=false until a separate identity/chronology verification.
- provider_event_id is never silently promoted to official_event_id.
- Headline/summary are truncated. No full publisher article text is stored.
- News discovery uses a news-specific append-only snapshot ID that includes captured response-completion time and payload digest; duplicate content at different retrieval times produces separate immutable Scout records. The generic structured-provider content-deduplicated storage is unchanged. All news outcomes, including blocked HTTP requests, include a unique captured timestamp within the payload hash, respecting the existing SQL unique constraint on provider/sport/capability/payload_hash. This is an *observed retrieval receipt*, not verified first-publication/available-to-user evidence; articles remain historical_pregame_eligible=false until independently verified, so this path alone cannot certify historical backtests.

## Opt-in configuration

Set the following in the existing Scout GitHub Actions environment ONLY after verifying selected-feed rights permit automated access, bounded storage and this research use.

| Key | Storage | Example |
|---|---|---|
| WOW_NEWS_FEED_ENABLED | Actions variable | true |
| WOW_NEWS_FEED_ALLOWED_DOMAINS | Actions variable | feeds.example.org |
| WOW_NEWS_FEED_TARGETS_JSON | Actions variable | [{"sport_key":"basketball_nba","provider":"LICENSED_NEWS","url":"https://feeds.example.org/sports.json"}] |
| WOW_NEWS_FEED_LICENSE_ACK | Actions variable | true |
| WOW_NEWS_FEED_API_KEY | Encrypted Actions secret, optional for a licensed feed | Never commit |
| WOW_NEWS_FEED_API_KEY_PROVIDER | Actions variable | LICENSED_NEWS |
| WOW_NEWS_FEED_API_KEY_HOST | Actions variable | feeds.example.org |

The example endpoint is NOT a real verified feed; do not enable it. The license ACK is an operator attestation, not legal approval. The adapter currently supports Bearer API tokens only. A bearer credential is sent **only** if both the configured provider identifier and the exact feed hostname match; GDELT never receives the license token. If a token is configured without an explicit scope, eligible private feed requests fail closed. Providers with other auth schemes need separately reviewed adapters.

WOW_SCOUT_DATABASE_URL is required to persist evidence. This PR changes the existing Scout workflow preflight so a configured database can continue optional permitted news acquisition even without a SportsDataIO key. Missing SportsDataIO is preserved as typed structured-source blockers, not green acquisition. Missing database configuration generates a BLOCKED_SOURCE_CONFIGURATION JSON receipt that the existing scheduled workflow uploads rather than silently skipping. This repairs the credential-skip symptom tracked by #1484 while preserving source failures. No new schedules, billing, subscriptions or service credentials are created.

### Source JSON shape

    {
      "articles": [
        {
          "url": "https://source.example.org/news/123",
          "title": "Team news update",
          "published_at": "2026-11-16T18:00:00Z",
          "summary": "Optional excerpt; publisher claim only",
          "event_id": "provider-specific-id"
        }
      ]
    }

### Free GDELT Project DOC 2.0 option

The public GDELT Project DOC 2.0 ArtList JSON API is supported as a discovery source. It is NOT the separately hosted GDELT Cloud product. Set provider=GDELT, allowlist=gdeltproject.org, and supply an operator-approved URL such as:

    https://api.gdeltproject.org/api/v2/doc/doc?query=NBA%20injury&mode=ArtList&format=json&maxrecords=5&timespan=24h

Replace query with the chosen sport/news criteria, and verify feed limits and attribution at https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/ and https://gdeltproject.org/data.html. DOC API requests are rate-limited and may return HTTP 429; this Scout adapter does not retry. Only ONE GDELT query per refresh is permitted. Its seendate is recorded as source_discovered_at_claim, NOT as publication or availability-to-user time. No article is an official injury report or verified lineup. GDELT is broad news discovery, not complete 13-sport event coverage.

All URLs must be HTTPS; feed endpoints must match the operator allowlist. Compressed HTTP responses are rejected before decoding; raw streamed response bytes are capped at 512,000 bytes to prevent decompression bombs. Each successful news payload contains a source_observed_at timestamp collected **after** the HTTP response is read. This timestamp is not proof of the publisher's actual publication time or a hidden earlier pool user's visibility. Payload capped at 512000 decoded bytes, maximum five targets and 40 articles per target. Future, timestamp-free, duplicate, malformed or unsafe article URLs are excluded. Redirects are not followed. Failed optional news does not alter the structured-source status. An enabled but empty target list yields CONFIG_INVALID, not a healthy feed.

## Test command

    cd artifacts/wow-engine
    python -m pytest -q test_v17_news_feed_research_adapter.py test_v17_research_source_adapters.py test_v17_firecrawl_research_adapter.py test_v17_source_snapshot_identity.py

## Acceptance

1. Verify provider terms covering automated retrieval, caching, retention, historical modeling and pick'em-related research.
2. Verify a live bounded source receipt, source identity, and that no secrets leak.
3. Confirm blocked credentials, HTTP errors, redirects and invalid source formats produce typed SOURCE_* failures without affecting fitted model probabilities.
4. Independently verify official event identity, first-available timing and material claims BEFORE any fitted feature extraction. Goalie, pitcher, lineup and injury claims remain subject to existing independent confirmation rules.
5. Measure out-of-time feature lift against frozen pregame champions, with Brier, log loss and appropriate market-specific metrics.
6. Require independent SIRT exact-head approval, normal QA/Release protections and verified post-deployment source receipt.

Rollback: set WOW_NEWS_FEED_ENABLED=false. No model or database schema migrations.
