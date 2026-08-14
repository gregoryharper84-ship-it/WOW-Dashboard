"""
gate_engine/mlb/startup_prewarm.py
------------------------------------
Auto-prewarm MLB pitcher identity + Statcast cache from today's ESPN probable
pitcher list at gunicorn startup.

Called by _run_startup_warmup() in app.py (background daemon thread, before the
first scoring request arrives). Never blocks the main request path. All errors
are captured and returned as partial_errors — never re-raised to the caller.

Public API
----------
parse_pitcher_names(probable_map)
    Convert the {full_name_lower: team} dict from ESPN into (first, last) tuples.

prewarm_today_pitchers(identity_fn, savant_fn, *, fetch_fn=None)
    Fetch today's probables, parse names, submit prefetch jobs.
    Returns (queued: int, partial_errors: list[str]).

Design notes
------------
- fetch_fn defaults to services.status.get_mlb_probable_pitchers via lazy import
  so this module stays importable in test environments without a live Flask app.
- prewarm() is fire-and-forget (ThreadPoolExecutor); this function returns as
  soon as jobs are submitted — identity + Statcast fetches run concurrently
  in the background while the server handles its first real requests.
- Partial errors are non-fatal — a missing ESPN response or a failed name parse
  degrades gracefully without killing the warmup for other pitchers.
"""

from __future__ import annotations

import logging
from typing import Callable

_log = logging.getLogger(__name__)

# Sentinel distinguishes "caller passed None" from "caller passed nothing"
_DEFAULT_FETCH = object()


# ---------------------------------------------------------------------------
# Name parsing
# ---------------------------------------------------------------------------

def parse_pitcher_names(probable_map: dict) -> list[tuple[str, str]]:
    """
    Convert the ``{full_name_lower: team}`` dict returned by
    ``services.status.get_mlb_probable_pitchers()`` into ``(first, last)``
    tuples suitable for ``pitcher_prefetch.prewarm()``.

    Splitting strategy: ``name.split(" ", 1)``  — first token = first name,
    remainder = last name.  This correctly handles multi-word last names
    (e.g. "jacob de grom" → ("Jacob", "De Grom")).

    Skips entries that are empty, single-token, or produce empty first/last
    after stripping.  Deduplicates case-insensitively.

    Parameters
    ----------
    probable_map : dict
        {lowercase_full_name: team_abbr, ...}  as returned by ESPN helper.
        May be empty or non-dict (treated as no pitchers available).

    Returns
    -------
    list[tuple[str, str]]
        [(first_title_case, last_title_case), ...]  deduplicated, stable order.
    """
    if not isinstance(probable_map, dict):
        return []

    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()

    for raw_name in probable_map.keys():
        name = (raw_name or "").strip()
        if not name:
            continue
        parts = name.split(" ", 1)
        if len(parts) < 2:
            continue                          # single-token — skip
        first = parts[0].strip()
        last  = parts[1].strip()
        if not first or not last:
            continue
        dedup_key = f"{first.lower()}|{last.lower()}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        pairs.append((first.title(), last.title()))

    return pairs


# ---------------------------------------------------------------------------
# Startup prewarm entry point
# ---------------------------------------------------------------------------

def prewarm_today_pitchers(
    identity_fn: Callable,
    savant_fn: Callable,
    *,
    fetch_fn=_DEFAULT_FETCH,
) -> tuple[int, list[str]]:
    """
    Fetch today's MLB probable pitchers and submit fire-and-forget prefetch
    jobs to the bounded ``pitcher_prefetch.ThreadPoolExecutor``.

    Parameters
    ----------
    identity_fn : callable
        ``_pb_lookup_mlbam_id(first, last) → int | None``
        Passed through to ``pitcher_prefetch.prewarm()`` as the identity
        resolver.  The call inside the worker already checks the persistent
        Postgres identity cache before hitting pybaseball.
    savant_fn : callable
        ``_get_pitcher_savant(mlbam_id) → dict``
        Passed through to ``pitcher_prefetch.prewarm()`` as the Statcast
        fetcher.
    fetch_fn : callable | sentinel
        ``() → (dict, status_str)`` or ``() → dict``
        Fetches today's probable pitcher map from ESPN.
        Defaults to ``services.status.get_mlb_probable_pitchers`` via lazy
        import (avoids circular imports in tests).  Pass an explicit callable
        in tests to avoid live HTTP.

    Returns
    -------
    (queued, partial_errors)
        queued        : int   — number of pitcher jobs successfully submitted.
        partial_errors: list  — human-readable non-fatal issues encountered.
                                Empty list means a clean run.
    """
    partial_errors: list[str] = []

    # ------------------------------------------------------------------
    # 1. Resolve fetch_fn (lazy import so the module is test-safe)
    # ------------------------------------------------------------------
    if fetch_fn is _DEFAULT_FETCH:
        try:
            from services.status import get_mlb_probable_pitchers as _default_fetch
            fetch_fn = _default_fetch
        except Exception as exc:
            partial_errors.append(f"import_error:services.status: {str(exc)[:120]}")
            return 0, partial_errors

    # ------------------------------------------------------------------
    # 2. Fetch today's probable pitcher map
    # ------------------------------------------------------------------
    try:
        raw = fetch_fn()
        # get_mlb_probable_pitchers returns (dict, status) tuple
        probable_map: dict = raw[0] if isinstance(raw, (tuple, list)) else raw
    except Exception as exc:
        partial_errors.append(f"fetch_probables_failed: {str(exc)[:120]}")
        return 0, partial_errors

    if not probable_map:
        partial_errors.append("no_probables: ESPN returned zero probable pitchers for today")
        return 0, partial_errors

    # ------------------------------------------------------------------
    # 3. Parse full names into (first, last) pairs
    # ------------------------------------------------------------------
    try:
        pairs = parse_pitcher_names(probable_map)
    except Exception as exc:
        partial_errors.append(f"parse_names_failed: {str(exc)[:120]}")
        return 0, partial_errors

    if not pairs:
        partial_errors.append("no_parseable_names: all ESPN pitcher names failed to parse")
        return 0, partial_errors

    # ------------------------------------------------------------------
    # 4. Submit fire-and-forget prefetch jobs
    # ------------------------------------------------------------------
    try:
        from gate_engine.mlb.pitcher_prefetch import prewarm as _prewarm
        _prewarm(pairs, identity_fn, savant_fn)
    except Exception as exc:
        partial_errors.append(f"prewarm_submit_failed: {str(exc)[:120]}")
        return 0, partial_errors

    _log.info(
        "[startup-prewarm] queued %d pitcher(s): %s",
        len(pairs),
        ", ".join(f"{f} {l}" for f, l in pairs[:10]),
    )
    return len(pairs), partial_errors
