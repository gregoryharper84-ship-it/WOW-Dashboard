# TheRundown Flask Proxy — Patch Instructions for Claude

## What's new on the Flask side (already deployed)

Two new routes that keep `RUNDOWN_API_KEY` server-side:

| Route | Returns | Notes |
|---|---|---|
| `GET /rundown/sports` | Sport directory (`{sport_id, sport_name}` list) | 1hr cache |
| `GET /rundown/events/<sport_id>/<YYYY-MM-DD>` | Events array (with lines if plan grants access) | **5min cache** (lines move fast); empty responses are not cached |

### Response contract (matches Claude's v2 client)

| Upstream condition | HTTP | Body shape |
|---|---|---|
| Lines available | `200` | `{ ok: true, source: "therundown", events: [...], count: N }` |
| Plan limit (upstream 401) | `200` | `{ ok: true, source: "therundown", events: [], count: 0, fallback_hint: true, reason, hint }` |
| No slate that day (upstream 404) | `200` | `{ ok: true, source: "therundown", events: [], count: 0 }` |
| Bad date format | `400` | `{ ok: false, error: "date must be YYYY-MM-DD" }` |
| Other upstream / network errors | `502` | `{ ok: false, step, status, body }` |

The 200 + `fallback_hint: true` handshake is the key piece: the browser sees a clean response (no network error noise), the v2 client logs one warning line and drops straight to the Odds API tier.

Both require the same `X-API-Key: $SCORING_API_KEY` header all the other Flask routes use.

## Sport IDs (live-confirmed from `/rundown/sports`)

```
NFL=2  MLB=3  NBA=4  NHL=6  UFC/MMA=7  WNBA=8
MLS=10  EPL=11  FRA1=12  GER1=13  ESP1=14  ITA1=15
UEFA-Champions=16  UEFA-Europa=33
NBA-Playoffs=24  NHL-Playoffs=28  MLB-Playoffs=31
TENNIS-ATP=38  TENNIS-WTA=39
```

**Claude's earlier WNBA correction (id=8) is verified correct.** UFC is id=7.

## Current plan status

Probed live: `RUNDOWN_API_KEY` works for `/sports` (directory) but returns **401 on every `/sports/{id}/events/{date}` call**. This is a plan limitation, not a code bug. The proxy returns a clean 401 JSON that tells the client to fall through to the Odds API tier — exactly matching the fallback chain the three-tier patch was designed for.

When the TheRundown plan is upgraded, real lines start flowing with **zero client changes**.

## Two-line patch to Claude's `fetchTeamEvents` helper

In the Tier 1 (TheRundown) block, swap the direct call for the Flask proxy:

### BEFORE
```js
const url = `${EP.rundown}/sports/${trdId}/events/${dateStr}`;
const r = await fx(url, { headers: { 'X-TheRundown-Key': KEYS.rundown } }, 10000);
```

### AFTER
```js
const url = `${EP.replit}/rundown/events/${trdId}/${dateStr}`;
const r = await fx(url, { headers: { 'X-API-Key': KEYS.replit } }, 12000);
```

And in the response parsing, the proxy wraps events under `data.events` (already what the existing code expects), so the `raw = data.events || data.data || []` line still works unchanged.

## Security win

This removes `KEYS.rundown` from client-side JS entirely. You can delete the `KEYS.rundown` constant from the HTML once both LLP and WU call sites are switched over.

## Smoke-test commands (run from a terminal, optional)

```bash
# Directory (should return 30+ sports)
curl -s "$REPLIT_DEV_DOMAIN/rundown/sports" -H "X-API-Key: $SCORING_API_KEY" | jq .count

# WNBA events today (should return 401 with clean "plan limit" JSON until upgrade)
curl -s "$REPLIT_DEV_DOMAIN/rundown/events/8/2026-05-24" -H "X-API-Key: $SCORING_API_KEY"
```

## Once the plan is upgraded

No code change needed. The 1hr cache will fill with real events, and `fetchTeamEvents` will stop falling through to the Odds API tier. You'll see the source counter shift from `oddsapi` to `therundown` in the engine log.
