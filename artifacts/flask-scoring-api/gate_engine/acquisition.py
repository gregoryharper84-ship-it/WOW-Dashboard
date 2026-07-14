"""
acquisition.py — WOW-PATCH-MANDATORY-RECONSTRUCTION-v1.0

Mandatory Data Acquisition and Reconstruction.

Every missing critical field must execute the full acquisition ladder and
document every attempted pathway before receiving DATA_UNOBTAINABLE,
REJECT_DATA_QUALITY, or any terminal missing-data bucket.

Taxonomy (Section 12 — Final Operating Command):
  retrieve → corroborate → reconstruct → proxy → classify

  DATA_UNOBTAINABLE                      — data does not exist after full acquisition
  INPUT_FAILURE — ACQUISITION_NOT_COMPLETED — engine had viable fallback paths but skipped them
  RUN_INVALID — ACQUISITION_INCOMPLETE   — a required path remained NOT_CALLED
  RECONSTRUCTED                          — data rebuilt from valid source rows
  PROXY_ONLY                             — only indirect estimation was possible
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Source Status Vocabulary (Section 2)
# ---------------------------------------------------------------------------
class SourceStatus:
    RETRIEVED         = "RETRIEVED"
    RECONSTRUCTED     = "RECONSTRUCTED"
    PROXY_ONLY        = "PROXY_ONLY"
    DATA_UNOBTAINABLE = "DATA_UNOBTAINABLE"
    INPUT_FAILURE     = "INPUT_FAILURE"
    SOURCE_CONFLICT   = "SOURCE_CONFLICT"
    NOT_CALLED        = "NOT_CALLED"
    FAILED            = "FAILED"


# ---------------------------------------------------------------------------
# Reconstruction Status Vocabulary (Section 4)
# ---------------------------------------------------------------------------
class ReconstructionStatus:
    RECONSTRUCTED_A                = "RECONSTRUCTED_A"
    RECONSTRUCTED_B_CORROBORATED   = "RECONSTRUCTED_B_CORROBORATED"
    RECONSTRUCTED_B_UNCORROBORATED = "RECONSTRUCTED_B_UNCORROBORATED"
    RECONSTRUCTION_FAILED          = "RECONSTRUCTION_FAILED"


# Approval caps per reconstruction status (Section 4 mapping)
RECONSTRUCTION_APPROVAL_CAPS: dict[str, str | None] = {
    ReconstructionStatus.RECONSTRUCTED_A:                None,   # no automatic cap
    ReconstructionStatus.RECONSTRUCTED_B_CORROBORATED:   None,   # no automatic cap
    ReconstructionStatus.RECONSTRUCTED_B_UNCORROBORATED: "MODEL_QUALIFIED_HOLD",
    ReconstructionStatus.RECONSTRUCTION_FAILED:          "DATA_UNOBTAINABLE",
}

# Ordered acquisition ladder steps (Section 2)
ACQUISITION_LADDER_STEPS: list[str] = [
    "internal_wow_engine",
    "direct_league_official_source",
    "official_box_score_gamelog",
    "trusted_statistical_database",
    "sportsbook_odds_api",
    "projection_provider",
    "public_search_verified_reporting",
    "direct_reconstruction_official_rows",
    "transparent_proxy_model",
    "DATA_UNOBTAINABLE",
]

# Sentinel labels (Section 8–11)
VERDICT_COMPLETE                     = "COMPLETE"
VERDICT_RUN_INVALID_NOT_CALLED       = "RUN_INVALID — ACQUISITION_INCOMPLETE"
VERDICT_INPUT_FAILURE_NOT_COMPLETED  = "INPUT_FAILURE — ACQUISITION_NOT_COMPLETED"


# ---------------------------------------------------------------------------
# Internal attempt record
# ---------------------------------------------------------------------------
@dataclass
class AcquisitionAttempt:
    source: str
    status: str        # SourceStatus constant
    detail: str = ""
    value:  Any = None


# ---------------------------------------------------------------------------
# AcquisitionTracker — one per row
# ---------------------------------------------------------------------------
class AcquisitionTracker:
    """
    Per-row tracker for field-level acquisition attempts.

    Usage pattern in pipeline:
        tracker = AcquisitionTracker(row_id)
        tracker.mark_missing_at_intake(["game_log", "l5_values"])
        tracker.record_attempt("game_log", "direct_league_official_source",
                               SourceStatus.RETRIEVED, detail="nba_api")
        row["gates"]["acquisition"] = tracker.build_row_report()
    """

    def __init__(self, row_id: str) -> None:
        self.row_id = row_id
        self._missing_at_intake: list[str] = []
        self._attempts: dict[str, list[AcquisitionAttempt]] = {}
        self._recovered:    list[str] = []
        self._proxy_only:   list[str] = []
        self._unobtainable: list[str] = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def mark_missing_at_intake(self, fields: list[str]) -> None:
        """Declare fields that were absent at Data Contract intake."""
        for f in fields:
            if f not in self._missing_at_intake:
                self._missing_at_intake.append(f)
            self._attempts.setdefault(f, [])

    def record_attempt(
        self,
        field:  str,
        source: str,
        status: str,
        detail: str = "",
        value:  Any = None,
    ) -> None:
        """Record one acquisition attempt for a field."""
        self._attempts.setdefault(field, []).append(
            AcquisitionAttempt(source=source, status=status, detail=detail, value=value)
        )
        if status in (SourceStatus.RETRIEVED, SourceStatus.RECONSTRUCTED):
            if field not in self._recovered:
                self._recovered.append(field)
        elif status == SourceStatus.PROXY_ONLY:
            if field not in self._proxy_only:
                self._proxy_only.append(field)
        elif status == SourceStatus.DATA_UNOBTAINABLE:
            if field not in self._unobtainable:
                self._unobtainable.append(field)

    def mark_unobtainable(self, field: str) -> None:
        """Explicitly mark a field as unobtainable after full ladder."""
        if field not in self._unobtainable:
            self._unobtainable.append(field)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def get_final_status(self, field: str) -> str:
        """Return the best source status achieved for a field."""
        attempts = self._attempts.get(field, [])
        if not attempts:
            return SourceStatus.NOT_CALLED
        priority = [
            SourceStatus.RETRIEVED, SourceStatus.RECONSTRUCTED,
            SourceStatus.PROXY_ONLY, SourceStatus.SOURCE_CONFLICT,
            SourceStatus.INPUT_FAILURE, SourceStatus.FAILED,
            SourceStatus.DATA_UNOBTAINABLE,
        ]
        achieved = {a.status for a in attempts}
        for s in priority:
            if s in achieved:
                return s
        return attempts[-1].status

    def not_called_fields(self) -> list[str]:
        """Return fields declared missing but with zero attempts recorded."""
        return [f for f in self._missing_at_intake if not self._attempts.get(f)]

    def is_acquisition_complete(self) -> bool:
        """True when every missing field has at least one documented attempt."""
        return len(self.not_called_fields()) == 0

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------
    def build_row_report(self) -> dict[str, Any]:
        """Build the per-row acquisition gate result."""
        not_called = self.not_called_fields()

        if not_called:
            verdict = VERDICT_RUN_INVALID_NOT_CALLED
        elif self._unobtainable:
            verdict = VERDICT_INPUT_FAILURE_NOT_COMPLETED
        else:
            verdict = VERDICT_COMPLETE

        field_detail: dict[str, Any] = {}
        for f in self._missing_at_intake:
            attempts = self._attempts.get(f, [])
            field_detail[f] = {
                "missing_at_intake": True,
                "final_status":      self.get_final_status(f),
                "attempts_count":    len(attempts),
                "attempts": [
                    {"source": a.source, "status": a.status, "detail": a.detail}
                    for a in attempts
                ],
                "recovered":    f in self._recovered,
                "proxy_only":   f in self._proxy_only,
                "unobtainable": f in self._unobtainable,
            }

        return {
            "fields_missing_at_intake": list(self._missing_at_intake),
            "fields_retrieved":         list(self._recovered),
            "fields_proxy_only":        list(self._proxy_only),
            "fields_unobtainable":      list(self._unobtainable),
            "fields_not_called":        not_called,
            "acquisition_complete":     self.is_acquisition_complete(),
            "acquisition_verdict":      verdict,
            "field_detail":             field_detail,
        }


# ---------------------------------------------------------------------------
# Run-level aggregation (Section 10 — Full-Board Reporting)
# ---------------------------------------------------------------------------
def build_run_acquisition_report(
    row_reports: list[dict[str, Any]],
    failed_source_calls: list[str] | None = None,
) -> dict[str, Any]:
    """
    Aggregate per-row acquisition gate results into the run-level
    Acquisition Execution Report required by Section 29.2.
    """
    total_rows         = len(row_reports)
    complete_rows      = 0
    total_missing      = 0
    total_retrieved    = 0
    total_proxy        = 0
    total_unobtainable = 0
    total_not_called   = 0
    total_fallbacks    = 0

    for rpt in row_reports:
        total_missing      += len(rpt.get("fields_missing_at_intake", []))
        total_retrieved    += len(rpt.get("fields_retrieved", []))
        total_proxy        += len(rpt.get("fields_proxy_only", []))
        total_unobtainable += len(rpt.get("fields_unobtainable", []))
        total_not_called   += len(rpt.get("fields_not_called", []))
        if rpt.get("acquisition_complete"):
            complete_rows += 1
        for fd in rpt.get("field_detail", {}).values():
            attempts = fd.get("attempts_count", len(fd.get("attempts", [])))
            if attempts > 1:
                total_fallbacks += attempts - 1

    rows_invalid = sum(
        1 for r in row_reports
        if r.get("acquisition_verdict") == VERDICT_RUN_INVALID_NOT_CALLED
    )

    return {
        "fields_missing_at_intake":   total_missing,
        "fields_retrieved":           total_retrieved,
        "fields_reconstructed":       0,
        "fields_proxy_only":          total_proxy,
        "fields_unobtainable":        total_unobtainable,
        "fields_not_called":          total_not_called,
        "failed_source_calls":        failed_source_calls or [],
        "fallbacks_executed":         total_fallbacks,
        "acquisition_complete":       complete_rows == total_rows,
        "rows_acquisition_complete":  complete_rows,
        "rows_total":                 total_rows,
        "rows_run_invalid":           rows_invalid,
        "acquisition_completeness_verdict": (
            "COMPLETE" if complete_rows == total_rows else
            f"INCOMPLETE: {total_rows - complete_rows} of {total_rows} rows have NOT_CALLED pathways"
        ),
    }


# ---------------------------------------------------------------------------
# Blocker format helper (Section 8 — Terminal Missing-Data Rule)
# ---------------------------------------------------------------------------
def format_unobtainable_blocker(
    missing_field: str,
    attempted_sources: list[str],
    reconstruction_attempted: bool,
    reconstruction_result: str,
    proxy_attempted: bool,
    final_source_status: str,
    approval_impact: str,
) -> str:
    """Produce a standardised DATA_UNOBTAINABLE blocker string."""
    return (
        f"DATA_UNOBTAINABLE:field={missing_field}:"
        f"sources_tried={len(attempted_sources)}:"
        f"reconstruction={reconstruction_result}:"
        f"proxy={'YES' if proxy_attempted else 'NO'}:"
        f"final_status={final_source_status}:"
        f"impact={approval_impact}"
    )
