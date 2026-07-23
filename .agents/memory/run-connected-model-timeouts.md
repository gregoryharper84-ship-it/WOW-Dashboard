---
name: run-connected-model timeout fixes
description: Root-cause + three-layer fix for gunicorn WORKER TIMEOUT on POST /run-connected-model; Anthropic SDK retries, psycopg2, gunicorn watchdog, and SIGALRM wall-clock budget.
---

## Root Cause (confirmed from production logs, 2026-07-23)

The Anthropic Python SDK defaults to `max_retries=2`. With `timeout=90s`, the SDK
makes up to **3 attempts × 90s = 270s** before surfacing `APITimeoutError`. gunicorn's
watchdog fires at 240s and kills the worker process mid-retry — the `except` block never
runs, the client gets no response, gunicorn logs CRITICAL WORKER TIMEOUT, and the worker
is replaced (causing the next request to the dead worker to 500 until a new one spawns).

## Fix applied (as of 2026-07-23)

### Fix A — Anthropic SDK `max_retries=0` (line ~11279)
```python
client = _anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=0)
```
Now: 1 attempt, 90s timeout → raises `APITimeoutError` → caught by `_llp_run_claude_audit_team`
→ graceful "claude audit failed" fallback. Total Claude wall time: ≤ 90s.

### Fix B — SIGALRM wall-clock budget decorator (lines ~11215–11264, ~17450–17453)
`@_cm_wall_budget_decorator` wraps the entire `cm_run_connected_model` view.
- Default budget: 200s (`CM_RUN_BUDGET_S` env var overrides)
- Fires SIGALRM 40s before gunicorn's 240s watchdog
- On breach: raises `_CMRunWallTimeout` → caught by the decorator → returns clean 504
  `{"ok": false, "error": "request_timeout", "message": "..."}`
- `finally` always cancels the alarm so it never leaks to the next request
- Works because gunicorn sync workers run each request on the process main thread,
  which is the only thread that can receive SIGALRM

### Fix C — psycopg2 connect_timeout (line ~155)
```python
psycopg2.connect(database_url, connect_timeout=10)
```
Prevents indefinite block on a stale/refused DB socket.

### Fix D — gunicorn --timeout 240 (artifact.toml)
Gives the full pipeline enough headroom even on slow external responses (vs prior 120s).

## Rule
Any new external API call added to a gunicorn-served route must set an explicit `timeout=`.
Rule of thumb: **each call's timeout < (gunicorn_timeout / max_expected_call_count)**.
Never rely on SDK-level `max_retries` > 0 when the cumulative retry time could exceed the
gunicorn watchdog.

## Re-deployment note
After applying these fixes locally, the production deployment must be **redeployed** via
`suggest_deploy` for the fix to take effect in the hosted environment.
