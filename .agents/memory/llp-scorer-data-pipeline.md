---
name: LLP scorer/scanner data pipeline reality
description: What the WOW scorer (/wow/l10/v2) and scanner (/wow-daily-scan) actually return, plus which live data sources work from this host.
---

# WOW scorer & scanner — audited behavior

## Endpoints
- Scorer: `GET /wow/l10/v2` (NO api key) — params `player,sport,prop,line,direction,year,season,mlb_season,hltv_id,nocache`. Returns a per-game `games[]` **ledger** + derived `l10_avg/l10_median/l5_avg`, `source`, `pulled_at`, `rows`, `complete`, `gap`, and `confidence_tier`.
- Scanner: `POST /wow-daily-scan` (X-API-Key = SCORING_API_KEY) — body `{sports,environment,limit_per_sport,async}`. Returns `source_access_status`, `execution_report`, `execution_notes`, `missing_sports`, `scan_valid`, classified pools.

## Strict input vocabulary (loose names silently REJECT)
- Prop names must be canonical/capitalized: `Points` works, `points` → REJECT `gap:"Prop 'points' not in NBA column map"` (see `_NBA_COLS`).
- MLB sport key is `mlb_batter` / `mlb_pitcher`, NOT `mlb` (→ "Unknown sport").
- CS2 needs `hltv_id`; even with it, it short-circuits to REJECT ("Cloudflare blocks automated fetch") — CS2 is manual-only by design.

## Live source reality from this host (verified 2026-06-06)
- `nba_api` (stats.nba.com) WORKS → full 10-row current-season ledger, `FINAL LOCK ELIGIBLE`.
- baseball-reference.com does NOT respond to this server → MLB-batter AND WNBA (both bbref-backed) return REJECT with a fetch-failed `gap`. So anything not on nba_api/statsapi is effectively manual-or-REJECT.
- **The engine never fabricates**: missing data → `rows:0` + `REJECT — INSUFFICIENT DATA` + `gap` + source still labeled; scanner → `scan_valid:false`/`missing_sports`. Fail-honest.

## Provenance + normalization (added to /wow/l10/v2)
`confidence_tier` still labels *completeness* (FINAL LOCK ELIGIBLE / CONDITIONAL — L5 ONLY / WATCH / RESEARCH ONLY / REJECT — INSUFFICIENT DATA). The per-prop scorer now ALSO returns:
- `data_quality` — 4-way provenance (Verified / Manually Reconstructed / Proxy Only / Missing) derived from `source`+`complete`. Additive; does NOT replace `confidence_tier`.
- `normalization_log` — always present; loose inputs are mapped to real column-map keys before dispatch (`points`→`Points`, `sport=mlb`+`hits`→`mlb_batter`+`Hitter Hits`). On cache hits the log reflects the CURRENT request, not the first cacher.
- `reject_reason` on REJECTs — INPUT_FORMAT_ERROR (bad prop/sport/player identifier) / FETCH_FAILED (source unreachable, tooling missing, Cloudflare/manual) / INSUFFICIENT_DATA (fetch ran, too few rows). Classified from the `gap` string; identifier errors checked before the source-unreachable bucket.

New route `GET /wow/health` (no auth) probes each sport source → per-sport Available/Degraded + overall UP/PARTIAL; cs2 is "Manual Only — permanent" and excluded from the overall calc.

## Fail-soft rule for external fetchers
**Why:** statsapi/ESPN can return HTTP 200 with an unexpected JSON shape (list/scalar nesting, schema drift). Guarding only the HTTP call is not enough — the *payload traversal* can still raise and bubble out of `_llp_analyze_one` as a 500.
**How to apply:** every LLP context fetcher must (a) bound its HTTP timeout, (b) `isinstance`-guard each nested container before `.get`, and (c) wrap the whole traversal in a try/except that returns the unverified shell. Acceptance for any new fetcher: feed it malformed-but-200 shapes and confirm it never raises.
