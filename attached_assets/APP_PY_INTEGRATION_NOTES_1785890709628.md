# app.py integration — read this before applying

## Why this isn't a diff

`odds_quota_crossworker.patch` contains two verified, apply-clean pieces:

1. **New file** `gate_engine/pg_odds_quota.py` — the cross-worker Postgres
   read/write module.
2. **New file** `gate_engine/tests/test_pg_odds_quota.py` — unit tests for it
   (6/6 passing locally, no live DB required — see module docstring).
3. **Modified** `gunicorn_conf.py` — verified against the exact current file
   content, applies cleanly with `git apply`, tested against a byte-identical
   round-trip.

I could **not** produce a verified diff for `app.py` itself. I pulled `app.py`
from `github.com/gregoryharper84-ship-it/WOW-Dashboard` (`main` branch) and it
does not contain `_ODDS_QUOTA_STORE`, `_odds_quota_update`, `_odds_quota_snapshot`,
`GPT_ACTION_SECRET`, `X-WOW-Action-Key`, or any `/wow/*` route — none of the
things `test_odds_quota_tracking.py` (also on that branch) imports and asserts
against. That app.py is also missing every `/wow/*` route referenced elsewhere
in the repo (`/wow/engine/health`, `/gate-engine/run`, etc.) and uses a
different auth header (`X-API-Key`/`SCORING_API_KEY`) than the test file
expects (`X-WOW-Action-Key`/`GPT_ACTION_SECRET`).

**Conclusion: GitHub `main` is stale relative to what's actually deployed on
Replit.** A diff built against it would not apply — the context lines
wouldn't match your real file. Rather than guess and hand you something that
fails `git apply`, here's the exact code to drop into the *real* app.py,
built strictly from the contract `test_odds_quota_tracking.py` already
defines (imports, symbol names, return shapes, endpoint behavior all
verified against that test file).

## What to change in the real app.py

### 1. Locate the existing quota block

Find `_ODDS_QUOTA_STORE`, `_ODDS_QUOTA_LOCK`, `_ODDS_QUOTA_THRESHOLD`,
`_odds_quota_update`, `_odds_quota_snapshot`, and the `/wow/odds/quota-status`
route. Confirm your current `_odds_quota_update` signature matches
`_odds_quota_update(tier, remaining_header, used_header) -> bool` (this is
what `test_odds_quota_tracking.py` calls positionally).

### 2. Add the write-through call at the end of `_odds_quota_update`

After the existing in-process store update (keep that part **byte-for-byte
unchanged** — it's what the unit tests assert against), add:

```python
# ── Cross-worker write-through (Postgres) ────────────────────────────────
# Fixes: quota state was per-process under gunicorn (2 workers), so
# GET /wow/odds/quota-status could report quota_warning=False on the
# worker that DIDN'T just make the low-quota Odds API call.
# Skipped under pytest so gate_engine/tests/test_odds_quota_tracking.py
# keeps its "no live network calls made" contract (see that file's docstring).
if not os.environ.get("PYTEST_CURRENT_TEST"):
    try:
        from gate_engine.pg_odds_quota import persist_quota_update
        persist_quota_update(tier, remaining, used, warning)
    except Exception:
        pass  # fail-open — quota tracking must never break the caller
```

`remaining`, `used`, and `warning` should already be the local variable names
your existing implementation computes before writing to `_ODDS_QUOTA_STORE`
— reuse whatever names are actually there; the values just need to be the
parsed int-or-None remaining/used and the bool warning flag.

### 3. Add a merged read for the endpoint (don't change `_odds_quota_snapshot` itself)

`_odds_quota_snapshot()` is called directly by
`gate_engine/tests/test_odds_quota_tracking.py::TestOddsQuotaSnapshot` and by
`TestQuotaStatusEndpoint::test_empty_store_returns_200`, which asserts
`data["tiers"] == {}` on a **freshly cleared local store** — that assertion
would become flaky if the route pulled in Postgres rows left over from a
different test in the same run. So: leave `_odds_quota_snapshot()` exactly as
it is, and add a new function the route uses instead:

```python
def _odds_quota_snapshot_cross_worker() -> dict:
    """
    Merge the local snapshot with the Postgres cross-worker view so
    GET /wow/odds/quota-status reflects quota consumed by ANY gunicorn
    worker, not just whichever worker answers this particular request.
    Local data wins per-tier when it's the newer of the two records.
    Skipped under pytest — see _odds_quota_update for why.
    """
    local = _odds_quota_snapshot()
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return local
    try:
        from gate_engine.pg_odds_quota import fetch_quota_snapshot
        remote = fetch_quota_snapshot()
    except Exception:
        remote = {}
    merged = dict(local)
    for tier, remote_row in remote.items():
        local_row = local.get(tier)
        if local_row is None or remote_row.get("updated_at", "") > local_row.get("updated_at", ""):
            merged[tier] = remote_row
    return merged
```

### 4. Point the route at the merged function

In the `/wow/odds/quota-status` handler, change the call from
`_odds_quota_snapshot()` to `_odds_quota_snapshot_cross_worker()` for the
`tiers` value (and recompute `quota_warning` as
`any(t.get("quota_warning") for t in tiers.values())` if it isn't already
derived that way). Keep whatever decorator the route currently uses for the
`X-WOW-Action-Key` / `GPT_ACTION_SECRET` check — don't build a new one, the
test file (`TestQuotaStatusEndpoint::test_requires_auth`) already covers
whatever auth mechanism is live.

### 5. Bootstrap the new table at startup

`gunicorn_conf.py` (already patched — see the `.patch` file) schedules
`gate_engine.pg_odds_quota.ensure_table_exists()` in a background thread on
every worker's `post_fork`, the same way Stage 2 tables are bootstrapped. No
action needed here beyond applying the patch, but if app.py's master-process
warmup thread also calls `ensure_all_tables()`-style bootstraps directly
(outside gunicorn's post_fork), add `pg_odds_quota.ensure_table_exists()`
there too for parity with the Stage 2 pattern.

## Why this design doesn't touch `can_execute`

Nothing here relates to WOW gate scoring, labels, or execution. The new
module (`gate_engine/pg_odds_quota.py`) doesn't import gate_engine's scoring
modules, doesn't produce ceilings/labels, and doesn't reference
`can_execute` anywhere — confirmed by grep against the patch content. It's
purely an observability/monitoring write-through for the Odds API quota
counter.

## Verification already done (this session)

- `git apply --check` and `git apply` succeed cleanly against a reconstructed
  copy of the current `gunicorn_conf.py` (byte-identical round-trip verified).
- `python3 -m pytest gate_engine/tests/test_pg_odds_quota.py -q` → 6/6 passed,
  no live `DATABASE_URL` required (fail-open contract verified directly).
- `py_compile` clean on both `pg_odds_quota.py` and the patched
  `gunicorn_conf.py`.

## What I could NOT verify (needs Replit Agent / a real run)

- The exact current `_odds_quota_update` / `_odds_quota_snapshot` code in the
  live app.py (GitHub main is stale — see above).
- `python -m pytest gate_engine/tests/ -x -q` against the **full** existing
  suite (I only have `test_pg_odds_quota.py` and the two new files locally
  runnable; the rest of `gate_engine/tests/` imports modules — `ml_settlement_truth`,
  `kalshi_engine`, etc. — I don't have local copies of, and app.py itself
  couldn't be imported to test `test_odds_quota_tracking.py` end-to-end).
- Whether `wow_odds_quota_state` collides with any existing table name —
  grep your schema for `wow_odds_quota_state` before running
  `ensure_table_exists()` in production, just in case.
