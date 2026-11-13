from datetime import datetime, timezone

import pytest
from fastapi import Depends, FastAPI

from v17 import daily_snapshot_runtime as daily
from v17.llp_team_intelligence_board import (
    ExternalResearchBoardRequest,
    install_llp_team_intelligence_board,
    read_llp_team_intelligence_board,
)

START = datetime(2026, 11, 12, 23, 0, tzinfo=timezone.utc)


def _manifest():
    return {
        "contract": "LLP_DAILY_RUN_MANIFEST_V1",
        "expected_row_count": 2,
        "expected_supported_row_indexes_by_sport": {
            "NFL": [0], "NBA": [1], "NHL": [],
        },
        "expected_supported_event_counts_by_sport": {
            "NFL": 1, "NBA": 1, "NHL": 0,
        },
        "cross_sport_discovery": {
            "row_reconciliation": "PASS",
            "board_completeness": "PASS",
            "acquisition_detail_reconciliation": "PASS",
            "source_blockers": [],
            "families_without_configured_feed": [],
        },
    }


def _row(index, sport, event_id):
    return {
        "lane": "MONEYLINE",
        "row_index": index,
        "row_status": "HELD",
        "detail": {
            "identity": {
                "sport": sport,
                "official_event_id": event_id,
                "home_team": f"{sport} Home",
                "away_team": f"{sport} Away",
                "commence_time_utc": START.isoformat(),
            },
            "result": {
                "model_status": "MODEL_UNAVAILABLE",
                "code": "MODEL_UNAVAILABLE",
                "can_execute": False,
            },
            "daily_request_context": {"daily_run_manifest": _manifest()},
        },
    }


def test_daily_run_manifest_records_expected_sport_rows_and_discovery_reconciliation():
    builder = getattr(daily, "_build_daily_run_manifest", None)
    assert callable(builder), "Daily must persist a reconciled run manifest before row detail writes"
    rows = [_row(0, "NFL", "nfl-1"), _row(1, "NBA", "nba-1")]
    audit = {
        "rows": [
            {"sport": "NFL", "event_key": "NFL:nfl-1"},
            {"sport": "NBA", "event_key": "NBA:nba-1"},
        ],
        "reconciliation": {
            "row_reconciliation": "PASS",
            "board_completeness": "PASS",
            "acquisition_detail_reconciliation": "PASS",
            "source_blockers": [],
            "families_without_configured_feed": [],
        },
        "acquisition_detail_persistence": {
            "status": "PERSISTED",
            "detail_available": True,
            "board_completeness": True,
            "blockers": [],
        },
    }
    manifest = builder(
        rows=rows,
        cross_sport_audit=audit,
        lane_reconciliation={"MONEYLINE": {"row_reconciliation": "PASS"}},
        blockers=[],
    )
    assert manifest["expected_row_count"] == 2
    assert manifest["expected_supported_row_indexes_by_sport"] == {
        "NFL": [0], "NBA": [1], "NHL": [],
    }
    assert manifest["expected_supported_event_counts_by_sport"] == {
        "NFL": 1, "NBA": 1, "NHL": 0,
    }
    assert manifest["cross_sport_discovery"]["row_reconciliation"] == "PASS"


def test_board_reads_every_page_and_returns_one_disposition_per_supported_event(monkeypatch):
    rows = [_row(0, "NFL", "nfl-1"), _row(1, "NBA", "nba-1")]
    calls = []

    def fake_read(db, *, run_id, offset, limit):
        calls.append(offset)
        selected = rows[offset:offset + 1]
        return {
            "run_id": run_id,
            "status": "OK",
            "rows": selected,
            "offset": offset,
            "returned": len(selected),
            "next_offset": offset + 1 if offset + 1 < len(rows) else None,
            "blockers": [],
        }

    monkeypatch.setattr(
        "v17.llp_team_intelligence_board.read_row_detail_page", fake_read,
    )
    result = read_llp_team_intelligence_board(
        object(), run_id="run-123",
        request=ExternalResearchBoardRequest(external_research=[]),
        now_utc=datetime(2026, 11, 8, 16, 0, tzinfo=timezone.utc),
    )
    assert calls == [0, 1]
    assert result["board_complete"] is True
    assert len(result["events"]) == 2
    assert {e["selection_disposition"]["status"] for e in result["events"]} == {"HELD"}
    assert result["counts_by_disposition"] == {
        "QUALIFIED": 0, "PROVISIONAL": 0, "HELD": 2,
    }
    assert result["source_mutated"] is False
    assert result["nfl_pickem_mutated"] is False
    assert result["terminal_decision_generated"] is False
    assert result["can_execute"] is False


def test_missing_page_or_row_index_gap_makes_board_incomplete(monkeypatch):
    row = _row(0, "NFL", "nfl-1")

    def fake_read(db, *, run_id, offset, limit):
        if offset == 0:
            return {
                "run_id": run_id, "status": "OK", "rows": [row],
                "offset": 0, "returned": 1, "next_offset": None, "blockers": [],
            }
        raise AssertionError("The board must stop at the end of the persisted page sequence")

    monkeypatch.setattr(
        "v17.llp_team_intelligence_board.read_row_detail_page", fake_read,
    )
    result = read_llp_team_intelligence_board(
        object(), run_id="run-123",
        request=ExternalResearchBoardRequest(external_research=[]),
        now_utc=datetime(2026, 11, 8, 16, 0, tzinfo=timezone.utc),
    )
    assert result["board_complete"] is False
    assert "DAILY_ROW_COUNT_MISMATCH" in result["board_blockers"]


def test_unresolved_identity_is_preserved_as_held_placeholder(monkeypatch):
    row = _row(0, "NFL", "")
    row["detail"]["identity"]["home_team"] = None
    row["detail"]["identity"]["away_team"] = None
    manifest = _manifest()
    manifest["expected_row_count"] = 1
    manifest["expected_supported_row_indexes_by_sport"] = {
        "NFL": [0], "NBA": [], "NHL": [],
    }
    manifest["expected_supported_event_counts_by_sport"] = {
        "NFL": 1, "NBA": 0, "NHL": 0,
    }
    row["detail"]["daily_request_context"]["daily_run_manifest"] = manifest

    def fake_read(db, *, run_id, offset, limit):
        return {
            "run_id": run_id, "status": "OK", "rows": [row],
            "offset": 0, "returned": 1, "next_offset": None, "blockers": [],
        }

    monkeypatch.setattr(
        "v17.llp_team_intelligence_board.read_row_detail_page", fake_read,
    )
    result = read_llp_team_intelligence_board(
        object(), run_id="run-123",
        request=ExternalResearchBoardRequest(external_research=[]),
        now_utc=datetime(2026, 11, 8, 16, 0, tzinfo=timezone.utc),
    )
    assert len(result["events"]) == 1
    assert result["events"][0]["selection_disposition"]["status"] == "HELD"
    assert "EVENT_IDENTITY_UNRESOLVED" in result["events"][0]["selection_disposition"]["reason_codes"]


def test_board_installer_is_authenticated_idempotent_and_additive():
    app = FastAPI()
    dependency = Depends(lambda: None)
    assert install_llp_team_intelligence_board(
        app, auth_dependency=dependency, db_client_fn=lambda: object(),
    ) is True
    assert install_llp_team_intelligence_board(
        app, auth_dependency=dependency, db_client_fn=lambda: object(),
    ) is False
    route = next(r for r in app.router.routes if r.path.endswith("/board"))
    assert "POST" in route.methods
    assert route.operation_id == "readLlpV17TeamIntelligenceBoard"
    assert len(route.dependencies) == 1
