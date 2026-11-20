# WOW V17 Public News Discovery — Simple Design (research only)

**Single source:** [The GDELT Project](https://www.gdeltproject.org/), not GDELT Cloud.
GDELT Project grants free academic and commercial use of its datasets with
attribution and a link: https://gdeltproject.org/about.html#termsofuse.
The rights to underlying publisher article text are separate. This collector
downloads **no publisher articles**, stores only GDELT-discovered URLs, titles,
and indexed-at claims, and always credits the GDELT Project.

## One independent flow

Existing Scout source-refresh workflow -> one scheduled GDELT DOC 2.0 ArtList
request -> bounded response -> JSON receipt artifact retained 90 days.

- No commercial news vendor, license acknowledgement setting, API token, or
  additional paid service.
- No news-specific database, migrations, candidate identity linkage, model
  scoring, fitted-model certification, or new controller.
- Works even if Scout DB and paid SportsDataIO credentials are absent.
- One sport per UTC six-hour slot, rotating through all 13 supported sport
  categories in 13 runs (~3¼ days). This **does not** provide live complete
  news coverage for all 13 sports every day.
- Source failures retain a typed \`BLOCKED\` receipt (429, timeout, no
  articles, invalid shape); none can block the existing data refresh,
  predictions, or weekly Pick'em entry workflow.
- No article is automatically mapped to an official match or certified for
  historical pregame scoring. Every article records
  \`official_event_identity_verified=false\`,
  \`publisher_claim_verified=false\`, and
  \`historical_pregame_eligible=false\`.
- The HTTP response is read raw, without redirects or compressed content,
  with a 256 KB budget. Queries target only
  \`https://api.gdeltproject.org/api/v2/doc/doc\`.

## Files and verification

- \`v17/news_feed_research_adapter.py\`: GDELT-only fetcher, safeguards,
  normalization, exact attribution and typed errors.
- \`v17/public_gdelt_discovery.py\`: fixed 13-sport rotation and standalone
  machine-readable receipt. Optional \`--sport <canonical_sport_key>\` for
  focused manual verification.
- \`.github/workflows/wow-v17-scout-source-refresh.yml\`: reuses existing
  four daily runs and uploads news evidence independently of Scout DB.
- \`test_v17_news_feed_research_adapter.py\`: offline, deterministic tests for
  endpoints, credential non-use, redirect rejection, gzip bombs, size caps,
  official identity holds, rotation and DB independence.

Example local execution from \`artifacts/wow-engine\`:

\`\`\`bash
python -m pytest -q test_v17_news_feed_research_adapter.py
python v17/public_gdelt_discovery.py --sport basketball_nba --output /tmp/gdelt-news.json
\`\`\`

The second command makes a real public request and requires internet access.
A successful CI test verifies software safety, **not** actual source
availability or improved sports predictions.

## Release acceptance

Independent repository review still applies to protected merges; do not forge
a reviewer receipt or bypass branch protection. The sole acceptance scope is
the **public-data research reader** and receipt quality, not scientific
promotion of a new model. Review the exact commit for no credentials/no
publisher download, query bounds, failure isolation, GDELT attribution,
and nonprediction flags. After authorized merge, verify one actual scheduled
artifact. Any future licensed source must be a separately reviewed PR.

\`V17_TERMINAL_REDUCER\` remains sole terminal authority and
\`can_execute=false\`. No betting or Pick'em entry is submitted.


## November 18, 2026 — recovery status and limits

Production receipts from source-refresh run 44375341142 and Multi-Scout
44369679104 independently show \`SOURCE_RATE_LIMITED\` with zero articles.
The Multi-Scout bridge also reported \`NEWS_NO_MATCHING_SPORT\` and zero
candidates touched. The source-refresh job's structured acquisition was skipped
because \`WOW_SCOUT_DATABASE_URL\` was empty. Neither workflow-wide SUCCESS
nor a successful upload establishes an operational public news feed.

This repair retains typed health failure steps, article counts and 90-day
receipt retention. The earlier six-second retry was superseded by the
single-attempt fail-fast policy below; there is no in-run 429 retry. Nightly Multi-Scout
uses its own future canonical candidate manifest for one active-sport choice;
it never invents news, executes more than two GDELT requests per run, or turns
the discovery receipt into a verified publisher claim. The independent
source-refresh workflow retains its original fixed 13-sport rotation. This
does not certify same-day news coverage across active leagues.

The morning report includes a per-pick \`news_feed_audit\` with the source
status, latest independently verified timestamp (null unless independently
established), and \`model_rescore_verified=false\`. No headline can be
treated as a fitted scoring feature. True acceptance still requires actual
nonempty source receipts; authoritative publisher/team/league verification;
canonical event mapping; post-update fitted-specialist rerun with immutable
before/after hash and pregame receipt; protected release checks; and independent
production readback. \`can_execute=false\` remains enforced.

### November 20, 2026 — discard unproven long retry

The earlier prolonged block observations remain valuable as diagnostics,
not an identified recovery mechanism. During November 18 probing, requests
received HTTP 429 after 0, 45, 90, 135 and 735 seconds of silence.
A **single 200 with five article records** followed 688 seconds *and*
a possible egress change after a runtime restart. This does not establish
that a 900-second sleep resolves an IP block. The initial probe also
issued paired requests, confounding the apparent request intervals.
The proposed 900-second retry in the original PR was never shown to
work from the modified collector; later tests had timeouts.

The source refresh already runs every six hours. Instead of occupying a
worker for 15 minutes and making a second request in a blocked period,
`v17/public_gdelt_discovery.py` now makes **one bounded DOC request per run**.
When GDELT returns 429, the receipt records `SOURCE_RATE_LIMITED`,
`http_status=429`, `attempt_count=1`, `total_wait_seconds=0`,
`retry_deferred=true`, any `Retry-After` value and the normal
source-observed timestamp. The next scheduled run remains a new independent
attempt; this does not assert verified recovery. A timeout remains
`SOURCE_TIMEOUT`, never a successful news receipt.

The `Retry-After` header is parsed for observability but no scheduling
promise is made across workflow invocations: there is no persistent
cross-run rate-limit state. Since the parser caps `Retry-After` at one hour
and the nominal source refresh is six hours apart, ordinary schedule
spacing exceeds that bounded hint. An unusual manually dispatched run
could violate the interval; operators should not repeatedly dispatch
blocked sources.

**Still blocked until proven otherwise:** actual nonempty GDELT acquisition
on the current merged code, independent news fact verification, fresh exact
event identity linkage, and a genuine controlling fitted-specialist rescore
receipt. Workflow-level green alone never proves source health.
`model_feature_eligible=false`, `model_rescore_verified=false` and
`can_execute=false` stay unchanged.
