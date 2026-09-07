# WOW Secure Odds API Proxy

Read-only credential proxy for The Odds API v4.

## Purpose

Custom GPT Actions cannot safely inject The Odds API's required `apiKey` query
parameter as a hidden secret. This service keeps the upstream vendor credential
on Render and exposes only four allowlisted GET capabilities:

- active/available sport inventory
- current events for one sport key
- available market keys for one event
- exact odds for selected markets on one event

The sport-inventory route exists so autonomous V17 discovery can enumerate the
current feed instead of hard-coding leagues and silently missing NBA, NCAAB,
tennis, soccer, or other active sports.

There is no generic URL proxy and no write method. This is acquisition-only and
never places, routes, modifies, cancels, or approves a wager. `can_execute=false`.

## Environment variables

Configure these in Render. Never put their values in GitHub, GPT Instructions,
Knowledge, chat, logs, URLs, or screenshots.

- `ODDS_API_KEY` — The Odds API vendor key. Server-side only.
- `WOW_ODDS_PROXY_ACTION_KEY` — separate Bearer secret used by the GPT Action
  and authorized nightly Scout acquisition clients.

The two values must be different.

## Render service

Recommended service name: `wow-odds-proxy`

Build command:

```text
pip install -r artifacts/wow-odds-proxy/requirements.txt
```

Start command:

```text
cd artifacts/wow-odds-proxy && uvicorn api:app --host 0.0.0.0 --port $PORT
```

The service can be created without secrets; until both environment variables are
configured it fails closed. `/odds-api/health` reports only booleans indicating
whether configuration is present and never returns secret values.

## GPT Action

Use `openapi.custom-gpt.yaml`, replacing the server placeholder with the exact
Render HTTPS origin.

Authentication:

```text
Authentication: API Key
Auth Type: Bearer
API Key value: WOW_ODDS_PROXY_ACTION_KEY
```

Do **not** enter `ODDS_API_KEY` into the GPT editor.

## V17 Nightly Multi-Scout

`artifacts/wow-engine/v17/nightly_multiscout.py` consumes this proxy read-only.
It requests the dynamic active-sport inventory and then enumerates events,
market inventory, and every bookmaker exposed by the configured public feed in
the requested regions. Bookmaker coverage is explicitly reported; the system
must not claim coverage for a sportsbook the upstream feed does not expose.

Sportsbook odds are evidence only. They never become governed WOW/LLP model
probability.

## Acceptance tests

1. `GET /odds-api/health` returns `status=ok`, `read_only=true`, `can_execute=false`.
2. Protected routes reject missing/wrong Bearer auth.
3. With valid proxy auth but no vendor key, protected routes return fail-closed `ODDS_API_KEY_UNCONFIGURED`.
4. `GET /odds-api/v4/sports` returns active/available sport inventory without exposing the upstream key.
5. `getOddsProxyEvents` returns current event identity without exposing the upstream key.
6. `getOddsProxyEventMarkets` returns bookmaker market inventory.
7. `getOddsProxyEventOdds` returns exact event odds and passes through only quota headers (`x-requests-remaining`, `x-requests-used`, `x-requests-last`).
8. Unknown query fields are discarded rather than forwarded.
9. No POST/PUT/PATCH/DELETE route exists.
10. OpenAPI exposes no vendor-key parameter.

## Local test

```text
cd artifacts/wow-odds-proxy
pip install -r requirements.txt
pytest -q
openapi-spec-validator openapi.custom-gpt.yaml
```
