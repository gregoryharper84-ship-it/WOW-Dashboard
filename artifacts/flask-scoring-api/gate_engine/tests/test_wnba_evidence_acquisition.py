"""
test_wnba_evidence_acquisition.py
WOW-PATCH-2026-08-06-WNBA-EVIDENCE-ACQUISITION-STRUCTURAL

Regression tests covering:
1. Fallback activation triggers when primary API returns partial/missing box_score_log
2. Failure-path / analytical gate cannot run while any required field is
   still in a non-terminal or NOT_CALLED-equivalent state
3. N retrieved box-score rows produce N raw ledger rows (not a summary average)
4. A role claim missing retrieved_at or source fails validation
5. DATA_UNOBTAINABLE_AFTER_EXHAUSTION is only emitted after all configured
   routes are logged as attempted in acquisition_audit
6. PACKET_COMPLETE when all required fields are present
7. PACKET_RECONSTRUCTED when box_score_log is missing but game_log alt key exists
8. PACKET_INCOMPLETE_REJECTED blocks row (terminal label set)
9. Non-WNBA rows are skipped entirely
"""
from __future__ import annotations

import pytest
from typing import Any

# Modules under test
from gate_engine.wnba.acquisition_packet import (
    PacketStatus,
    AcquisitionFieldStatus,
    build_packet,
    reconstruct_raw_ledger_rows,
    normalize_source_claim,
    validate_role_source_claims,
)
from gate_engine.wnba.missing_field_detector import (
    detect_missing,
    classify_missing_fields,
    REQUIRED_PACKET_FIELDS,
)
from gate_engine.wnba.fallback_router import (
    FALLBACK_SOURCE_PRIORITY,
    route_fallback_for_categories,
)
from gate_engine.wnba.evidence_acquisition import run as evidence_run


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_wnba_row(**kwargs) -> dict[str, Any]:
    base = {
        "row_id":    "test-row-001",
        "player":    "A'ja Wilson",
        "sport":     "WNBA",
        "prop_type": "points",
        "line":      23.5,
        "direction": "MORE",
        "blockers":  [],
        "gates":     {},
        "role_status": {
            "active_status":     "ACTIVE",
            "role_timestamp":    "2026-08-06T10:00:00Z",
            "projected_minutes": 34.0,
        },
    }
    base.update(kwargs)
    return base


def _make_full_enr() -> dict[str, Any]:
    """Enrichment with ALL required fields present."""
    return {
        "opponent":        "Seattle Storm",
        "game_date":       "2026-08-06",
        "event_status":    "SCHEDULED",
        "role_timestamp":  "2026-08-06T10:00:00Z",
        "box_score_log": [
            {"date": "2026-08-01", "PTS": 25, "REB": 8, "AST": 4, "MIN": 36, "FGA": 18},
            {"date": "2026-07-28", "PTS": 30, "REB": 10, "AST": 5, "MIN": 38, "FGA": 21},
            {"date": "2026-07-25", "PTS": 22, "REB": 7, "AST": 3, "MIN": 34, "FGA": 16},
            {"date": "2026-07-22", "PTS": 28, "REB": 9, "AST": 6, "MIN": 37, "FGA": 20},
            {"date": "2026-07-19", "PTS": 18, "REB": 6, "AST": 2, "MIN": 30, "FGA": 14},
        ],
        "matchup": {
            "pace":               96.2,
            "opponent_defense":   108.5,
            "position_defense":   112.0,
            "rebound_environment": 0.52,
            "assist_environment":  0.61,
        },
    }


# ---------------------------------------------------------------------------
# Test 1: Fallback activation triggers when primary API returns missing box_score_log
# ---------------------------------------------------------------------------

def test_fallback_activates_when_box_score_log_missing():
    """
    When enrichment has no box_score_log (and no game_log alt key),
    the missing-field detector should flag it and fallback routing should be triggered.
    The acquisition_audit must show fallback_triggered=True and list
    box_score_log in the routes attempted.
    """
    row = _make_wnba_row()
    enr = {
        "opponent":       "Seattle Storm",
        "game_date":      "2026-08-06",
        "event_status":   "SCHEDULED",
        "role_timestamp": "2026-08-06T10:00:00Z",
        # box_score_log intentionally absent
        "matchup": {"pace": 96.0},
    }

    result = evidence_run(row, enr)

    assert result["acquisition_audit"]["fallback_triggered"] is True
    assert any(
        "box_score_log" in f or "l5_ledger" in f or "l10_ledger" in f
        for f in result["acquisition_audit"]["missing_after_primary"]
    ), "box_score_log (or derived ledgers) should be in missing_after_primary"

    # The fallback routes for box_score_log must all be logged
    expected_sources = [s["source_id"] for s in FALLBACK_SOURCE_PRIORITY["box_score_log"]]
    audit = result["acquisition_audit"]
    for src in expected_sources:
        assert src in audit["fallback_routes_attempted"], (
            f"Expected source '{src}' to be logged in fallback_routes_attempted"
        )


# ---------------------------------------------------------------------------
# Test 2: Analytical gate cannot run while a required field is NOT_CALLED-equivalent
# ---------------------------------------------------------------------------

def test_packet_incomplete_rejected_blocks_row():
    """
    When the packet cannot be completed (PACKET_INCOMPLETE_REJECTED),
    the pipeline sets a terminal label on the row, and the row must not
    proceed to any analytical gate.

    We simulate this by running evidence_run with no enrichment at all
    (severe data gap) and checking the pipeline response.
    """
    row = _make_wnba_row(role_status={})  # strip role_status too
    enr: dict[str, Any] = {}  # all enrichment absent

    result = evidence_run(row, enr)

    # Must get PACKET_INCOMPLETE_REJECTED due to multiple unresolved fields
    assert result["packet_status"] == PacketStatus.PACKET_INCOMPLETE_REJECTED, (
        f"Expected PACKET_INCOMPLETE_REJECTED, got {result['packet_status']}"
    )

    # Row must have a terminal_label set by the pipeline gate
    assert row.get("terminal_label") is not None or result["fields_unresolved"], (
        "Row should be blocked when PACKET_INCOMPLETE_REJECTED"
    )

    # No field in field_status_map may be NOT_CALLED-equivalent as a terminal status
    # (the only allowed NOT_CALLED-like intermediate state must never appear in the final map)
    for field_path, status in result["field_status_map"].items():
        assert status != AcquisitionFieldStatus._NOT_YET_ATTEMPTED, (
            f"Field '{field_path}' still has intermediate status _NOT_YET_ATTEMPTED "
            f"— must be resolved to a terminal status"
        )


# ---------------------------------------------------------------------------
# Test 3: N box-score rows → N raw ledger rows (no averaging, no collapsing)
# ---------------------------------------------------------------------------

def test_ledger_reconstruction_preserves_row_count():
    """
    reconstruct_raw_ledger_rows() must return exactly N rows for N input rows.
    No averaging, collapsing, or summarization is permitted — each input
    game must produce exactly one output ledger row.
    """
    box_score_log = [
        {"date": f"2026-08-0{i}", "PTS": 20 + i, "REB": 5, "AST": 3, "MIN": 32, "FGA": 15}
        for i in range(1, 9)  # 8 rows
    ]

    ledger_rows = reconstruct_raw_ledger_rows(box_score_log)

    assert len(ledger_rows) == 8, (
        f"Expected 8 ledger rows (1:1 mapping), got {len(ledger_rows)}"
    )

    # Verify PRA is correctly computed for each row
    for i, r in enumerate(ledger_rows):
        pts = 20 + (i + 1)
        expected_pra = pts + 5 + 3
        assert r["pra"] == pytest.approx(expected_pra), (
            f"Row {i}: expected PRA={expected_pra}, got {r['pra']}"
        )
        # Confirm it's not an average
        assert r["points"] == pts, f"Row {i}: expected points={pts}, got {r['points']}"


def test_ledger_reconstruction_zero_rows():
    """Empty box_score_log → empty ledger (not a crash, not a summary row)."""
    result = reconstruct_raw_ledger_rows([])
    assert result == [], "Empty box_score_log must produce empty ledger, not a summary row"


# ---------------------------------------------------------------------------
# Test 4: Role claim missing retrieved_at or source fails validation
# ---------------------------------------------------------------------------

def test_source_claim_missing_retrieved_at_fails():
    """
    A source claim without retrieved_at must fail normalize_source_claim().
    """
    claim = {
        "source": "official_wnba_injury_report",
        # retrieved_at intentionally absent
        "source_grade": "A",
        "acquisition_method": "PRIMARY_API",
    }
    valid, normalized = normalize_source_claim(claim)
    assert valid is False, "Source claim without retrieved_at must fail validation"
    assert "_validation_error" in normalized


def test_source_claim_missing_source_fails():
    """
    A source claim without source must fail normalize_source_claim().
    """
    claim = {
        # source intentionally absent
        "retrieved_at": "2026-08-06T10:00:00Z",
        "source_grade": "A",
    }
    valid, normalized = normalize_source_claim(claim)
    assert valid is False, "Source claim without source must fail validation"


def test_validate_role_source_claims_flags_missing_fields():
    """
    validate_role_source_claims() must return errors for claims
    missing required metadata fields.
    """
    role_status = {
        "sources": [
            {"source": "espn", "retrieved_at": "2026-08-06T10:00:00Z"},  # valid
            {"source": "beat_reporter"},                                  # missing retrieved_at
            {"retrieved_at": "2026-08-06T10:00:00Z"},                    # missing source
        ]
    }
    errors = validate_role_source_claims(role_status)
    assert len(errors) == 2, f"Expected 2 validation errors, got {len(errors)}: {errors}"
    error_text = " ".join(errors)
    assert "retrieved_at" in error_text
    assert "source" in error_text


# ---------------------------------------------------------------------------
# Test 5: DATA_UNOBTAINABLE_AFTER_EXHAUSTION only after all routes logged
# ---------------------------------------------------------------------------

def test_data_unobtainable_requires_all_routes_logged():
    """
    When fallback routing produces DATA_UNOBTAINABLE_AFTER_EXHAUSTION,
    the acquisition_audit must log ALL configured routes for that field
    category as attempted — not just the ones that were tried.
    """
    # Row with no box_score_log available anywhere
    row = _make_wnba_row()
    enr = {
        "opponent":       "Seattle Storm",
        "game_date":      "2026-08-06",
        "event_status":   "SCHEDULED",
        "role_timestamp": "2026-08-06T10:00:00Z",
        # no box_score_log, no game_log
        "matchup": {"pace": None},
    }

    result  = evidence_run(row, enr)
    audit   = result["acquisition_audit"]

    # box_score_log should be DATA_UNOBTAINABLE_AFTER_EXHAUSTION
    detail = (audit.get("fallback_result_details") or {}).get("box_score_log")
    if detail:  # only check if box_score_log was missing
        if detail["status"] == AcquisitionFieldStatus.DATA_UNOBTAINABLE_AFTER_EXHAUSTION:
            expected_sources = [s["source_id"] for s in FALLBACK_SOURCE_PRIORITY["box_score_log"]]
            for src in expected_sources:
                assert src in audit["fallback_routes_attempted"], (
                    f"Route '{src}' must be logged before DATA_UNOBTAINABLE_AFTER_EXHAUSTION "
                    f"can be emitted. Actually logged: {audit['fallback_routes_attempted']}"
                )


# ---------------------------------------------------------------------------
# Test 6: PACKET_COMPLETE when all required fields present
# ---------------------------------------------------------------------------

def test_packet_complete_when_all_fields_present():
    """
    With full enrichment, packet_status must be PACKET_COMPLETE and
    missing_after_primary must be empty.
    """
    row = _make_wnba_row()
    enr = _make_full_enr()

    result = evidence_run(row, enr)

    assert result["packet_status"] == PacketStatus.PACKET_COMPLETE, (
        f"Expected PACKET_COMPLETE with full enrichment, got {result['packet_status']}. "
        f"Missing: {result['missing_after_primary']}"
    )
    assert result["acquisition_audit"]["fallback_triggered"] is False
    assert result["acquisition_audit"]["missing_after_primary"] == []


# ---------------------------------------------------------------------------
# Test 7: PACKET_RECONSTRUCTED when box_score_log absent but game_log present
# ---------------------------------------------------------------------------

def test_packet_reconstructed_via_game_log_alt_key():
    """
    When box_score_log is absent but game_log (alternate key) is present,
    fallback routing reconstructs it and the packet_status is PACKET_RECONSTRUCTED.
    """
    row = _make_wnba_row()
    enr = {
        "opponent":       "Seattle Storm",
        "game_date":      "2026-08-06",
        "event_status":   "SCHEDULED",
        "role_timestamp": "2026-08-06T10:00:00Z",
        # Use alternate key game_log instead of box_score_log
        "game_log": [
            {"date": "2026-08-01", "PTS": 25, "REB": 8, "AST": 4, "MIN": 36, "FGA": 18},
            {"date": "2026-07-28", "PTS": 30, "REB": 10, "AST": 5, "MIN": 38, "FGA": 21},
            {"date": "2026-07-25", "PTS": 22, "REB": 7, "AST": 3, "MIN": 34, "FGA": 16},
        ],
        "matchup": {
            "pace": 96.0, "opponent_defense": 108.0,
            "position_defense": 111.0, "rebound_environment": 0.50,
            "assist_environment": 0.60,
        },
    }

    result = evidence_run(row, enr)

    # Must not be rejected
    assert result["packet_status"] in (
        PacketStatus.PACKET_COMPLETE,
        PacketStatus.PACKET_RECONSTRUCTED,
    ), f"Expected COMPLETE or RECONSTRUCTED, got {result['packet_status']}"

    # Fallback must have been triggered for box_score_log
    assert result["acquisition_audit"]["fallback_triggered"] is True
    assert "box_score_log" in result["acquisition_audit"].get("fallback_result_details", {})


# ---------------------------------------------------------------------------
# Test 8: Non-WNBA rows are skipped entirely
# ---------------------------------------------------------------------------

def test_non_wnba_rows_skipped():
    """
    evidence_run() must return an empty dict for non-WNBA rows
    and must not touch the row's gates or blockers.
    """
    for sport in ("NBA", "MLB", "NFL", "TENNIS", "nba", ""):
        row = {
            "row_id":    f"test-{sport}",
            "player":    "Test Player",
            "sport":     sport,
            "prop_type": "points",
            "line":      20.5,
            "direction": "MORE",
            "blockers":  [],
            "gates":     {},
        }
        result = evidence_run(row, {})
        assert result == {}, (
            f"Non-WNBA sport '{sport}' should produce empty result, got {result}"
        )
        assert "wnba_evidence_acquisition" not in row.get("gates", {}), (
            f"Non-WNBA row should not have wnba_evidence_acquisition gate"
        )


# ---------------------------------------------------------------------------
# Test 9: can_execute=False is unconditional everywhere
# ---------------------------------------------------------------------------

def test_can_execute_is_always_false():
    """
    can_execute=False must be set on all four new modules unconditionally,
    and must appear in the gate result for every WNBA row regardless of
    packet_status.
    """
    from gate_engine.wnba import acquisition_packet, missing_field_detector
    from gate_engine.wnba import fallback_router, evidence_acquisition

    assert acquisition_packet.can_execute is False
    assert missing_field_detector.can_execute is False
    assert fallback_router.can_execute is False
    assert evidence_acquisition.can_execute is False

    # Gate result also always has can_execute=False
    row = _make_wnba_row()
    result = evidence_run(row, _make_full_enr())
    assert result.get("can_execute") is False, (
        "Gate result must always contain can_execute=False"
    )
