---
name: Odds API → TheRundown failover in get_h2h_odds
description: Failover chain added to services/odds_api.py so quota exhaustion or invalid key on The Odds API transparently falls through to TheRundown without any consumer changes.
---

## What was built

`services/odds_api.py` — `get_h2h_odds(sport_key)` now has a two-provider failover chain:

1. **The Odds API (primary)** — native `bookmakers → markets → outcomes` shape.
2. **TheRundown (failover, 429 or 401 only)** — response normalized via `_normalize_rundown_to_h2h_events()` into the identical Odds API shape; every downstream consumer (`_books_from_odds_api_event` in consensus_odds.py) works with zero changes.

## Failover trigger conditions (narrow on purpose)

Only these two Odds API statuses trigger the fallback:
- `"FAILED: quota exhausted"` (HTTP 429)
- `"FAILED: invalid ODDS_API_KEY"` (HTTP 401)

Transient failures (timeout, 5xx, network errors) do NOT trigger failover — those conditions typically affect both providers, and burning a second quota hit only adds latency.

**Why:** NOT_CALLED (no key set) and transient errors would wastefully burn TheRundown quota with zero chance of success.

## Normalizer: _normalize_rundown_to_h2h_events(rundown_events)

Converts TheRundown's moneyline shape into Odds API bookmakers/markets/outcomes:

| TheRundown field | Odds API field |
|---|---|
| `teams_normalized[0].name` | `home_team` |
| `teams_normalized[-1].name` | `away_team` |
| `event_date` | `commence_time` |
| `lines[aff_id].moneyline.moneyline_home` | `bookmakers[].markets[].outcomes[].price` (home) |
| `lines[aff_id].moneyline.moneyline_away` | `bookmakers[].markets[].outcomes[].price` (away) |
| `lines[aff_id].moneyline.date_updated` | `bookmakers[].last_update` + `markets[].last_update` |

Affiliate key format: `"rundown:<affiliate_id>"` (keeps source traceable in logs).

Integrity rules: affiliates missing either home or away price are silently dropped (never fabricated). Events with < 2 teams are skipped.

## Other fixes in the same patch

- `_get()` now reads `ODDS_API_KEY` from `os.environ` dynamically at call time (not module-load constant) → key rotation takes effect without a process restart.
- `_get()` now copies the `params` dict before mutation (`dict(params or {})`) so the caller's dict is never mutated (was a latent bug).

## Tests

`services/tests/test_odds_api_failover.py` — 31 tests, all passing:
- `TestNormalizeRundownToH2hEvents` (12) — normalizer correctness, edge cases, multi-affiliate
- `TestSportKeyToName` (3) — reverse map round-trip for all sports
- `TestGetH2hOddsFailover` (11) — primary success, failover on 429/401, NO failover on timeout/5xx/NOT_CALLED, empty/failed TheRundown, unknown sport key, end-to-end shape parseable by `_books_from_odds_api_event`
- `TestRundownFailoverStatuses` (5) — trigger set completeness

## How to apply

Any new odds-service function that should fail gracefully should follow this pattern:
1. Try primary, check `data is not None` (not truthiness — empty list is a valid response)
2. Inspect status string for specific terminal failure codes before attempting fallback
3. Normalize secondary provider shape to match the primary shape before returning
4. Use deferred `from services import rundown` inside the failover branch to avoid circular import risk at module load time.
