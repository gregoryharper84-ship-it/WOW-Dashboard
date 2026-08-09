---
name: Kalshi WX shadow non-blocking capture
description: Architecture of the daemon-thread dispatch in kalshi_wx_shadow_capture.py and the semaphore guard pattern.
---

## Rule
Snapshot construction (fast, pure Python) stays on the request thread.
Orchestrator call (5 sequential Claude API calls, potentially minutes) dispatches to a daemon thread via `_Thread` before returning.

## Pattern
```python
_Thread = threading.Thread   # module-level alias — patchable in tests
_SHADOW_SEMAPHORE = threading.Semaphore(1)   # at most 1 run in flight per worker

# In maybe_fire_shadow_snapshot():
if not _SHADOW_SEMAPHORE.acquire(blocking=False):
    _logger.info("SHADOW_CAPTURE_SKIPPED ...")
    return

def _fire_orchestrator():
    try:
        run_shadow_orchestrator(...)
    except Exception as exc:
        _logger.warning(...)
    finally:
        _SHADOW_SEMAPHORE.release()  # always release

_Thread(target=_fire_orchestrator, daemon=True, name=f"kalshi-wx-shadow-{snapshot_id}").start()
# returns immediately — route is unblocked
```

## Why
gunicorn sync workers block the response until the function returns; orchestrator has 90s per subagent × 5 subagents = up to 450s blocking. Daemon thread = identical pattern to app.py:1507, 5309, 7179 (already in production).

## How to apply
- Tests: patch `gate_engine.kalshi_wx_shadow_capture._Thread` with `_SyncThread` (calls target synchronously) and `_SHADOW_SEMAPHORE` with `threading.Semaphore(999)` so tests are deterministic.
- Semaphore: acquire BEFORE thread.start(); release in thread's finally. If .start() itself fails, outer try/except catches it (semaphore theoretically leaks, but .start() failure is an OS-level extreme; process restart recovers it).
- post_fork: no changes needed — per-request daemon threads are not long-lived; semaphore is a plain int, safe across fork.
