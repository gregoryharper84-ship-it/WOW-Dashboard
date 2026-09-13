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

## The mistake this patch corrects — read this first

The first cut chose Odds-API-v4 as the internal interchange shape (correct) and
then **assumed the providers already looked close enough to it** (wrong). It
shipped a strict Odds-API-v4 validator plus a TheRundown **V1** translator, and
neither provider actually returns either shape today. The whole test suite was
green because the fixtures were written from the module's own shapes.

The rule: **Odds-API-v4 is the interchange, not an assumption about upstream.**
Every provider gets a native adapter, and the provider disappears at that
boundary.

```
SharpAPI native (row-major)      TheRundown V2 native (market-major)
        |                                    |
  SharpAPI adapter                  TheRundown V2 adapter
        \__________     ______________________/
                   \   /
             Odds-API-v4 internal shape
                     |
     Scout / bookmaker_rows / cross-book / dedupe (unchanged)
```

### Documented native structures

- **TheRundown V2** (`/api/v2/...`, both `events` and `openers`):
  `event -> markets[] -> participants[] -> lines[] -> prices{affiliate_id}`.
  Market-major, so the adapter regroups by book because Odds-API-v4 is
  book-major. Handled by `rundown_v2_event_to_odds_api_v4()`.
- **TheRundown V1** (legacy): `event.lines{affiliate_id}.{moneyline,spread,total}`.
  Still handled by `rundown_event_to_odds_api_v4()` for the V1 endpoints.
- **SharpAPI**: row-major — one record per sportsbook/event/market/selection
  with `odds` and `line` fields, plus a line-shopping variant nesting competing
  books under `all_books`. Handled by `sharpapi_rows_to_odds_api_v4()`, which
  groups event -> sportsbook -> canonical market -> outcomes.

`coerce_odds_api_v4_event()` remains as a strict *validator* for anything that
genuinely already presents `bookmakers[].markets[].outcomes[]`; it is the last
fallback, never the primary path.

## Why response fields are still never guessed

Neither `api.sharpapi.io` nor `therundown.io` is reachable from the Claude Code
remote environment (both return `EGRESS_BLOCKED` / `CONNECT tunnel failed, 403`
at the network egress proxy), so no live response can be inspected here.

Adapters therefore accept the documented field names plus their common
aliases, and **drop** any row whose event identity, market, selection or price
cannot be resolved. Anything that resolves to zero usable rows returns
`<PROVIDER>_SCHEMA_UNRECOGNISED` with a `schema_probe`. Nothing is defaulted.

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
- **TheRundown numeric market ids.** Markets are matched by *name*; ids can be
  pinned with `WOW_RUNDOWN_MARKET_ID_MAP_JSON` (e.g. `{"3": "h2h"}`). No
  numeric market id is hard-coded.
- **SharpAPI league identifiers.** Explicit map in `SHARPAPI_SPORT_LEAGUES`,
  overridable per sport with `WOW_SHARPAPI_LEAGUE_<SPORT_KEY>`. An unmapped
  sport fails closed before any call rather than querying a default league.
- **TheRundown's date `offset`.** The docs show `offset=300` for a US Central
  date boundary. That is derived from the compiled zone
  (`WOW_USER_TIMEZONE`, default `America/Chicago`) rather than pinned, so the
  slate day stays correct across DST: 300 in CDT, 360 in CST.
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

Plus the aggregate evidence-side terminal, `MARKET_DATA_UNOBTAINABLE`.

**Keep the failure boundary clean.** Losing a market feed removes *evidence*,
not a *model*. A market-acquisition failure must never on its own convert an
otherwise valid fitted sporting probability into `MODEL_UNAVAILABLE` — V17
preserves sporting probability when downstream market evidence fails. So:

| condition | terminal |
| --- | --- |
| provider unreachable / rejected / disabled / unconfigured | `MARKET_DATA_UNOBTAINABLE` (or the specific provider code) |
| provider returned something no adapter understands | `<PROVIDER>_SCHEMA_UNRECOGNISED` |
| no fitted probability capability exists for the lane | `MODEL_UNAVAILABLE` — and nothing in this module can cause it |

The snapshot asserts `affects_fitted_model_availability: false` and the string
`MODEL_UNAVAILABLE` never appears in its output.

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
artifact, and a separate `market-evidence-acceptance` job that runs a
**credentialed live call** and fails unless every configured provider returns
`captured_rows > 0`. Fixtures cannot prove an adapter matches a real provider
response; only that job can. It skips cleanly when no key is configured, and
uploads the structural probes alongside the result. It covers today and the next slate, and reconciles
`lanes_requested == lanes_captured + lanes_blocked`.

Note the tension worth remembering: `SPORTSBOOK_FEED` carries a 15-minute
freshness ceiling in `scout_source_policy`, so the daily snapshot is
opener/CLV evidence. Anything needing *live* market evidence must go through
the on-demand bridge path, not the nightly file.

## Tests — two files, and the distinction matters

`tests/test_v17_market_evidence_provider_contracts.py` (18 tests) holds
fixtures built from each provider's **documented native structure** — V2
market-major, V1 legacy, SharpAPI row-major, and `all_books` line shopping.
These are the tests that would have caught the original defect. Any adapter
change must add a fixture here, not only to the file below.

`tests/test_v17_market_evidence_sources.py` (33 tests):
governance markers, no-probability-field assertion, canonical key mapping,
schema-unrecognised fail-closed + value-free probe, 401/403/429/5xx typing,
credential redaction, sport-id resolution vs. pinning, row completeness through
`bookmaker_rows`, cross-provider book-key collision, snapshot reconciliation,
and binding to the `SPORTSBOOK_FEED` policy rule.
