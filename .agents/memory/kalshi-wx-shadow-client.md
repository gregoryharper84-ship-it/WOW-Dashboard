---
name: Kalshi WX shadow research client scaffold
description: KalshiWxShadowResearchClient class — authority constants, three safety gates, feature flag pattern, and relationship to the test-only direct completion helper.
---

## What this is
`gate_engine/kalshi_wx_shadow_client.py` — the class-based Claude Agent SDK
client scaffold for the Kalshi Weather shadow research feature.  Currently
inert: `research()` returns a closed failure on all code paths.  Future steps
will wire agent behavior inside gate 3 and route the response through
`validate_shadow_output()`.

## Authority constants (hardcoded class-level, all False)
```
CAN_EXECUTE           = False
PRODUCTION_AUTHORITY  = False
USER_OUTPUT_AUTHORITY = False
```
Not configurable by callers.  `assert_inert()` classmethod confirms them at
call time.  The authority guard in `research()` re-checks at call time to catch
subclasses that accidentally override one to True.

## Three safety gates in research() (in order)
1. **Feature flag** — `_SHADOW_ENABLED` (reads `KALSHI_WX_SHADOW_AGENT_ENABLED`
   env var, defaults False).  Fires BEFORE any SDK object is constructed.
   A present API key cannot bypass this gate.
2. **Authority guard** — rejects subclasses with any authority constant = True.
3. **Scaffold not wired** — no agent behavior connected yet; returns
   `SHADOW_CLIENT_NOT_WIRED`.

## Feature flag pattern
Module-level `_SHADOW_ENABLED: bool` (private, underscored) — tests patch it as:
```python
patch("gate_engine.kalshi_wx_shadow_client._SHADOW_ENABLED", True/False)
```
Reads the same env var (`KALSHI_WX_SHADOW_AGENT_ENABLED`) as
`kalshi_wx_shadow_agent.py` but is an independent read, no cross-module import.

## Relationship to test-only direct completion helper
`invoke_forecast_context_agent()` in `kalshi_wx_shadow_agent.py` is the
**TEST-ONLY direct completion helper** (bare `messages.create()` call, Step 10.1
proof-of-concept).  Its docstring is now labelled "TEST-ONLY DIRECT COMPLETION
HELPER".  Production use = `KalshiWxShadowResearchClient.research()`.

## Validator reuse
`validate_shadow_output` from `kalshi_wx_shadow_schema.py` is imported.  When
behavior is wired in a future step, `return validate_shadow_output(payload)` is
the ONLY permitted path to `passed=True`.  Raw model output must never escape.

## S4 test design decision
The structural "no DB imports" test uses `re.compile` to match actual import
lines (`import <pkg>` / `from <pkg>`) rather than raw string search — the
module docstring explains what it excludes, so raw string search would false-
positive on the documentation text.

## Files
- `gate_engine/kalshi_wx_shadow_client.py` — the scaffold module
- `tests/test_kalshi_wx_shadow_client.py` — 16 tests (S1–S9), all pass

**Why:** Belt-and-suspenders authority enforcement + flag-first activation
discipline means no combination of credentials, env vars, or subclass inheritance
can activate market-facing behavior through this scaffold.
