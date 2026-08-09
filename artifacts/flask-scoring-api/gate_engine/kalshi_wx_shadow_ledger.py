"""
gate_engine/kalshi_wx_shadow_ledger.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — Stage 3 (telemetry)

Thread-safe in-process shadow telemetry ledger.

SHADOW-ONLY INVARIANT
  This ledger stores records in a bounded in-memory ring buffer only.
  It does NOT write to any production table, any production log file, or any
  row in weather_scout_log, llp_source_snapshots, or any other existing table.
  It does NOT import psycopg2, SQLAlchemy, or any database driver.
  The isolation test (OR4) asserts this by scanning this file's source.

  Each gunicorn worker has its own in-process singleton.  Shadow runs in one
  worker are NOT visible to another.  This is intentional for the pilot —
  shadow runs are not relied upon for any production decision.

DESIGN
  - Bounded deque (default 500 entries) — oldest entry discarded when full.
  - Thread-safe via threading.Lock — safe under gunicorn's threaded workers.
  - ShadowLedgerEntry is a frozen dataclass — immutable once recorded.
  - get_default_ledger() returns the module-level singleton.

FUTURE EXTENSION POINTS (not built here)
  - Optional JSONL file export path (currently unused).
  - Optional dedicated shadow_research_log Postgres table (not created here).

OUT OF SCOPE
  No Flask routes, no DB imports, no scoring/market/settlement logic.
"""
from __future__ import annotations

import dataclasses
import datetime
import threading
from collections import deque
from typing import Any, Optional

_MAX_ENTRIES: int = 500


# ── Ledger entry ──────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class ShadowLedgerEntry:
    """
    Immutable record of one shadow orchestrator run.

    Fields
    ------
    run_id                  : Caller-supplied run identifier.
    city                    : City under evaluation.
    date                    : ISO-8601 date string (YYYY-MM-DD).
    status                  : Run status — COMPLETE | SCHEMA_FAIL | BLOCKED.
    hook_violations_count   : Number of pre/post hook violations recorded.
    subagents_succeeded     : List of subagent IDs that returned success=True.
    subagents_failed        : List of subagent IDs that returned success=False.
    recorded_at_utc         : UTC ISO-8601 timestamp of when the record was written.
    """
    run_id:                 str
    city:                   str
    date:                   str
    status:                 str
    hook_violations_count:  int
    subagents_succeeded:    tuple
    subagents_failed:       tuple
    recorded_at_utc:        str


# ── Ledger ────────────────────────────────────────────────────────────────────

class ShadowLedger:
    """
    Thread-safe bounded in-memory ring buffer for shadow run telemetry.

    Shadow-only: no DB writes, no file I/O, no production log entries.
    """

    def __init__(self, max_entries: int = _MAX_ENTRIES) -> None:
        self._lock = threading.Lock()
        self._entries: deque[ShadowLedgerEntry] = deque(maxlen=max_entries)
        self._total_recorded: int = 0
        self._total_violations: int = 0

    def record(
        self,
        run_id: str,
        city: str,
        date: str,
        status: str,
        subagent_results: dict,
        hook_violations: list,
    ) -> None:
        """
        Record a shadow orchestrator run.

        Parameters
        ----------
        run_id           : Run identifier.
        city             : City name.
        date             : ISO-8601 date.
        status           : One of COMPLETE | SCHEMA_FAIL | BLOCKED.
        subagent_results : Dict mapping subagent_id → SubagentResult.
        hook_violations  : List of hook violation dicts accumulated during the run.
        """
        succeeded = tuple(
            sid for sid, r in subagent_results.items()
            if getattr(r, "success", False)
        )
        failed = tuple(
            sid for sid, r in subagent_results.items()
            if not getattr(r, "success", True)
        )
        now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()

        entry = ShadowLedgerEntry(
            run_id=str(run_id),
            city=str(city),
            date=str(date),
            status=str(status),
            hook_violations_count=len(hook_violations),
            subagents_succeeded=succeeded,
            subagents_failed=failed,
            recorded_at_utc=now_utc,
        )

        with self._lock:
            self._entries.append(entry)
            self._total_recorded += 1
            self._total_violations += len(hook_violations)

    def get_recent(self, n: int = 10) -> list[ShadowLedgerEntry]:
        """Return the n most recent entries, newest first."""
        with self._lock:
            entries = list(self._entries)
        return list(reversed(entries[-n:])) if entries else []

    def violation_count(self) -> int:
        """Total hook violations across all recorded runs (lifetime counter)."""
        with self._lock:
            return self._total_violations

    def total_recorded(self) -> int:
        """Total runs recorded since this ledger was created."""
        with self._lock:
            return self._total_recorded

    def clear(self) -> None:
        """Clear all entries.  Useful for test isolation."""
        with self._lock:
            self._entries.clear()
            self._total_recorded = 0
            self._total_violations = 0


# ── Module-level singleton ────────────────────────────────────────────────────
# One ledger per gunicorn worker process.  Shadow runs are not cross-worker
# visible — intentional for the pilot.

_DEFAULT_LEDGER: ShadowLedger = ShadowLedger()


def get_default_ledger() -> ShadowLedger:
    """Return the module-level singleton ShadowLedger for this worker."""
    return _DEFAULT_LEDGER
