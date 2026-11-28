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


### November 20, 2026 — compact same-run active-slate selection

Observed proof: scheduled [Multi-Scout run #44789095413](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/44789095413), descendant of all merged news repairs, emitted a real `SOURCE_FETCH_OK`
GDELT receipt at 2026-11-20 18:17:02 UTC: NHL, 11 article
URLs, one request, HTTP 200. However it declared
`selection_policy=SLATE_UNAVAILABLE_FIXED_ROTATION`,
`slate_active_event_counts={}`, even though that exact run's early
discovery artifact contained 298 team-event candidates. This is because
its original `model-handoff.json` is roughly **596 MB** due to large
market evidence: the legacy GDELT loader refuses a manifest over 64 MB,
then silently uses an unrelated fixed sport rotation.

The existing Scout script now emits a *second, bounded event-only* file
`news-active-slate.json` alongside the full model handoff. It contains
sport, event start, provider event key for **sampling/deduplication only**,
and the distinct verified official-ID field only when independently
established; it excludes all bookmaker quotes, prices and irrelevant
candidate payloads. The GDELT runner detects oversized full manifests and
reads this tiny same-run sibling, while still making exactly **one** public
GDELT request. It emits explicit `slate_manifest_source` and
`slate_manifest_status` fields so a missing, invalid, or oversized
compact sibling is a typed selection failure, never a fake active-slate
success. The fallback sport rotation is labeled honestly.

This does not verify provider event IDs as official fixtures, increase
sports coverage to all 13 sports per run, independently authenticate a
publisher, produce a fitted feature or establish news-triggered specialist
rescoring. Only end-to-end evidence and independent QA can accept those
distinct objectives. It remains research only; `can_execute=false`.


## November 24, 2026 — user-readable public research newsfeed

Existing research collection now publishes a **bounded human-readable newsfeed** without
a new paid provider, scheduler, credential, crawler, or inferred model feature.

- `wow-v17-scout-source-refresh` continues four existing scheduled acquisition
  attempts per day. Each emits `gdelt-public-news/receipt.json` plus
  **`newsfeed.json` and `newsfeed.md`** in the existing
  `wow-v17-gdelt-public-news-<run>-<attempt>` artifact.
- `wow-v17-nightly-multiscout` makes its normal single same-run GDELT
  query after compact slate selection, then publishes a **separate, small**
  `wow-v17-public-newsfeed-<run>-<attempt>` artifact containing
  `public-gdelt-receipt.json`, `public-newsfeed.json` and
  `public-newsfeed.md`. The same files are preserved in early discovery
  recovery evidence. Artifacts retain the workflow's 90-day expiration.
- The renderer records the SHA-256 of the **actual discovery receipt bytes**,
  source code, timestamps, sampled sport, deduplicated headline groups,
  source URLs, syndicated source instances, and explicit coverage states for
  all 13 sports. HTML/Markdown-special headline characters are escaped.
  No story bodies are copied. A missing, blocked, stale or invalid source
  yields an explicit blocked newsfeed, **never a fabricated headline**.
- The renderer has **no source fetch, extra API request or DB write**.
  This is a **single-sport sample per run**, not a comprehensive daily
  13-sport newsroom. The scheduled four polls, GDELT quota limitations,
  and source-provider rights remain unchanged.
- Neither an article title nor publication-date *claim* means it is a league,
  team, or verified injury fact. Only the existing independent reviewed-fact
  producer with exact authoritative event mapping may request a fitted
  rescore. No unreviewed discovery can become a probability or alter weights.
  `can_execute=false` and the global V17 terminal reducer are untouched.

### Reading the newsfeed
From GitHub Actions, open either workflow, select its **latest actual run**,
and view/download its named news artifact. Read `newsfeed.md` for headlines
and `newsfeed.json` for status and exact source SHA. Check `status` and
`sampled_sport` before using any headline; never call an `OK` workflow
a recovered source if the source JSON says `SOURCE_RATE_LIMITED`.

**Final product acceptance** additionally requires a genuine recent
source receipt, a run with nonempty valid headlines, independently verified
publisher facts for material changes, a source-rights-approved durable
persistence strategy if used as model data, and a fresh, immutable
specialist+terminal receipt for *model* publication. A green CI test,
artifact upload, or GDELT headline receipt is not that acceptance.


## November 24, 2026 — GKG dump fallback + active-season sampling

Live DOC ArtList probes and scheduled Actions receipts continue to show
`SOURCE_RATE_LIMITED` / `SOURCE_TIMEOUT` with zero articles on shared egress.
GDELT's own 429 body points high-traffic users at downloadable datasets.
The GDELT Project ToU already covers those public dumps with attribution.

**Engineering repair (acquisition path only):**

1. Keep exactly one DOC ArtList request per run (no same-run DOC sleep/retry).
2. On `SOURCE_RATE_LIMITED` or `SOURCE_TIMEOUT` only, attempt a **same-provider**
   fallback: read `https://data.gdeltproject.org/gdeltv2/lastupdate.txt`, then
   download at most two recent `*.gkg.csv.zip` slices (walk back 15-minute
   stamps when the listed pointer 404s). Extract HTTPS `DocumentIdentifier`
   URLs and `<PAGE_TITLE>` values matching the selected sport. Never download
   publisher article bodies.
3. Receipt fields: `acquisition_path` (`GDELT_DOC` or
   `GDELT_GKG_DUMP_FALLBACK`), `fallback_status`, and per-attempt `channel`.
4. Fixed source-refresh rotation now prefers **active-season** sports for the
   current calendar month so four daily polls cover live leagues sooner.
   Off-season sports remain in inventory as `NOT_SAMPLED_THIS_RUN` unless
   explicitly requested. Multi-Scout compact-slate selection is unchanged.

This does **not** enable ESPN/FOX/NewsAPI. Those remain rights-gated candidates.
It does **not** authenticate publisher facts, map official events, or trigger
fitted-specialist rescoring. Independent Verification must still confirm a
scheduled nonempty artifact after merge. `can_execute=false`.
