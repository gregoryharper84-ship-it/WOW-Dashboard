---
name: stats.nba.com blocking + nba_api parameter names
description: stats.nba.com is unreachable (consistent 20s+ timeout) from this legacy platform host; also documents the correct nba_api LeagueDashTeamStats kwarg name.
---

`nba_api`'s `LeagueDashTeamStats` (and likely other stats.nba.com-backed endpoints) consistently time out from this host — confirmed with both a 10s and a 20s explicit timeout, same result both times, via two independent test paths (the app's own health route and a standalone script).

**Why:** stats.nba.com is known to throttle or block requests from cloud/datacenter IPs. This is a real, reproducible upstream connectivity issue, not a code bug or a transient blip (contrast with Open-Meteo's one-off 503).

**How to apply:** any code path depending on `nba_api` live calls (not the offline `nba_api.stats.static` data) should be expected to fail from this environment until proven otherwise — always report the failure honestly (existing codebase convention already does this via `{"error": ...}` dicts, never fabricates). Do not assume a live nba_api call will succeed just because the package is installed.

Also: `nba_api.stats.endpoints.leaguedashteamstats.LeagueDashTeamStats.__init__` takes `per_mode_detailed`, not `per_mode_simple` — the latter doesn't exist in the installed version and raises `TypeError`. Check `inspect.signature(...)` before assuming a kwarg name if copying from older nba_api examples/docs.
