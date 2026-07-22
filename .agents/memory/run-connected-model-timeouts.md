---
name: run-connected-model timeout fixes
description: Three-layer fix for gunicorn WORKER TIMEOUT on POST /run-connected-model; Anthropic, psycopg2, and gunicorn.
---

## The Rule
`POST /run-connected-model` runs a multi-stage pipeline (LLP team analysis → Anthropic audit → DB postmortem). Any stage without an explicit timeout can exceed gunicorn's worker watchdog, yielding a 500 to the caller.

## Three-layer fix applied
1. **Anthropic call** — `_cm_claude_call()` (line ~11121): `Anthropic(api_key=..., timeout=90.0)`. Raises `APITimeoutError`, propagates as "failed" audit → graceful fallback. Configurable via `CM_CLAUDE_TIMEOUT_S` env var.
2. **psycopg2** — `get_db_conn()` (line ~155): `psycopg2.connect(database_url, connect_timeout=10)`. Prevents indefinite block on a stale/refused DB socket.
3. **gunicorn watchdog** — `artifact.toml`: `--timeout 240` (was 120). Gives the full pipeline (odds API + ESPN + Claude) enough headroom even on slow external responses.

**Why:** gunicorn sync workers are killed by the master after `--timeout` seconds with no response. The Anthropic SDK had no default timeout — a slow or hung API call blocked the worker until the master killed it, returning 500 to the caller.

**How to apply:** Any new external API call added to a gunicorn-served route must set an explicit timeout. Rule of thumb: external timeout < (gunicorn timeout − 60s overhead).
