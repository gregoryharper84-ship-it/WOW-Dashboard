---
name: MLB pitcher endpoint data sources
description: Which external data sources work vs fail for /wow/mlb/pitcher, and the fallback chain for leash score and opponent K%.
---

## Rule
BBRef (`baseball-reference.com`) and FanGraphs are both blocked from legacy platform's server IPs. Use MLB Stats API + Statcast proxies instead.

**Why:** BBRef returns fetch-failed (connection refused / 403). FanGraphs returns 403 for both the HTML leaderboard and via `pybaseball.team_batting()` (it calls `leaders-legacy.aspx`). Verified June 2026.

## How to apply

### Leash score (innings pitched per start)
- Primary: `_l10_bbref` — only works locally, fails on legacy platform.
- Fallback: `_leash_score_from_statcast(first, last)` — groups `statcast_pitcher` by `game_date`, uses `max(inning)` per game as IP proxy, feeds `_leash_score()`.
- In the endpoint: `if bbref_log: leash = _leash_score(bbref_log) else: leash = _leash_score_from_statcast(first, last)`.

### Opponent K%
- Primary: `statsapi.mlb.com/api/v1/teams/stats?season=YYYY&sportId=1&stats=season&group=hitting`
  - Returns all 30 teams; compute K% as `strikeOuts / plateAppearances`.
  - Match team name with `_FG_TEAM_ALIASES` dict.
- Fallback: `pybaseball.team_batting()` (403 from FanGraphs but may be unblocked in future).
- Final fallback: FanGraphs HTML scrape (also 403 currently).

### WOW game object key names
`_l10_bbref` returns game objects with `context` (= the ctx_col, which is `IP` for mlb_pitcher) and `value` (= the stat for the prop). **Not** `IP` or `Pit` directly. Update all helper functions that read raw BBRef rows to check `row.get("context") or row.get("IP")` and `row.get("value") or row.get("Pit")`.

### Savant data
`_get_pitcher_savant` uses `pybaseball.statcast_pitcher` — works fine.

## Verified
Two regression tests pass after these fixes:
- Abbott LESS 5.0 Ks → GATE_KILL (leash=3/LONG, REJECT_ONE_K_MARGIN_TRAP, WATCH_TIGHT_K_LESS_LINE)
- Skenes MORE 7.5 Ks → GATE_PASS (leash=4/MEDIUM, efficiency=null, opp_k=0.2479)
