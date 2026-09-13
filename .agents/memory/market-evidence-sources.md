---
name: Cross-book market evidence sources (SharpAPI + TheRundown)
description: Research-only SPORTSBOOK_FEED tier for WOW V17 Scout — providers, fail-closed codes, schema-probe workflow, and why no response field names were guessed
---

## What this is

`artifacts/wow-engine/v17/market_evidence_sources.py` is a sibling of
`scout_secondary_source.py`. It adds a **tertiary** acquisition tier for Scout:

```
primary Odds API proxy
  → ESPN secondary research feed (scout_secondary_source)
    → subscription market feeds (market_evidence_sources)   <-- this patch
```

Providers: `SHARPAPI` (breadth across many books, `/api/v1/odds`, `X-API-Key`
header) and `RUNDOWN` (openers + market delta = the opener/CLV story,
`key` query param).

Governance ceiling is identical to every other research tier — evidence only.
Every emitted event carries `prediction_authority=False`,
`exact_line_authority=False`, `research_only=True`, `can_execute=False`.

## Why response schemas were not hard-coded from memory

Neither `api.sharpapi.io` nor `therundown.io` is reachable from the Claude Code
remote environment (both return `EGRESS_BLOCKED` / `CONNECT tunnel failed, 403`
at the network egress proxy), so no live response could be inspected while this
was written.

Rather than guess field names — which is exactly the FIX-C/FIX-D failure class
in `acquisition-routing-patch.md` — normalisation is **structural and
self-validating**:

- `rundown_event_to_odds_api_v4()` understands TheRundown's
  `events[].lines{affiliate_id}` shape with `moneyline`/`spread`/`total`
  sub-objects.
- `coerce_odds_api_v4_event()` is a strict *validator* for payloads that
  already present `bookmakers[].markets[].outcomes[]`.
- Anything matching neither returns `<PROVIDER>_SCHEMA_UNRECOGNISED` and **zero
  rows**, together with a `schema_probe`.

`structural_probe()` emits key names, container types and lengths only — never
values — so a probe can be pasted into a PR or a log without leaking a
credential or a price.

## Pinning an unverified schema

Run the probe from anywhere with egress (a GitHub Actions runner works):

```
python v17/market_evidence_snapshot.py --probe --provider RUNDOWN --capability sports
python v17/market_evidence_snapshot.py --probe --provider SHARPAPI --capability odds --sport-key baseball_mlb
```

The output is the shape, not the data. Use it to confirm or correct the
normaliser in a follow-up commit.

## Things deliberately not guessed

- **TheRundown numeric sport ids.** Resolved at runtime from the provider's own
  `/api/v1/sports` index, or pinned with `WOW_RUNDOWN_SPORT_ID_<SPORT_KEY>`.
  An unresolved sport is `MARKET_EVIDENCE_UNSUPPORTED_SPORT`, never a default.
- **Request paths.** Defaults are the paths actually observed/documented; every
  one is overridable via `WOW_<PROVIDER>_<CAPABILITY>_PATH`.
- **Market vocabulary.** Provider labels are translated through
  `canonical_market_key()` into `h2h`/`spreads`/`totals` before anything leaves
  the module. An unmapped market is dropped, not passed through raw.

## Configuration

| env | default | meaning |
| --- | --- | --- |
| `WOW_MARKET_EVIDENCE_ENABLED` | `false` | master gate; off means zero outbound calls |
| `RUNDOWN_API_KEY` / `WOW_RUNDOWN_API_KEY` / `THERUNDOWN_API_KEY` | — | TheRundown key |
| `SHARPAPI_API_KEY` / `WOW_SHARPAPI_API_KEY` | — | SharpAPI key |
| `WOW_MARKET_EVIDENCE_TIMEOUT_SECONDS` | `15` | per-request timeout |
| `WOW_RUNDOWN_SPORT_ID_<SPORT_KEY>` | — | pin a sport id, skips the index call |

Keys are read from the environment only. They are never written to a file, and
`fetch()` redacts the key out of every endpoint string it reports.

## Failure codes (all fail closed, zero rows)

`MARKET_EVIDENCE_DISABLED`, `MARKET_EVIDENCE_PROVIDER_UNKNOWN`,
`MARKET_EVIDENCE_CREDENTIAL_UNCONFIGURED`, `MARKET_EVIDENCE_ENDPOINT_UNCONFIGURED`,
`MARKET_EVIDENCE_PATH_PARAMETER_MISSING`, `MARKET_EVIDENCE_UNSUPPORTED_SPORT`,
`MARKET_EVIDENCE_CAPABILITY_UNSUPPORTED`, `MARKET_EVIDENCE_NO_ROWS`,
`MARKET_EVIDENCE_EVENT_NOT_FOUND`, `<PROVIDER>_HTTP_<code>`,
`<PROVIDER>_INVALID_JSON`, `<PROVIDER>_SCHEMA_UNRECOGNISED`.

A blocked provider never becomes a synthetic probability. Where a governed lane
needs a model it stays `MODEL_UNAVAILABLE`.

## Join safety

`market_evidence_scout_bridge._merge_books()` prefixes every book key with its
provider (`rundownmarketevidence__pinnacle`) so two providers quoting the same
book cannot collapse into one row. `nightly_multiscout._blocker_diagnostics`
gained the `tertiary_*` keys so the new tier's reason codes actually propagate
into the handoff (the FIX-A field-propagation class).

## Scheduling

`.github/workflows/wow-v17-nightly-multiscout.yml` gained a
`Capture research-only cross-book market evidence snapshot` step
(`continue-on-error: true`) that writes `market-evidence.json` into the run
artifact. It covers today and the next slate, and reconciles
`lanes_requested == lanes_captured + lanes_blocked`.

Note the tension worth remembering: `SPORTSBOOK_FEED` carries a 15-minute
freshness ceiling in `scout_source_policy`, so the daily snapshot is
opener/CLV evidence. Anything needing *live* market evidence must go through
the on-demand bridge path, not the nightly file.

## Tests

`artifacts/wow-engine/tests/test_v17_market_evidence_sources.py` (33 tests):
governance markers, no-probability-field assertion, canonical key mapping,
schema-unrecognised fail-closed + value-free probe, 401/403/429/5xx typing,
credential redaction, sport-id resolution vs. pinning, row completeness through
`bookmaker_rows`, cross-provider book-key collision, snapshot reconciliation,
and binding to the `SPORTSBOOK_FEED` policy rule.
