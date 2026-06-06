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

## Spec delta to remember
`confidence_tier` labels *completeness* (FINAL LOCK ELIGIBLE / CONDITIONAL — L5 ONLY / WATCH / RESEARCH ONLY / REJECT — INSUFFICIENT DATA), NOT the spec's 4-way *provenance* taxonomy (Verified / Manually Reconstructed / Proxy Only / Missing). Provenance only lives on the scanner's `source_access_status`, not on the per-prop scorer.
