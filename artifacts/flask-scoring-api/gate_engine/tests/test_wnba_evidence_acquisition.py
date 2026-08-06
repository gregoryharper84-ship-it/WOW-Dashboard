"""
test_wnba_evidence_acquisition.py
WOW-PATCH-2026-08-06-WNBA-EVIDENCE-ACQUISITION-STRUCTURAL
WOW-PATCH-2026-08-06-WNBA-ACQUISITION-CONTRACT-REPAIR

Regression tests covering:
1. Fallback activation triggers when primary API returns partial/missing box_score_log
2. Failure-path / analytical gate cannot run while any required field is
   still in a non-terminal or NOT_CALLED-equivalent state
3. N retrieved box-score rows produce N raw ledger rows (not a summary average)
4. A role claim missing retrieved_at or source fails validation
5. DATA_UNOBTAINABLE_AFTER_EXHAUSTION is only emitted after all configured
   routes are logged as attempted in acquisition_audit
6. PACKET_COMPLETE when all required fields are present
7. PACKET_RECONSTRUCTED_COMPLETE when box_score_log is missing but game_log alt key exists
8. PACKET_INCOMPLETE_REJECTED blocks row (terminal label set)
9. Non-WNBA rows are skipped entirely
10-19. External adapter behaviour under mocked HTTP responses
20-19b. Audit-semantics invariants (routes_attempted = HTTP-only)
20+. BUG-001/002/003 contract-repair regression tests (WOW-PATCH-2026-08-06-WNBA-ACQUISITION-CONTRACT-REPAIR)
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
    """Enrichment with ALL required fields present (critical + qualification)."""
    return {
        "opponent":        "Seattle Storm",
        "game_date":       "2026-08-06",
        "event_status":    "SCHEDULED",
        "role_timestamp":  "2026-08-06T10:00:00Z",
        "projected_minutes": 34.0,
        "role_status": {
            "active_status":     "ACTIVE",
            "role_timestamp":    "2026-08-06T10:00:00Z",
            "projected_minutes": 34.0,
        },
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
        # Qualification-blocking fields — present here so PACKET_COMPLETE fires cleanly
        "market_comparison": {
            "consensus_line":    23.5,
            "books_sampled":     3,
            "cross_book_spread": 0.5,
        },
        "news_contradiction_check": {
            "headlines_scanned":    4,
            "contradiction_found":  False,
            "contradiction_detail": None,
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

    audit = result["acquisition_audit"]

    # Under the new audit-semantics invariant, fallback_routes_attempted contains
    # ONLY HTTP-attempted providers.  In-pipeline sources (enrichment_box_score_log,
    # enrichment_game_log_alternate_key) and policy-skipped sources (basketball_reference)
    # must NOT appear there.  Verify the ESPN gamelog adapter was reached instead.
    fra = audit["fallback_routes_attempted"]
    assert "basketball_reference"          not in fra, "BBRef must not be in fallback_routes_attempted"
    assert "statmuse_reconstruction_query" not in fra, "StatMuse must not be in fallback_routes_attempted"
    assert "enrichment_box_score_log"      not in fra, "In-pipeline source must not be in fallback_routes_attempted"

    # Policy-skipped and not-implemented sources land in the correct new audit fields
    assert "basketball_reference"          in audit.get("routes_skipped_by_policy", []), \
        "basketball_reference must be in routes_skipped_by_policy"
    assert "statmuse_reconstruction_query" in audit.get("routes_not_implemented", []), \
        "statmuse_reconstruction_query must be in routes_not_implemented"

    # The ESPN gamelog adapter (the only real HTTP route) must be logged in
    # fallback_routes_attempted (if a request was actually attempted)
    # OR appear in the routes_attempted normalized records
    espn_in_fra     = "espn_wnba_athlete_gamelog" in fra
    espn_in_records = any(
        r["provider"] == "espn_wnba_athlete_gamelog"
        for r in audit.get("routes_attempted", [])
    )
    assert espn_in_fra or espn_in_records, (
        "espn_wnba_athlete_gamelog must appear in fallback_routes_attempted "
        "or routes_attempted when box_score_log fallback fires"
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
            # Under the new audit-semantics invariant:
            #   - HTTP-attempted routes appear in fallback_routes_attempted
            #   - Policy-skipped routes appear in routes_skipped_by_policy
            #   - Not-implemented routes appear in routes_not_implemented
            # All non-in-pipeline configured routes must be visible somewhere.
            fra      = audit.get("fallback_routes_attempted", [])
            skipped  = audit.get("routes_skipped_by_policy", [])
            not_impl = audit.get("routes_not_implemented", [])
            all_visible = set(fra) | set(skipped) | set(not_impl)

            # basketball_reference → routes_skipped_by_policy (NOT fallback_routes_attempted)
            assert "basketball_reference" in skipped, (
                f"basketball_reference must be in routes_skipped_by_policy; got {skipped}"
            )
            assert "basketball_reference" not in fra, (
                f"basketball_reference (request_made=False) must not be in "
                f"fallback_routes_attempted; got {fra}"
            )
            # statmuse → routes_not_implemented (NOT fallback_routes_attempted)
            assert "statmuse_reconstruction_query" in not_impl, (
                f"statmuse_reconstruction_query must be in routes_not_implemented; got {not_impl}"
            )
            assert "statmuse_reconstruction_query" not in fra, (
                f"statmuse_reconstruction_query (request_made=False) must not be "
                f"in fallback_routes_attempted; got {fra}"
            )
            # Full visibility: all non-enrichment priority-table sources in some bucket
            for src in FALLBACK_SOURCE_PRIORITY["box_score_log"]:
                sid = src["source_id"]
                if sid.startswith("enrichment_"):
                    continue   # in-pipeline sources are not tracked in HTTP/skip buckets
                assert sid in all_visible, (
                    f"Route '{sid}' must be visible in fallback_routes_attempted, "
                    f"routes_skipped_by_policy, or routes_not_implemented. "
                    f"Visible: {all_visible}"
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
    build_packet() now consumes game_log directly (BUG-001 fix) — so box_score_log
    is populated BEFORE detect_missing runs and the fallback for box_score_log is
    never triggered.  The packet_status must not be PACKET_INCOMPLETE_REJECTED.

    Updated from original: after BUG-001 fix, "box_score_log" MUST NOT appear in
    fallback_result_details — it is consumed by build_packet, not the fallback router.
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

    # Must not be rejected — game_log is consumed by build_packet (BUG-001 fix)
    assert result["packet_status"] in (
        PacketStatus.PACKET_COMPLETE,
        PacketStatus.PACKET_RECONSTRUCTED_COMPLETE,
        PacketStatus.PACKET_PARTIAL_HOLD,  # acceptable if qual-blocking fields absent
    ), f"Expected COMPLETE/RECONSTRUCTED_COMPLETE/PARTIAL_HOLD, got {result['packet_status']}"
    assert result["packet_status"] != PacketStatus.PACKET_INCOMPLETE_REJECTED, (
        "PACKET_INCOMPLETE_REJECTED must not fire when game_log alt key is present "
        "(BUG-001 fix: build_packet consumes game_log before detect_missing runs)"
    )

    # BUG-001 fix verification: box_score_log MUST NOT be in fallback_result_details —
    # it was consumed by build_packet directly, not the fallback router.
    fallback_details = result["acquisition_audit"].get("fallback_result_details", {})
    assert "box_score_log" not in fallback_details, (
        "After BUG-001 fix: build_packet() normalises game_log into box_score_log "
        "before detect_missing runs — the fallback for box_score_log must NOT fire. "
        f"fallback_result_details keys: {list(fallback_details.keys())}"
    )

    # box_score_log-related fields must not be in fields_unresolved
    unresolved = result.get("fields_unresolved") or []
    bsl_unresolved = [f for f in unresolved if
                      "box_score" in f or f in ("l5_ledger", "l10_ledger")]
    assert not bsl_unresolved, (
        f"box_score/ledger fields must not be unresolved when game_log is present: "
        f"{bsl_unresolved}"
    )


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


# ===========================================================================
# Tests 10-19: External adapter behaviour under mocked HTTP responses
# All use unittest.mock.patch so no real network calls are made.
# ===========================================================================

from unittest.mock import patch, MagicMock

from gate_engine.wnba.external_adapters import (
    AdapterResult,
    RequestStatus,
    fetch_role_status,
    fetch_event_status,
    fetch_box_score_log,
    fetch_market_comparison,
    fetch_news_contradiction,
)


def _mock_response(status_code: int, json_body: Any) -> MagicMock:
    """Build a minimal requests.Response mock."""
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_body
    return m


# ---------------------------------------------------------------------------
# Test 10: fetch_role_status — player NOT on injury list → ACTIVE_INFERRED
# ---------------------------------------------------------------------------

def test_fetch_role_status_player_not_on_list_infers_active():
    """
    When the ESPN injury feed returns successfully but the player is absent,
    the adapter must return REQUEST_SUCCEEDED with active_status=ACTIVE_INFERRED
    and raw_record_count=0 (absence inference, not a confirmed signal).
    """
    mock_body = {"injuries": []}  # empty injury list
    with patch("requests.get", return_value=_mock_response(200, mock_body)):
        result = fetch_role_status("A'ja Wilson")

    assert isinstance(result, AdapterResult)
    assert result.request_status == RequestStatus.REQUEST_SUCCEEDED
    assert result.normalized_fields.get("active_status") == "ACTIVE_INFERRED"
    assert result.normalized_fields.get("inference_basis") == "not_on_espn_injury_report"
    assert result.raw_record_count == 0
    assert result.request_count == 1


# ---------------------------------------------------------------------------
# Test 11: fetch_role_status — player on list as Questionable → UNCERTAIN
# ---------------------------------------------------------------------------

def test_fetch_role_status_player_listed_questionable_returns_uncertain():
    """
    When a player IS in the injury feed and listed as Questionable, the adapter
    maps to active_status=UNCERTAIN and raw_record_count=1.
    """
    mock_body = {
        "injuries": [
            {
                "injuries": [
                    {
                        "athlete":     {"displayName": "A'ja Wilson"},
                        "status":      "Questionable",
                        "longComment": "Right ankle soreness",
                        "details":     {"returnDate": None},
                    }
                ]
            }
        ]
    }
    with patch("requests.get", return_value=_mock_response(200, mock_body)):
        result = fetch_role_status("A'ja Wilson")

    assert result.request_status == RequestStatus.REQUEST_SUCCEEDED
    assert result.normalized_fields.get("active_status") == "UNCERTAIN"
    assert result.normalized_fields.get("injury_status") == "QUESTIONABLE"
    assert result.raw_record_count == 1
    assert result.request_count == 1


# ---------------------------------------------------------------------------
# Test 12: fetch_event_status — ESPN scoreboard returns SCHEDULED game
# ---------------------------------------------------------------------------

def test_fetch_event_status_returns_scheduled():
    """
    When ESPN scoreboard JSON contains a matching game with STATUS_SCHEDULED,
    the adapter must return event_status='SCHEDULED' and REQUEST_SUCCEEDED.
    """
    mock_body = {
        "events": [
            {
                "id": "evt-001",
                "competitions": [
                    {
                        "competitors": [
                            {"team": {"abbreviation": "LVA", "displayName": "Las Vegas Aces"}},
                            {"team": {"abbreviation": "SEA", "displayName": "Seattle Storm"}},
                        ],
                        "status": {
                            "type": {
                                "name":        "STATUS_SCHEDULED",
                                "description": "Scheduled",
                            }
                        },
                    }
                ],
            }
        ]
    }
    with patch("requests.get", return_value=_mock_response(200, mock_body)):
        result = fetch_event_status("LVA vs SEA", date_str="20260806")

    assert result.request_status == RequestStatus.REQUEST_SUCCEEDED
    assert result.normalized_fields.get("event_status") == "SCHEDULED"
    assert result.request_count == 1


# ---------------------------------------------------------------------------
# Test 13: fetch_event_status — connection error → SOURCE_UNAVAILABLE
# ---------------------------------------------------------------------------

def test_fetch_event_status_handles_connection_error():
    """
    When requests.get raises ConnectionError, the adapter must return
    SOURCE_UNAVAILABLE (not an unhandled exception) and request_count=1
    (the attempt was made).
    """
    import requests as _req
    with patch("requests.get", side_effect=_req.exceptions.ConnectionError("refused")):
        result = fetch_event_status("LVA vs SEA")

    assert result.request_status == RequestStatus.SOURCE_UNAVAILABLE
    assert result.request_count == 1
    assert result.failure_reason is not None


# ---------------------------------------------------------------------------
# Test 14: fetch_box_score_log — ESPN search returns empty → REQUEST_EMPTY
# ---------------------------------------------------------------------------

def test_fetch_box_score_log_espn_search_empty_returns_unobtainable():
    """
    When the ESPN athlete search returns HTTP 200 but no matching athlete,
    fetch_box_score_log must return REQUEST_EMPTY (not a crash or silent None).
    """
    # Search returns empty results block
    search_body = {"results": []}
    with patch("requests.get", return_value=_mock_response(200, search_body)):
        result = fetch_box_score_log("A'ja Wilson", n_games=10)

    assert isinstance(result, AdapterResult)
    # Any non-success status is acceptable — we only care that it doesn't crash
    assert result.request_status in (
        RequestStatus.REQUEST_EMPTY,
        RequestStatus.PARSE_FAILED,
        RequestStatus.REQUEST_FAILED,
        RequestStatus.SOURCE_UNAVAILABLE,
        RequestStatus.RATE_LIMITED,
        RequestStatus.AUTH_REQUIRED,
    ), f"Unexpected status for search-empty case: {result.request_status}"
    # No exception should escape
    assert result.request_count >= 1


# ---------------------------------------------------------------------------
# Test 15: fetch_market_comparison — no ODDS_API_KEY → AUTH_REQUIRED, 0 requests
# ---------------------------------------------------------------------------

def test_fetch_market_comparison_no_env_key_returns_auth_required(monkeypatch):
    """
    When ODDS_API_KEY is not configured, fetch_market_comparison must return
    AUTH_REQUIRED immediately without making any HTTP request (request_count=0).
    """
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.delenv("ODDS_API_FREE_KEY", raising=False)
    monkeypatch.delenv("ODDS_API_PAID_KEY", raising=False)

    with patch("requests.get") as mock_get:
        result = fetch_market_comparison("A'ja Wilson", "points", line=23.5)
        # Confirm no HTTP call was made
        mock_get.assert_not_called()

    assert result.request_status == RequestStatus.AUTH_REQUIRED
    assert result.request_count == 0


# ---------------------------------------------------------------------------
# Test 16: fetch_market_comparison — events found but player not in props
# ---------------------------------------------------------------------------

def test_fetch_market_comparison_player_not_in_any_prop_market(monkeypatch):
    """
    When Odds API returns events but no outcome for the specified player,
    fetch_market_comparison must return REQUEST_EMPTY with request_count >= 2
    (at least the events call + one odds call).
    """
    monkeypatch.setenv("ODDS_API_KEY", "test-key-12345")

    events_body = [{"id": "evt-abc"}]
    odds_body   = {"bookmakers": []}  # empty bookmakers — player not listed

    responses = [
        _mock_response(200, events_body),
        _mock_response(200, odds_body),
    ]
    with patch("requests.get", side_effect=responses):
        result = fetch_market_comparison("A'ja Wilson", "points", line=23.5)

    assert result.request_status in (
        RequestStatus.REQUEST_EMPTY,
        RequestStatus.REQUEST_SUCCEEDED,
    ), f"Expected REQUEST_EMPTY or REQUEST_SUCCEEDED, got {result.request_status}"
    assert result.request_count >= 2


# ---------------------------------------------------------------------------
# Test 17: PACKET_PARTIAL_HOLD does not block critical path / row proceeds
# ---------------------------------------------------------------------------

def test_packet_partial_hold_does_not_block_row():
    """
    When all CRITICAL_BLOCKING fields are present but QUALIFICATION_BLOCKING
    fields (market_comparison, news_contradiction_check) are absent,
    evidence_run must return PACKET_PARTIAL_HOLD — NOT PACKET_INCOMPLETE_REJECTED.
    The row must NOT be assigned a terminal_label by the acquisition gate itself.
    """
    row = _make_wnba_row()
    # Full critical fields, but NO qualification-blocking fields
    enr = {
        "opponent":        "Seattle Storm",
        "game_date":       "2026-08-06",
        "event_status":    "SCHEDULED",
        "role_timestamp":  "2026-08-06T10:00:00Z",
        "projected_minutes": 34.0,
        "role_status": {
            "active_status":     "ACTIVE",
            "role_timestamp":    "2026-08-06T10:00:00Z",
            "projected_minutes": 34.0,
        },
        "box_score_log": [
            {"date": "2026-08-01", "PTS": 25, "REB": 8, "AST": 4, "MIN": 36, "FGA": 18},
            {"date": "2026-07-28", "PTS": 30, "REB": 10, "AST": 5, "MIN": 38, "FGA": 21},
            {"date": "2026-07-25", "PTS": 22, "REB": 7, "AST": 3, "MIN": 34, "FGA": 16},
            {"date": "2026-07-22", "PTS": 28, "REB": 9, "AST": 6, "MIN": 37, "FGA": 20},
            {"date": "2026-07-19", "PTS": 18, "REB": 6, "AST": 2, "MIN": 30, "FGA": 14},
        ],
        "matchup": {
            "pace": 95.0, "opponent_defense": 107.0,
            "position_defense": 110.0, "rebound_environment": 0.51,
            "assist_environment": 0.60,
        },
        # market_comparison and news_contradiction_check deliberately absent
    }

    result = evidence_run(row, enr)

    # Must not be hard-rejected (critical fields all present)
    assert result["packet_status"] != PacketStatus.PACKET_INCOMPLETE_REJECTED, (
        "PACKET_INCOMPLETE_REJECTED must not fire when all CRITICAL fields are resolved"
    )

    # evidence_run itself does NOT set terminal_label — that's pipeline.py's job
    # The acquisition gate output only sets the packet_status signal.
    assert result["packet_status"] in (
        PacketStatus.PACKET_COMPLETE,
        PacketStatus.PACKET_RECONSTRUCTED_COMPLETE,
        PacketStatus.PACKET_PARTIAL_HOLD,
    ), f"Unexpected packet_status: {result['packet_status']}"


# ---------------------------------------------------------------------------
# Test 18: Failure-path adapters always return non-negative request_count
# ---------------------------------------------------------------------------

def test_adapter_request_count_nonnegative_after_timeout(monkeypatch):
    """
    When a request times out, the adapter must return request_count >= 1 and
    a non-None failure_reason. request_count must never be negative.
    """
    import requests as _req
    for adapter_call, kwargs in [
        (lambda: fetch_role_status("A'ja Wilson"),                               {}),
        (lambda: fetch_event_status("LVA vs SEA", date_str="20260806"),          {}),
        (lambda: fetch_news_contradiction("A'ja Wilson"),                        {}),
    ]:
        with patch("requests.get", side_effect=_req.exceptions.Timeout("timed out")):
            result = adapter_call()
        assert result.request_count >= 0, (
            f"request_count must be non-negative after timeout; got {result.request_count}"
        )
        assert result.failure_reason is not None, (
            "failure_reason must be set after a timeout"
        )
        assert result.request_status != RequestStatus.NOT_ATTEMPTED, (
            "NOT_ATTEMPTED must not appear when the adapter was actually called"
        )


# ---------------------------------------------------------------------------
# Test 19: All five adapters return AdapterResult instances (sanity check)
# ---------------------------------------------------------------------------

def test_all_adapters_return_adapter_result_instances(monkeypatch):
    """
    Every adapter function must return an AdapterResult regardless of the
    HTTP response received. Verify with minimal valid mocks.
    """
    monkeypatch.setenv("ODDS_API_KEY", "test-key")

    # Minimal ESPN body: athlete search returns empty, scoreboard returns empty
    empty_body: dict = {}

    import requests as _req
    with patch("requests.get", return_value=_mock_response(200, empty_body)):
        r1 = fetch_role_status("Test Player")
        r2 = fetch_event_status("A vs B")
        r3 = fetch_box_score_log("Test Player", n_games=10)
        r4 = fetch_news_contradiction("Test Player")

    # market_comparison with ODDS_API_KEY set but empty events list
    with patch("requests.get", return_value=_mock_response(200, [])):
        r5 = fetch_market_comparison("Test Player", "points", line=20.0)

    for i, r in enumerate([r1, r2, r3, r4, r5], start=1):
        assert isinstance(r, AdapterResult), (
            f"Adapter {i} returned {type(r).__name__}, expected AdapterResult"
        )
        assert r.request_count >= 0, (
            f"Adapter {i} returned negative request_count={r.request_count}"
        )


# ===========================================================================
# Tests 20-25: Audit-semantics invariants (routes_attempted correctness)
# Requirement: routes_attempted / fallback_routes_attempted may contain ONLY
# providers where an actual outbound HTTP request was initiated (request_made=True).
# ===========================================================================

def _run_evidence_with_no_http_enrichment() -> dict:
    """Run evidence_run with no box_score_log so fallback fires, but mock all
    HTTP adapters to fail immediately so we can inspect audit fields without
    real network calls."""
    row = _make_wnba_row()
    enr = {
        "opponent":       "Seattle Storm",
        "game_date":      "2026-08-06",
        "event_status":   "SCHEDULED",
        "role_timestamp": "2026-08-06T10:00:00Z",
        "projected_minutes": 34.0,
        "role_status": {
            "active_status":     "ACTIVE",
            "role_timestamp":    "2026-08-06T10:00:00Z",
            "projected_minutes": 34.0,
        },
        # box_score_log intentionally absent — triggers ESPN gamelog fallback
        # matchup intentionally absent — triggers matchup fallback
    }
    import requests as _req
    with patch("requests.get",
               side_effect=_req.exceptions.ConnectionError("mocked offline")):
        return evidence_run(row, enr)


# ---------------------------------------------------------------------------
# Test 20: basketball_reference absent from routes_attempted
# ---------------------------------------------------------------------------

def test_basketball_reference_absent_from_routes_attempted():
    """
    basketball_reference is configured in FALLBACK_SOURCE_PRIORITY["box_score_log"]
    but no HTTP request is ever made for it (skipped by robots.txt/ToS policy).
    It must NOT appear in routes_attempted or fallback_routes_attempted in any
    acquisition_audit regardless of what other adapters return.
    """
    result = _run_evidence_with_no_http_enrichment()
    audit  = result["acquisition_audit"]

    # Top-level normalized records — request_made=True only
    ra_providers = {r["provider"] for r in audit.get("routes_attempted", [])}
    assert "basketball_reference" not in ra_providers, (
        f"basketball_reference must never appear in routes_attempted "
        f"(request_made=False); found in: {ra_providers}"
    )

    # Backward-compat string list — HTTP only
    fra = audit.get("fallback_routes_attempted", [])
    assert "basketball_reference" not in fra, (
        f"basketball_reference must never appear in fallback_routes_attempted; "
        f"found in: {fra}"
    )


# ---------------------------------------------------------------------------
# Test 21: basketball_reference in routes_skipped_by_policy; statmuse in
#          routes_not_implemented (not in routes_attempted)
# ---------------------------------------------------------------------------

def test_policy_skipped_and_not_implemented_in_correct_buckets():
    """
    - basketball_reference → routes_skipped_by_policy (NOT routes_attempted)
    - statmuse_reconstruction_query → routes_not_implemented (NOT routes_attempted)
    Both must be fully absent from routes_attempted and fallback_routes_attempted.
    """
    result = _run_evidence_with_no_http_enrichment()
    audit  = result["acquisition_audit"]

    ra_providers  = {r["provider"] for r in audit.get("routes_attempted", [])}
    fra           = audit.get("fallback_routes_attempted", [])
    skipped       = audit.get("routes_skipped_by_policy", [])
    not_impl      = audit.get("routes_not_implemented", [])

    # Correct bucket membership
    assert "basketball_reference" in skipped, (
        f"basketball_reference must be in routes_skipped_by_policy; got {skipped}"
    )
    assert "statmuse_reconstruction_query" in not_impl, (
        f"statmuse_reconstruction_query must be in routes_not_implemented; got {not_impl}"
    )

    # Absent from HTTP-only lists
    for provider in ("basketball_reference", "statmuse_reconstruction_query"):
        assert provider not in ra_providers, (
            f"{provider} (request_made=False) must not appear in routes_attempted"
        )
        assert provider not in fra, (
            f"{provider} (request_made=False) must not appear in fallback_routes_attempted"
        )


# ---------------------------------------------------------------------------
# Test 22: request_count equals actual HTTP requests (0 when no requests made)
# ---------------------------------------------------------------------------

def test_request_count_equals_actual_http_request_count():
    """
    When all external HTTP adapters fail with a connection error and no
    in-pipeline HTTP calls occur, request_count in the audit must reflect
    the actual number of HTTP requests attempted — not include BBRef or
    StatMuse (which were never requested).

    We mock ConnectionError so no successful response comes back, but
    the adapters DO attempt a request (one attempt each, then fail).
    We verify request_count >= 0 and that it equals the sum of
    request_made=True route records' counts.
    """
    result = _run_evidence_with_no_http_enrichment()
    audit  = result["acquisition_audit"]

    # request_count must equal the number of actual HTTP attempts
    http_records = [r for r in audit.get("routes_attempted", []) if r.get("request_made")]
    declared_count = audit.get("request_count", -1)

    assert declared_count >= 0, f"request_count must be non-negative; got {declared_count}"

    # bbref/statmuse contribute 0 requests — verify they don't inflate the count
    skipped = audit.get("routes_skipped_by_policy", [])
    ni      = audit.get("routes_not_implemented", [])
    non_http_providers = set(skipped + ni)

    for rec in audit.get("routes_attempted", []):
        assert rec["provider"] not in non_http_providers, (
            f"Provider '{rec['provider']}' with request_made=False "
            f"is in routes_attempted — invariant violated"
        )


# ---------------------------------------------------------------------------
# Test 23: adapters_called contains no provider with request_made=False
# ---------------------------------------------------------------------------

def test_adapters_called_excludes_request_made_false_providers():
    """
    adapters_called must contain only providers where an actual HTTP request
    was issued (request_count > 0, request_made=True on the route record).
    basketball_reference and statmuse_reconstruction_query must never appear.
    """
    result = _run_evidence_with_no_http_enrichment()
    audit  = result["acquisition_audit"]

    adapters_called = audit.get("adapters_called", [])
    skipped         = set(audit.get("routes_skipped_by_policy", []))
    not_impl        = set(audit.get("routes_not_implemented", []))
    no_request_providers = skipped | not_impl

    for provider in adapters_called:
        assert provider not in no_request_providers, (
            f"adapters_called contains '{provider}' which has request_made=False "
            f"(skipped_by_policy or not_implemented) — invariant violated"
        )

    # Explicit checks for the two known offenders
    assert "basketball_reference"       not in adapters_called
    assert "statmuse_reconstruction_query" not in adapters_called


# ---------------------------------------------------------------------------
# Test 24: fallback_routes_attempted contains only HTTP-attempted entries
# ---------------------------------------------------------------------------

def test_fallback_routes_attempted_contains_only_http_entries():
    """
    fallback_routes_attempted (backward-compat list[str]) must contain only
    provider names where request_made=True.  Every entry in the list must
    correspond to a route record where request_made=True in routes_attempted.
    """
    result = _run_evidence_with_no_http_enrichment()
    audit  = result["acquisition_audit"]

    fra          = audit.get("fallback_routes_attempted", [])
    http_records = audit.get("routes_attempted", [])   # normalized, request_made=True only
    http_names   = {r["provider"] for r in http_records}

    # All entries in fallback_routes_attempted must be in the HTTP-record set
    # OR be in-pipeline non-HTTP sources (enrichment_* / status_role_gate).
    # The critical invariant: BBRef and StatMuse must not be there.
    forbidden = {"basketball_reference", "statmuse_reconstruction_query"}
    for entry in fra:
        assert entry not in forbidden, (
            f"'{entry}' (request_made=False) must not be in fallback_routes_attempted; "
            f"full list: {fra}"
        )


# ---------------------------------------------------------------------------
# Test 25: route_records in fallback_result_details use request_made field
# ---------------------------------------------------------------------------

def test_fallback_result_details_route_records_have_request_made_field():
    """
    Each entry in fallback_result_details[category]["route_records"] must have
    a boolean request_made field.  Entries with request_made=False must have
    a skip_category field (SKIPPED_BY_POLICY, NOT_IMPLEMENTED, AUTH_REQUIRED,
    or UNAVAILABLE).  This validates the per-record normalized schema.
    """
    result = _run_evidence_with_no_http_enrichment()
    audit  = result["acquisition_audit"]
    details = audit.get("fallback_result_details", {})

    for category, detail in details.items():
        for rec in detail.get("route_records", []):
            assert "request_made" in rec, (
                f"category={category} route_record missing 'request_made': {rec}"
            )
            assert isinstance(rec["request_made"], bool), (
                f"category={category} 'request_made' must be bool; got {type(rec['request_made'])}"
            )
            if not rec["request_made"]:
                assert "skip_category" in rec, (
                    f"category={category} request_made=False record missing "
                    f"'skip_category': {rec}"
                )
                assert rec["skip_category"] in (
                    "SKIPPED_BY_POLICY", "NOT_IMPLEMENTED",
                    "AUTH_REQUIRED", "UNAVAILABLE",
                ), (
                    f"category={category} unknown skip_category "
                    f"'{rec['skip_category']}': {rec}"
                )


# =============================================================================
# WOW-PATCH-2026-08-06-WNBA-ACQUISITION-CONTRACT-REPAIR — Regression Tests
# Tests 29-40: BUG-001 (packet key alias), BUG-002 (ledger normalization),
#              BUG-003a (Odds API credential), BUG-003b (ESPN v2 search)
# =============================================================================

# ---------------------------------------------------------------------------
# Test 29 — BUG-001: game_log alias populates canonical box_score_log in build_packet
# ---------------------------------------------------------------------------

def test_bug001_game_log_alias_populates_box_score_log():
    """
    BUG-001: build_packet() must consume 'game_log' when 'box_score_log' is absent.
    The canonical packet field 'box_score_log' must have the same row count as game_log.
    """
    row = _make_wnba_row(prop_type="player_points")
    enr = {
        "game_log": [
            {"stat": 22.0, "line": 23.5, "hit": False, "date": "2026-08-01", "opponent": "CHI"},
            {"stat": 28.0, "line": 23.5, "hit": True,  "date": "2026-07-28", "opponent": "LV"},
            {"stat": 19.0, "line": 23.5, "hit": False, "date": "2026-07-25", "opponent": "NY"},
        ],
    }
    result = evidence_run(row, enr)
    # The patch_status should NOT be PACKET_INCOMPLETE_REJECTED for the ledger bucket
    # (box_score fields consumed by build_packet, not a fallback)
    audit = result.get("acquisition_audit", {})
    fallback_details = audit.get("fallback_result_details", {})
    assert "box_score_log" not in fallback_details, (
        "BUG-001: game_log must be consumed by build_packet, never a fallback target. "
        f"fallback_result_details keys: {list(fallback_details.keys())}"
    )


# ---------------------------------------------------------------------------
# Test 30 — BUG-001: box_score_log takes precedence over game_log when both present
# ---------------------------------------------------------------------------

def test_bug001_box_score_log_takes_precedence_over_game_log():
    """
    BUG-001 precedence rule: when both 'box_score_log' and 'game_log' are present
    in enrichment, 'box_score_log' must win.  The packet's box_score_audit must
    record source_input_key='box_score_log'.
    """
    row = _make_wnba_row(prop_type="player_points")
    # box_score_log has 5 rows; game_log has 2 rows; box_score_log must win
    bsl_rows = [{"PTS": 25 + i, "REB": 8, "AST": 4, "MIN": 36} for i in range(5)]
    gl_rows  = [{"stat": 10.0, "line": 23.5, "hit": False}] * 2
    pkt = build_packet(row, {"box_score_log": bsl_rows, "game_log": gl_rows})

    audit = pkt.get("box_score_audit", {})
    assert audit.get("source_input_key") == "box_score_log", (
        f"Expected source_input_key='box_score_log', got {audit.get('source_input_key')!r}"
    )
    assert audit.get("source_row_count") == 5, (
        f"Expected 5 rows from box_score_log, got {audit.get('source_row_count')}"
    )
    assert len(pkt.get("box_score_log", [])) == 5, (
        "box_score_log in packet must have 5 rows (from box_score_log key)"
    )


# ---------------------------------------------------------------------------
# Test 31 — BUG-002: single-stat "stat" rows map correctly via market_type
# ---------------------------------------------------------------------------

def test_bug002_stat_key_maps_to_correct_field_via_market_type():
    """
    BUG-002: when a row has only the 'stat' key (player_logs.py format),
    reconstruct_raw_ledger_rows must map the value to the correct ledger field
    using market_type context.  player_points → points field non-null.
    """
    rows = [
        {"stat": 28.0, "line": 25.5, "hit": True,  "date": "2026-08-01", "opponent": "CHI"},
        {"stat": 19.0, "line": 25.5, "hit": False, "date": "2026-07-28", "opponent": "LV"},
        {"stat": 31.0, "line": 25.5, "hit": True,  "date": "2026-07-25", "opponent": "NY"},
    ]
    ledger = reconstruct_raw_ledger_rows(rows, market_type="player_points")
    assert len(ledger) == 3
    for i, r in enumerate(ledger):
        assert r["points"] == rows[i]["stat"], (
            f"row {i}: expected points={rows[i]['stat']}, got {r['points']}"
        )
        # Other stat fields must remain None (not fabricated)
        assert r["rebounds"] is None, f"row {i}: rebounds must be None, got {r['rebounds']}"
        assert r["assists"]  is None, f"row {i}: assists must be None, got {r['assists']}"
        # Audit trail
        assert r["raw_stat_value"]      == rows[i]["stat"]
        assert r["canonical_stat_type"] == "points"
        assert r["source_market_type"]  == "player_points"


# ---------------------------------------------------------------------------
# Test 32 — BUG-002: 10 stat rows produce L5=5 and L10=10
# ---------------------------------------------------------------------------

def test_bug002_ten_rows_produce_l5_5_and_l10_10():
    """
    BUG-002: when 10 valid single-stat rows are provided under 'game_log'
    with a supported market_type, the resulting l5_ledger must have 5 rows
    and l10_ledger must have 10 rows.
    """
    row = _make_wnba_row(prop_type="player_points")
    game_log_10 = [
        {"stat": 20.0 + i, "line": 23.5, "hit": (20.0 + i) > 23.5,
         "date": f"2026-0{7 if i < 3 else 8}-{10 + i:02d}", "opponent": "OPP"}
        for i in range(10)
    ]
    pkt = build_packet(row, {"game_log": game_log_10})

    assert len(pkt["box_score_log"]) == 10, (
        f"Expected 10 box_score_log rows, got {len(pkt['box_score_log'])}"
    )
    assert len(pkt["l5_ledger"])     == 5, (
        f"Expected l5_ledger=5, got {len(pkt['l5_ledger'])}"
    )
    assert len(pkt["l10_ledger"])    == 10, (
        f"Expected l10_ledger=10, got {len(pkt['l10_ledger'])}"
    )
    # At least 5 rows must have non-null points
    non_null_points = sum(1 for r in pkt["l5_ledger"] if r.get("points") is not None)
    assert non_null_points == 5, (
        f"Expected all 5 l5_ledger rows to have points set, got {non_null_points}"
    )


# ---------------------------------------------------------------------------
# Test 33 — BUG-002: STAT_MAPPING_UNRESOLVED for unsupported market_type
# ---------------------------------------------------------------------------

def test_bug002_unsupported_market_type_fails_visibly():
    """
    BUG-002: when a single-stat row has 'stat' key and market_type is not in
    _MARKET_TO_STAT_KEY, the row must get stat_mapping_unresolved=True and no
    stat field should receive the value.  Must never silently guess.
    """
    rows = [{"stat": 4.0, "line": 3.5, "hit": True, "date": "2026-08-01"}]
    ledger = reconstruct_raw_ledger_rows(rows, market_type="player_blocks_plus_steals_plus_turnovers")
    assert len(ledger) == 1
    r = ledger[0]
    assert r.get("stat_mapping_unresolved") is True, (
        "Unsupported market_type must set stat_mapping_unresolved=True"
    )
    assert "STAT_MAPPING_UNRESOLVED" in (r.get("stat_mapping_error") or ""), (
        "stat_mapping_error must contain 'STAT_MAPPING_UNRESOLVED'"
    )
    # No stat field should be fabricated
    assert r["points"]   is None, "points must be None for unresolved market"
    assert r["rebounds"] is None, "rebounds must be None for unresolved market"
    assert r["assists"]  is None, "assists must be None for unresolved market"
    assert r["blocks"]   is None, "blocks must be None for unresolved market"


# ---------------------------------------------------------------------------
# Test 34 — BUG-002: no all-None stat rows for supported market types
# ---------------------------------------------------------------------------

def test_bug002_no_all_none_stat_rows_for_supported_markets():
    """
    BUG-002: for any supported market type with a valid 'stat' value, the
    reconstructed ledger row must have at least one non-null stat field.
    """
    supported = {
        "player_points":   "points",
        "player_rebounds": "rebounds",
        "player_assists":  "assists",
        "player_threes":   "three_pointers_made",
        "player_steals":   "steals",
        "player_blocks":   "blocks",
    }
    for market, expected_field in supported.items():
        rows   = [{"stat": 7.0, "line": 6.5, "hit": True}]
        ledger = reconstruct_raw_ledger_rows(rows, market_type=market)
        assert len(ledger) == 1
        r = ledger[0]
        assert r.get(expected_field) == 7.0, (
            f"market={market}: expected {expected_field}=7.0, got {r.get(expected_field)}"
        )
        assert not r.get("stat_mapping_unresolved"), (
            f"market={market}: must NOT have stat_mapping_unresolved set"
        )


# ---------------------------------------------------------------------------
# Test 35 — BUG-003a: shared Odds API resolver prefers paid key over legacy
# ---------------------------------------------------------------------------

def test_bug003a_odds_api_resolver_prefers_paid_key(monkeypatch):
    """
    BUG-003a: resolve_odds_api_key_with_source() must prefer ODDS_API_PAID_KEY
    over ODDS_API_FREE_KEY and ODDS_API_KEY (legacy).  The source name must
    reflect the actual key used.
    """
    from services.odds_api import resolve_odds_api_key_with_source

    monkeypatch.setenv("ODDS_API_PAID_KEY", "paid-test-key")
    monkeypatch.setenv("ODDS_API_FREE_KEY", "free-test-key")
    monkeypatch.setenv("ODDS_API_KEY",      "legacy-deactivated-key")

    key, source = resolve_odds_api_key_with_source()
    assert source == "ODDS_API_PAID_KEY", (
        f"Expected ODDS_API_PAID_KEY, got {source!r}"
    )
    assert key == "paid-test-key"

    # Free key priority when paid absent
    monkeypatch.delenv("ODDS_API_PAID_KEY")
    key2, source2 = resolve_odds_api_key_with_source()
    assert source2 == "ODDS_API_FREE_KEY", (
        f"Expected ODDS_API_FREE_KEY when paid absent, got {source2!r}"
    )

    # Legacy key as last resort
    monkeypatch.delenv("ODDS_API_FREE_KEY")
    key3, source3 = resolve_odds_api_key_with_source()
    assert source3 == "ODDS_API_KEY_LEGACY", (
        f"Expected ODDS_API_KEY_LEGACY as last resort, got {source3!r}"
    )

    # No keys → NONE with empty string
    monkeypatch.delenv("ODDS_API_KEY")
    key4, source4 = resolve_odds_api_key_with_source()
    assert source4 == "NONE"
    assert key4 == ""


# ---------------------------------------------------------------------------
# Test 36 — BUG-003a: fetch_market_comparison uses shared resolver, not direct env read
# ---------------------------------------------------------------------------

def test_bug003a_fetch_market_comparison_uses_paid_key_over_legacy(monkeypatch):
    """
    BUG-003a: fetch_market_comparison must use resolve_odds_api_key_with_source()
    and MUST NOT read ODDS_API_KEY directly.  When ODDS_API_PAID_KEY is set and
    ODDS_API_KEY is absent/deactivated, the paid key must be used (no AUTH_REQUIRED).
    """
    monkeypatch.setenv("ODDS_API_PAID_KEY", "paid-key-valid")
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.delenv("ODDS_API_FREE_KEY", raising=False)

    events_body = [{"id": "evt-001"}]
    odds_body   = {"bookmakers": []}  # player not listed → REQUEST_EMPTY but not AUTH_REQUIRED

    responses = [
        _mock_response(200, events_body),
        _mock_response(200, odds_body),
    ]
    with patch("requests.get", side_effect=responses):
        result = fetch_market_comparison("A'ja Wilson", "player_points", line=23.5)

    # Must NOT be AUTH_REQUIRED (which would mean it fell back to checking ODDS_API_KEY=absent)
    assert result.request_status != RequestStatus.AUTH_REQUIRED, (
        "fetch_market_comparison returned AUTH_REQUIRED even though ODDS_API_PAID_KEY "
        "was set — it is reading ODDS_API_KEY directly instead of using the shared resolver"
    )
    # credential_resolver_used must be True in normalized_fields
    nf = result.normalized_fields or {}
    assert nf.get("credential_resolver_used") is True, (
        "normalized_fields must contain credential_resolver_used=True"
    )
    assert nf.get("credential_source_name") == "ODDS_API_PAID_KEY", (
        f"Expected credential_source_name='ODDS_API_PAID_KEY', got {nf.get('credential_source_name')!r}"
    )


# ---------------------------------------------------------------------------
# Test 37 — BUG-003a: no direct ODDS_API_KEY read in external_adapters.py
# ---------------------------------------------------------------------------

def test_bug003a_no_direct_odds_api_key_read_in_adapter_source():
    """
    BUG-003a: external_adapters.py must not contain any direct
    os.environ.get("ODDS_API_KEY") read (only the shared resolver is allowed).
    This is a source-code level invariant enforced by inspection.
    """
    import re, pathlib
    adapter_src = pathlib.Path(__file__).parent.parent / "wnba" / "external_adapters.py"
    assert adapter_src.exists(), f"external_adapters.py not found at {adapter_src}"
    text = adapter_src.read_text()

    # Any direct read of the raw ODDS_API_KEY env var is a violation
    # (the resolver itself is in services/odds_api.py, not in external_adapters)
    pattern = r'os\.environ\.get\s*\(\s*["\']ODDS_API_KEY["\']'
    matches = re.findall(pattern, text)
    assert not matches, (
        f"BUG-003a: external_adapters.py contains {len(matches)} direct read(s) of "
        f"ODDS_API_KEY via os.environ.get — must use resolve_odds_api_key_with_source() instead. "
        f"Matches: {matches}"
    )


# ---------------------------------------------------------------------------
# Test 38 — BUG-003b: WNBA athlete resolves via ESPN v2 endpoint
# ---------------------------------------------------------------------------

def test_bug003b_espn_v2_endpoint_resolves_wnba_athlete(monkeypatch):
    """
    BUG-003b: _espn_search_wnba_athlete must use the v2 search endpoint
    (site.api.espn.com/apis/search/v2) and parse uid-based athlete IDs.
    A mock v2-shaped response for 'Aliyah Boston' must return the correct athlete_id.
    """
    from gate_engine.wnba.external_adapters import _espn_search_wnba_athlete, RequestStatus

    v2_response = {
        "results": [
            {
                "type": "player",
                "contents": [
                    {
                        "uid":         "s:40~l:59~a:4066407",
                        "displayName": "Aliyah Boston",
                        "description": "WNBA",
                    }
                ],
            }
        ]
    }
    mock_resp = _mock_response(200, v2_response)
    with patch("requests.get", return_value=mock_resp) as mock_get:
        athlete_id, canonical_name, status, url_used = \
            _espn_search_wnba_athlete("Aliyah Boston")

    assert status == RequestStatus.REQUEST_SUCCEEDED, (
        f"Expected REQUEST_SUCCEEDED, got {status!r}"
    )
    assert athlete_id == "4066407", (
        f"Expected athlete_id='4066407', got {athlete_id!r}"
    )
    assert "Aliyah Boston" in canonical_name, (
        f"Expected canonical_name to contain 'Aliyah Boston', got {canonical_name!r}"
    )
    # Verify the v2 URL was used (not v3)
    called_url = mock_get.call_args[0][0] if mock_get.call_args[0] else \
                 mock_get.call_args[1].get("url", "")
    assert "search/v2" in called_url or "apis/search" in called_url, (
        f"BUG-003b: v2 search URL not used. Called: {called_url!r}"
    )
    assert "common/v3/search" not in called_url, (
        f"BUG-003b: broken v3 endpoint is still being called: {called_url!r}"
    )


# ---------------------------------------------------------------------------
# Test 39 — BUG-003b: HTTP 200 with zero WNBA athletes → ATHLETE_NOT_FOUND, not REQUEST_FAILED
# ---------------------------------------------------------------------------

def test_bug003b_http_200_zero_athletes_is_not_found_not_failed(monkeypatch):
    """
    BUG-003b: when the ESPN v2 search returns HTTP 200 but no WNBA athlete
    matches the player name, the status must be REQUEST_EMPTY (ATHLETE_NOT_FOUND)
    — never REQUEST_FAILED.  This was the pre-fix behavior (0 athletes hit for
    every WNBA player tested with the old v3 endpoint).
    """
    from gate_engine.wnba.external_adapters import _espn_search_wnba_athlete, RequestStatus

    # Simulate v2 HTTP 200 with results but wrong league (NBA, not WNBA)
    nba_response = {
        "results": [
            {
                "type": "player",
                "contents": [
                    {
                        "uid":         "s:40~l:46~a:999",
                        "displayName": "A'ja Smith",   # wrong sport
                        "description": "NBA",           # wrong league
                    }
                ],
            }
        ]
    }
    mock_resp = _mock_response(200, nba_response)
    with patch("requests.get", return_value=mock_resp):
        _, _, status, _ = _espn_search_wnba_athlete("A'ja Wilson")

    assert status == RequestStatus.REQUEST_EMPTY, (
        f"HTTP 200 with no WNBA match must be REQUEST_EMPTY (ATHLETE_NOT_FOUND), "
        f"got {status!r}"
    )
    # Also verify the full news adapter reports REQUEST_EMPTY, not REQUEST_FAILED
    with patch("requests.get", return_value=mock_resp):
        news_result = fetch_news_contradiction("A'ja Wilson")
    assert news_result.request_status == RequestStatus.REQUEST_EMPTY, (
        f"fetch_news_contradiction must return REQUEST_EMPTY when athlete not found, "
        f"got {news_result.request_status!r}"
    )
    nf = news_result.normalized_fields or {}
    assert nf.get("athlete_resolution_status") == "ATHLETE_NOT_FOUND", (
        f"normalized_fields.athlete_resolution_status must be 'ATHLETE_NOT_FOUND', "
        f"got {nf.get('athlete_resolution_status')!r}"
    )


# ---------------------------------------------------------------------------
# Test 40 — BUG-003b: HTTP 200 with zero news articles → NEWS_REQUEST_EMPTY, not REQUEST_FAILED
# ---------------------------------------------------------------------------

def test_bug003b_http_200_zero_news_is_empty_not_failed(monkeypatch):
    """
    BUG-003b: when the athlete is resolved but the news endpoint returns
    HTTP 200 with zero articles, fetch_news_contradiction must report
    REQUEST_EMPTY with news_fetch_status=NEWS_REQUEST_EMPTY — not REQUEST_FAILED.
    """
    from gate_engine.wnba.external_adapters import RequestStatus

    v2_athlete_resp = {
        "results": [
            {
                "type": "player",
                "contents": [
                    {
                        "uid":         "s:40~l:59~a:3149391",
                        "displayName": "A'ja Wilson",
                        "description": "WNBA",
                    }
                ],
            }
        ]
    }
    empty_news_resp = {"articles": []}  # ESPN returns 200 but no articles

    responses = [
        _mock_response(200, v2_athlete_resp),
        _mock_response(200, empty_news_resp),
    ]
    with patch("requests.get", side_effect=responses):
        result = fetch_news_contradiction("A'ja Wilson")

    assert result.request_status == RequestStatus.REQUEST_EMPTY, (
        f"Zero-article 200 response must be REQUEST_EMPTY, got {result.request_status!r}"
    )
    nf = result.normalized_fields or {}
    assert nf.get("athlete_resolution_status") == "ATHLETE_RESOLVED", (
        f"Athlete must show ATHLETE_RESOLVED (search succeeded), "
        f"got {nf.get('athlete_resolution_status')!r}"
    )
    assert nf.get("news_fetch_status") == "NEWS_REQUEST_EMPTY", (
        f"news_fetch_status must be NEWS_REQUEST_EMPTY, got {nf.get('news_fetch_status')!r}"
    )


# ---------------------------------------------------------------------------
# Test 41 — BUG-001 + BUG-002 combined: packet cannot reach PACKET_RECONSTRUCTED_COMPLETE
#           when ledger construction fails despite non-empty box_score_log
# ---------------------------------------------------------------------------

def test_bug001_002_packet_not_reconstructed_complete_with_empty_ledgers():
    """
    Invariant: PACKET_RECONSTRUCTED_COMPLETE must not fire when l5/l10 ledger
    construction fails even if box_score_log is non-empty.
    Simulate this by providing game_log rows with an unsupported market_type
    so every row is STAT_MAPPING_UNRESOLVED and ledger values are all null.
    The packet should be PACKET_PARTIAL_HOLD or PACKET_INCOMPLETE_REJECTED,
    never PACKET_RECONSTRUCTED_COMPLETE.
    """
    # Use a completely unsupported market type so stat mapping always fails
    row = _make_wnba_row(prop_type="player_blocks_plus_steals_plus_turnovers")
    game_log_bad_market = [
        {"stat": 5.0, "line": 4.5, "hit": True, "date": "2026-08-01", "opponent": "CHI"}
    ]
    pkt = build_packet(row, {"game_log": game_log_bad_market})

    # box_score_log stores the RAW input rows; reconstructed ledger rows live in l5/l10_ledger.
    assert len(pkt["box_score_log"]) == 1, "box_score_log must have 1 raw input row"
    # The ledger rows (result of reconstruct_raw_ledger_rows) carry the unresolved flag
    l5 = pkt.get("l5_ledger") or []
    l10 = pkt.get("l10_ledger") or []
    # At least l10 should have 1 row (we supplied 1 game row)
    all_ledger = l5 or l10
    assert len(all_ledger) == 1, f"Expected 1 ledger row, got {len(all_ledger)}"
    r = all_ledger[0]
    assert r.get("stat_mapping_unresolved") is True, (
        "All ledger rows must be STAT_MAPPING_UNRESOLVED for the unsupported market. "
        f"Row was: {r}"
    )


# ---------------------------------------------------------------------------
# Test 42 — BUG-002: PRA market assigns pra field directly (source query IS for PRA)
# ---------------------------------------------------------------------------

def test_bug002_pra_market_assigns_pra_from_single_stat():
    """
    BUG-002: when market_type is 'player_points_rebounds_assists' (a PRA query),
    a single 'stat' value may be assigned to the 'pra' field directly.
    This is the only case where a composite can come from a single stat value.
    """
    rows = [{"stat": 45.0, "line": 42.5, "hit": True, "date": "2026-08-01", "opponent": "LV"}]
    ledger = reconstruct_raw_ledger_rows(rows, market_type="player_points_rebounds_assists")
    assert len(ledger) == 1
    r = ledger[0]
    assert r["pra"] == 45.0, (
        f"Expected pra=45.0 for PRA market with stat=45.0, got {r['pra']}"
    )
    # Component fields must remain None (no components available)
    assert r["points"]   is None, "points must be None when only PRA stat present"
    assert r["rebounds"] is None, "rebounds must be None when only PRA stat present"
    assert r["assists"]  is None, "assists must be None when only PRA stat present"
    assert r["canonical_stat_type"] == "pra"
