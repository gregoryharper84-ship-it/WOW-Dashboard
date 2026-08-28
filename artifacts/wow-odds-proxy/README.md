# WOW Secure Odds API Proxy

Read-only credential proxy for The Odds API v4.

## Purpose

Custom GPT Actions cannot safely inject The Odds API's required `apiKey` query
parameter as a hidden secret. This service keeps the upstream vendor credential
on Render and exposes only three allowlisted GET capabilities:

- current events for one sport key
- available market keys for one event
- exact odds for selected markets on one event

There is no generic URL proxy and no write method. This is acquisition-only and
never places, routes, modifies, cancels, or approves a wager. `can_execute=false`.

## Environment variables

Configure these in Render. Never put their values in GitHub, GPT Instructions,
Knowledge, chat, logs, URLs, or screenshots.

- `ODDS_API_KEY` — The Odds API vendor key. Server-side only.
- `WOW_ODDS_PROXY_ACTION_KEY` — separate Bearer secret used by the GPT Action.

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

Keep the existing direct `api.the-odds-api.com` Action until all proxy Preview
smoke tests pass. Then remove only the direct Odds Action.

## Acceptance tests

1. `GET /odds-api/health` returns `status=ok`, `read_only=true`,
   `can_execute=false`.
2. Protected routes reject missing/wrong Bearer auth.
3. With valid proxy auth but no vendor key, protected routes return fail-closed
   `ODDS_API_KEY_UNCONFIGURED`.
4. `getOddsProxyEvents` returns current event identity without exposing the
   upstream key.
5. `getOddsProxyEventMarkets` returns bookmaker market inventory.
6. `getOddsProxyEventOdds` returns exact event odds and passes through only
   quota headers (`x-requests-remaining`, `x-requests-used`, `x-requests-last`).
7. Unknown query fields are discarded rather than forwarded.
8. No POST/PUT/PATCH/DELETE route exists.
9. OpenAPI exposes no vendor-key parameter.

## Local test

```text
cd artifacts/wow-odds-proxy
pip install -r requirements.txt
pytest -q
openapi-spec-validator openapi.custom-gpt.yaml
```
