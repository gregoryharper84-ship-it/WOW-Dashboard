---
name: Cross-worker Odds API quota fix — 100K failover
description: Four isolated call sites bypassed the key ladder; proactive quota skip added to both _odds_api_request and _get().
---

# Cross-worker Odds API quota: 100K key failover

**Commit:** f6caf4c — no patch registry entry (infrastructure fix, not a scoring gate).

## Root cause
`ODDS_API_KEY_100K` was in `_odds_api_request()`'s ladder (`"high"` tier) but **four** call sites in `app.py` used `os.environ.get("ODDS_API_KEY", "")` directly, bypassing the ladder entirely. If the legacy `ODDS_API_KEY` is deactivated or exhausted, these endpoints fail even when the 100K key is healthy.

## Four call sites fixed
- `wow_tennis_matchups` (line ~6899) — raw `requests.get` with legacy key
- `_get_cross_book_variance()` (line ~9396) — raw `requests.get` with legacy key
- `_llp_fetch_odds_events()` (line ~16517) — LLP odds cache
- Odds API health check (line ~11677) — checked `_odds.ODDS_API_KEY` (module-level constant)

All now use `resolve_odds_api_key_with_source()` from `services/odds_api.py`.

## Proactive quota skip
Before every HTTP call in `_odds_api_request()` and `_get()`:
- `remaining == 0` in store → skip that tier's HTTP call, move to next tier.
- `remaining is None` → no skip (unknown, not zero; fail-open).
- DB error in `_get()` → fail-open, no proactive skip, reactive fallback still works.

## Key tier names in pg_odds_quota / _ODDS_QUOTA_STORE
```
ODDS_API_PAID_KEY → 'paid'
ODDS_API_KEY_100K → 'high'   ← 100K key; tracks quota under this name
ODDS_API_FREE_KEY → 'free'
ODDS_API_KEY      → 'legacy'
```
`_odds_quota_update("high", ...)` is already called by `_odds_api_request()` when the 100K key is the one that succeeds.

**How to apply:** Any new Odds API call must use `resolve_odds_api_key_with_source()` or `_odds_api_request()` — never `os.environ.get("ODDS_API_KEY", "")` directly.
