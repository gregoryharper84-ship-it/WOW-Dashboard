"""Durable request-scope identity regressions for WOW Daily."""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from storage import daily_manifest


class _Cursor:
    def __init__(self, selected_row: dict):
        self._selected_row = selected_row
        self._mode = ""
        self.description = []

    def execute(self, sql, _params=()):
        self._mode = "insert" if "INSERT INTO wow_daily_runs" in sql else "select"
        if self._mode == "select":
            self.description = [(key,) for key in self._selected_row]

    def fetchone(self):
        if self._mode == "insert":
            return None
        return tuple(self._selected_row.values())

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Connection:
    def __init__(self, selected_row: dict):
        self._cursor = _Cursor(selected_row)

    def cursor(self):
        return self._cursor

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_reusing_an_idempotency_key_with_a_conflicting_scope_is_rejected(monkeypatch):
    selected = {
        "run_id": "existing-run",
        "run_date": "2026-08-20",
        "run_timezone": "America/Chicago",
        "request_scope": "FULL_BOARD",
        "request_fingerprint": "scope-fingerprint",
    }
    monkeypatch.setattr(
        daily_manifest,
        "_get_conn",
        lambda **_kwargs: _Connection(selected),
    )

    with pytest.raises(ValueError, match="IDEMPOTENCY_KEY_SCOPE_MISMATCH"):
        daily_manifest.create_or_get_run(
            run_id="new-run",
            idempotency_key="same-key",
            run_date="2026-08-20",
            started_at="2026-08-20T17:00:00+00:00",
            deadline_at="2026-08-20T17:45:00+00:00",
            environment="test",
            requested_sports=["MLB"],
            run_timezone="America/Chicago",
            request_fingerprint="scope-fingerprint",
            request_scope="MONEYLINE_REMAINING_TODAY",
            scope_requested_at="2026-08-20T17:00:00+00:00",
        )