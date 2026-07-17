---
name: Production gunicorn PATH fix
description: Bare `gunicorn` silently fails in Replit deployment container; use python -m gunicorn.
---

# Production gunicorn PATH fix

## The rule
Always use `python -m gunicorn` in `artifact.toml` production run commands, not bare `gunicorn`.

## Why
In the Replit deployment container (separate from the dev container), the `gunicorn` binary is not on PATH or resolves to an incompatible version after `pip install`. The result is silent failure: `pre_start.py` prints success, then `gunicorn` exits with no output, no port bind, no error message in logs. The deployment orchestrator never detects port 25643, so the proxy never routes to Flask, and all public requests time out.

`python -m gunicorn` bypasses PATH entirely — it invokes gunicorn as a Python module using the same interpreter that already has the packages installed.

## How to apply
In `artifacts/flask-scoring-api/.replit-artifact/artifact.toml`:
```toml
[services.production.run]
args = [
  "bash", "-c",
  "cd /home/runner/workspace/artifacts/flask-scoring-api && python pre_start.py && python -m gunicorn app:app --bind 0.0.0.0:25643 --workers 2 --timeout 300 --access-logfile -"
]
```

## Diagnostic signal
- Deployment log shows `pre_start: all required env vars present, starting gunicorn`
- No subsequent gunicorn output (`[INFO] Starting gunicorn`, `[INFO] Booting worker`) ever appears
- Only one port detected (`artifact port detected detected=1 expected=2 port=8080`) — Flask port 25643 never detected
- Public URL times out or returns connection error
