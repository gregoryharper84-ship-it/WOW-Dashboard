"""Append-only, local-disk Gemini standby lifecycle journal (offline).

No prompts, credentials, API responses, user data or probabilities are stored.
Deploy only on approved *persistent* private storage; ephemeral disks do not
provide cross-restart proof. Future actual submission requires an independent
budget reservation/approval service and exact provider readback.
"""
from __future__ import annotations

import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from v17.gemini_async_contracts import BATCH_NAME_RE, CACHE_NAME_RE

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
REF_RE = re.compile(r"^[A-Za-z0-9_:/.-]{1,160}$")
KINDS = frozenset({"BATCH_INLINE", "CACHE_CREATE"})
NEXT = {
    "RESERVED": frozenset({"PROVIDER_CONFIRMED", "OUTCOME_UNKNOWN", "TERMINAL_HOLD"}),
    "PROVIDER_CONFIRMED": frozenset({"RECONCILED", "OUTCOME_UNKNOWN", "TERMINAL_HOLD"}),
    "OUTCOME_UNKNOWN": frozenset({"PROVIDER_CONFIRMED", "TERMINAL_HOLD"}),
    "RECONCILED": frozenset(),
    "TERMINAL_HOLD": frozenset(),
}


class JournalError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class GeminiJournal:
    """SQLite single-writer journal; no automatic retries or API calls.

    A trusted orchestrator must independently validate budget_reservation_ref
    and approval_ref. Merely providing text references is not proof of payment.
    """

    def __init__(self, path: str | Path):
        if not isinstance(path, (str, Path)) or str(path) in {"", ":memory:"}:
            raise JournalError("GEMINI_JOURNAL_STORAGE_UNVERIFIED")
        self.path = Path(path).expanduser().resolve()
        if not self.path.parent.is_dir() or self.path.is_dir():
            raise JournalError("GEMINI_JOURNAL_STORAGE_UNVERIFIED")

    def _open(self):
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        os.chmod(self.path, 0o600)
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("""
          CREATE TABLE IF NOT EXISTS gemini_lifecycle_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL,
            stage TEXT NOT NULL,
            budget_reservation_ref TEXT NOT NULL,
            approval_ref TEXT NOT NULL,
            provider_name TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            UNIQUE(kind, manifest_sha256, stage)
          )
        """)
        conn.execute("""
          CREATE TRIGGER IF NOT EXISTS gemini_events_no_update
          BEFORE UPDATE ON gemini_lifecycle_events BEGIN
            SELECT RAISE(ABORT, 'append_only');
          END
        """)
        conn.execute("""
          CREATE TRIGGER IF NOT EXISTS gemini_events_no_delete
          BEFORE DELETE ON gemini_lifecycle_events BEGIN
            SELECT RAISE(ABORT, 'append_only');
          END
        """)
        return conn

    def reserve(self, *, kind: str, manifest_sha256: str, budget_reservation_ref: str,
                approval_ref: str) -> dict[str, object]:
        if (kind not in KINDS or not isinstance(manifest_sha256, str)
            or not HASH_RE.fullmatch(manifest_sha256)
            or not isinstance(budget_reservation_ref, str)
            or not REF_RE.fullmatch(budget_reservation_ref)
            or not isinstance(approval_ref, str) or not REF_RE.fullmatch(approval_ref)):
            raise JournalError("GEMINI_JOURNAL_INPUT_INVALID")
        with closing(self._open()) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                old = conn.execute(
                    "SELECT stage FROM gemini_lifecycle_events WHERE kind=? AND manifest_sha256=? LIMIT 1",
                    (kind, manifest_sha256),
                ).fetchone()
                if old:
                    raise JournalError("GEMINI_OPERATION_ALREADY_RESERVED")
                conn.execute(
                    """INSERT INTO gemini_lifecycle_events
                    (kind, manifest_sha256, stage, budget_reservation_ref, approval_ref)
                    VALUES (?, ?, 'RESERVED', ?, ?)""",
                    (kind, manifest_sha256, budget_reservation_ref, approval_ref),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return {"kind": kind, "manifest_sha256": manifest_sha256,
                "stage": "RESERVED", "can_execute": False}

    def record(self, *, kind: str, manifest_sha256: str, next_stage: str,
               provider_name: str | None = None) -> dict[str, object]:
        if kind not in KINDS or not isinstance(manifest_sha256, str) or not HASH_RE.fullmatch(manifest_sha256):
            raise JournalError("GEMINI_JOURNAL_INPUT_INVALID")
        if next_stage not in NEXT:
            raise JournalError("GEMINI_JOURNAL_INPUT_INVALID")
        if provider_name is not None and (
            not isinstance(provider_name, str) or not REF_RE.fullmatch(provider_name)):
            raise JournalError("GEMINI_JOURNAL_INPUT_INVALID")
        identity_pattern = BATCH_NAME_RE if kind == "BATCH_INLINE" else CACHE_NAME_RE
        if next_stage == "PROVIDER_CONFIRMED" and (
                not provider_name or not identity_pattern.fullmatch(provider_name)):
            raise JournalError("GEMINI_JOURNAL_PROVIDER_ID_INVALID")
        with closing(self._open()) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                last = conn.execute(
                    """SELECT stage, budget_reservation_ref, approval_ref, provider_name
                       FROM gemini_lifecycle_events WHERE kind=? AND manifest_sha256=?
                       ORDER BY event_id DESC LIMIT 1""",
                    (kind, manifest_sha256),
                ).fetchone()
                if not last:
                    raise JournalError("GEMINI_JOURNAL_RESERVATION_MISSING")
                stage, budget, approval, known_provider = last
                if next_stage not in NEXT[stage]:
                    raise JournalError("GEMINI_JOURNAL_TRANSITION_REJECTED")
                if provider_name and known_provider and provider_name != known_provider:
                    raise JournalError("GEMINI_JOURNAL_PROVIDER_ID_INVALID")
                value = provider_name or known_provider
                conn.execute(
                    """INSERT INTO gemini_lifecycle_events
                      (kind, manifest_sha256, stage, budget_reservation_ref, approval_ref, provider_name)
                      VALUES (?, ?, ?, ?, ?, ?)""",
                    (kind, manifest_sha256, next_stage, budget, approval, value),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return {"kind": kind, "manifest_sha256": manifest_sha256, "stage": next_stage,
                "provider_name": value, "can_execute": False}

    def history(self, *, kind: str, manifest_sha256: str) -> list[dict[str, object]]:
        if kind not in KINDS or not isinstance(manifest_sha256, str) or not HASH_RE.fullmatch(manifest_sha256):
            raise JournalError("GEMINI_JOURNAL_INPUT_INVALID")
        with closing(self._open()) as conn:
            rows = conn.execute(
                """SELECT event_id, stage, provider_name, created_at
                   FROM gemini_lifecycle_events WHERE kind=? AND manifest_sha256=?
                   ORDER BY event_id""",
                (kind, manifest_sha256),
            ).fetchall()
        return [{"event_id": e, "stage": s, "provider_name": n, "created_at": dt}
                for e, s, n, dt in rows]
