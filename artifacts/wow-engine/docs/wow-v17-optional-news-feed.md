# WOW V17 — Optional Sports News Discovery (research only)

Related work: umbrella #2076; Scout acquisition #1481; source health #1484; free-core #1452; continuous intelligence #2164.

## Contract

This additive adapter reads explicitly configured licensed JSON feeds into the existing Scout source snapshot store. It does not scrape publisher websites, acquire a vendor subscription, confirm a lineup/injury or identify an official event.

- Scope: all 13 sports recognized by the existing web intelligence sport profiles.
- Source classification: PUBLIC_WEB_RESEARCH (not league-official verification).
- Research authority only: prediction_authority=false, can_execute=false.
- No changes to LLP team/event probabilities, player props, calibration, eligibility, terminal reducer or betting.
- Story publication timestamps are publisher claims, NOT proven pre-lock availability. All stories have historical_pregame_eligible=false until a separate identity/chronology verification.
- provider_event_id is never silently promoted to official_event_id.
- Headline/summary are truncated. No full publisher article text is stored.
- Same-content snapshot IDs are stable across retries, but the existing snapshot store updates observed_at. This path does NOT by itself provide immutable historical first-seen evidence and cannot certify backtests.

## Opt-in configuration

Set the following in the existing Scout GitHub Actions environment ONLY after verifying selected-feed rights permit automated access, bounded storage and this research use.

| Key | Storage | Example |
|---|---|---|
| WOW_NEWS_FEED_ENABLED | Actions variable | true |
| WOW_NEWS_FEED_ALLOWED_DOMAINS | Actions variable | feeds.example.org |
| WOW_NEWS_FEED_TARGETS_JSON | Actions variable | [{"sport_key":"basketball_nba","provider":"LICENSED_NEWS","url":"https://feeds.example.org/sports.json"}] |
| WOW_NEWS_FEED_LICENSE_ACK | Actions variable | true |
| WOW_NEWS_FEED_API_KEY | Encrypted Actions secret, optional for public permitted feeds | Never commit |

The example endpoint is NOT a real verified feed; do not enable it. The license ACK is an operator attestation, not legal approval. The adapter currently supports Bearer API tokens only. Providers with other auth schemes need separately reviewed adapters.

WOW_SCOUT_DATABASE_URL is required to persist evidence. The existing Scout scheduled workflow additionally requires WOW_SPORTSDATAIO_API_KEY at its current preflight; without it the refresh step (including news) is SKIPPED. That limitation is tracked in #1484 / #1452 and is not resolved by this patch. No new schedules, billing, subscriptions or service credentials are created.

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

All URLs must be HTTPS; feed endpoints must match the operator allowlist. Payload capped at 512000 decoded bytes, maximum five targets and 40 articles per target. Future, timestamp-free, duplicate, malformed or unsafe article URLs are excluded. Redirects are not followed. Failed optional news does not alter the structured-source status. An enabled but empty target list yields CONFIG_INVALID, not a healthy feed.

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
