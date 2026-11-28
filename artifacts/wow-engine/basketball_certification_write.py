"""Verify that a persisted basketball fit is visible on the certification row.

`fit_and_persist` writes an artifact, a calibrator and then upserts the single
certification row.  A PostgREST upsert can return without the row reflecting the
new values (stale schema cache, replica lag, or a rejected write the client did
not surface), and the fit would still be reported as SHADOW evidence.  This
module re-reads that one row and raises when it does not name the artifact that
was just written, so a silent no-op can never be reported as persisted evidence.

It contains no sporting logic and never changes can_execute.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

CAN_EXECUTE = False
CERTIFICATION_TABLE = "wow_basketball_team_event_certification"
WRITE_NOT_OBSERVED = "BASKETBALL_CERTIFICATION_WRITE_NOT_OBSERVED"


class BasketballCertificationWriteError(RuntimeError):
    """The certification row does not name the artifact just written."""

    code = WRITE_NOT_OBSERVED

    def __init__(self, message: str, *, code: str = WRITE_NOT_OBSERVED):
        super().__init__(message)
        self.code = code


def _first_row(response: Any) -> Mapping[str, Any] | None:
    data = getattr(response, "data", None)
    if isinstance(data, list):
        return data[0] if data and isinstance(data[0], Mapping) else None
    return data if isinstance(data, Mapping) else None


def read_certification_row(client: Any, *, sport: str) -> Mapping[str, Any] | None:
    """Read the one certification row for a sport, or None when absent."""
    response = (
        client.table(CERTIFICATION_TABLE)
        .select("model_artifact_version,calibrator_version,capability_status,reason_code")
        .eq("sport", sport)
        .limit(1)
        .execute()
    )
    return _first_row(response)


def verify_certification_write(
    client: Any,
    *,
    sport: str,
    model_artifact_version: str,
    calibrator_version: str | None = None,
    unchanged: bool = False,
) -> dict[str, Any]:
    """Re-read the row after the upsert; raise unless it names this artifact.

    ``unchanged`` covers the frozen NBA forward candidate: a refit stores an
    inactive challenger and deliberately leaves the registration row alone, so
    the row must still name the version that was already registered.

    Transport failures while reading are left to propagate unchanged so the
    caller keeps the provider's own error fields.  Only an observable but wrong
    row raises ``BasketballCertificationWriteError``.
    """
    row = read_certification_row(client, sport=sport)
    label = "unchanged" if unchanged else "expected"
    if row is None:
        raise BasketballCertificationWriteError(
            f"{WRITE_NOT_OBSERVED} sport={sport} "
            f"{label}={model_artifact_version} observed=NONE"
        )

    observed = str(row.get("model_artifact_version") or "")
    if observed != model_artifact_version:
        raise BasketballCertificationWriteError(
            f"{WRITE_NOT_OBSERVED} sport={sport} {label}={model_artifact_version} "
            f"observed={observed or 'NONE'} "
            f"capability_status={row.get('capability_status')} "
            f"reason_code={row.get('reason_code')}"
        )

    if calibrator_version is not None:
        observed_calibrator = str(row.get("calibrator_version") or "")
        if observed_calibrator != calibrator_version:
            raise BasketballCertificationWriteError(
                f"{WRITE_NOT_OBSERVED} sport={sport} "
                f"calibrator_expected={calibrator_version} "
                f"observed={observed_calibrator or 'NONE'}"
            )

    return dict(row)


__all__ = [
    "CAN_EXECUTE",
    "CERTIFICATION_TABLE",
    "WRITE_NOT_OBSERVED",
    "BasketballCertificationWriteError",
    "read_certification_row",
    "verify_certification_write",
]
