---
name: WOW read-only connector health-check contract
description: Standard shape and NOT_CALLED-vs-FAILED semantics expected for any new /wow/<connector>/health endpoint in flask-scoring-api.
---

When adding a source-review health endpoint for a WOW/LLP data connector (e.g. `/wow/kalshi/health/sports`, `/wow/odds/health`, `/wow/mlb-stats/health`), the response must include: `source`, `endpoint`, `timestamp`, `source_status`, `source_grade`, `data_status`, `dry_run_only: true`, `can_execute: false`.

**Why:** the user's connector-audit spec treats `NOT_CALLED` (integration not configured / no key) and `FAILED` (configured but the live call errored) as semantically distinct — conflating them hides real outages behind "not set up yet," and vice versa. This mirrors the Kalshi bridge's `dry_run_only`/`can_execute` discipline used to prove nothing executes.

**How to apply:** before wiring a new external data source into WOW, check whether it already has ad-hoc inline probing (many do, e.g. MLB Stats API and NWS were already called throughout `app.py`/`services/` before any dedicated health route existed) — in that case the real gap is usually the formal health contract, not the integration itself. Grade `A` only when the live signal is fully confirmed (e.g. quota readable); grade `None` on any `FAILED`. `app.py` has no Flask-test-client route-test suite (unlike `kalshi_engine`/`gate_engine`, which test internal modules directly) — verify new health routes with a live curl smoke test instead of writing route-level pytest.
