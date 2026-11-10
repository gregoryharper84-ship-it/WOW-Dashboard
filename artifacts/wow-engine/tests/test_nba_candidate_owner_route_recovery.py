"""Fail-closed regression for NBA owner-routing proposal and 27-row board.
This does not claim fitted certification or production Action acceptance.
"""
import json
import re
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
MIGRATION = ENGINE / "migrations/20261026_nba_candidate_research_owner_route.sql"
FIXTURE = ENGINE / "v17/fixtures/nba_oct26_historical_27_offer_replay.json"
NBA_EXACT_ROUTES = {"POINTS", "REBOUNDS", "ASSISTS", "PRA",
                    "POINTS_REBOUNDS", "POINTS_ASSISTS", "REBOUNDS_ASSISTS"}


def test_nba_ownership_is_exact_and_still_nonexecuting():
    sql = MIGRATION.read_text()
    match = re.search(
        r"elsif v_sport='NBA' and v_key in \((.*?)\) then\s*"
        r"--[^\n]*\n\s*v_controller := 'wow.nba-player-prop-probability-expert'",
        sql, re.S,
    )
    assert match is not None
    assert set(re.findall(r"'([A-Z_]+)'", match.group(1))) == NBA_EXACT_ROUTES
    assert "THREE_POINTERS_MADE" not in match.group(1)
    assert "'can_execute',false" in sql
    assert "'MODEL_UNAVAILABLE'" in sql
    assert "security definer" not in sql.lower()
    assert "grant " not in sql.lower().replace("does not change grants/rls", "")


def test_preexisting_sport_routes_remain_owned():
    sql = MIGRATION.read_text()
    for sport, owner in (
        ("MLB", "wow.mlb-pitcher-outs-workload-expert"),
        ("WNBA", "wow.wnba-player-prop-probability-expert"),
        ("NFL", "wow.nfl-player-prop-probability-expert"),
    ):
        assert f"v_sport='{sport}'" in sql
        assert owner in sql


def test_historical_inventory_is_exact_and_no_offer_conversion():
    board = json.loads(FIXTURE.read_text())
    rows = board["rows"]
    assert len(rows) == 27
    assert [r["source_index"] for r in rows] == list(range(1, 28))
    assert len({(r["player"], r["original_offer"]) for r in rows}) == 27
    assert len({r["player"] for r in rows}) == 9
    assert sum(r["offer_kind"] == "AT_LEAST" for r in rows) == 22
    assert sum(r["offer_kind"] == "OVER" for r in rows) == 4
    assert sum(r["offer_kind"] == "COMPOUND" for r in rows) == 1
    assert all(re.fullmatch(r"\d+\+ (assists|rebounds|points)", r["original_offer"])
               for r in rows if r["offer_kind"] == "AT_LEAST")
    assert all(r["route"] == "DOUBLE_DOUBLE" for r in rows if r["offer_kind"] == "COMPOUND")
    assert board["offer_conversions_authorized"] is False
    assert board["canonical_event_ids_verified"] is False
    assert board["can_execute"] is False
    assert board["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_historical_eight_are_accounted_for_without_probabilities():
    rows = json.loads(FIXTURE.read_text())["rows"]
    retried = [r for r in rows if r["backend_retry_row_key"]]
    assert len(retried) == 8
    assert len({r["backend_retry_row_key"] for r in retried}) == 8
    assert all(r["acceptance_state"] == "BACKEND_HELD_MODEL_UNAVAILABLE" for r in retried)
    assert sum(r["acceptance_state"] == "NOT_SUBMITTED" for r in rows) == 19
    assert all(r["prediction_id"] is None for r in rows)


def test_backend_receipt_lines_are_not_treated_as_authorized_at_least_translations():
    rows = json.loads(FIXTURE.read_text())["rows"]
    observed = [row for row in rows if row["observed_backend_request"] is not None]
    assert len(observed) == 8
    at_least = [row for row in observed if row["offer_kind"] == "AT_LEAST"]
    assert len(at_least) == 6
    for row in at_least:
        threshold = int(row["original_offer"].split("+", 1)[0])
        assert row["observed_backend_request"]["submitted_line"] == threshold - 0.5
        assert row["threshold_semantics_certified"] == "NOT_CERTIFIED_HALF_LINE_CONVERSION"
    exact_over = [row for row in observed if row["offer_kind"] == "OVER"]
    assert len(exact_over) == 2
    assert all(row["threshold_semantics_certified"] == "EXACT_OVER_LINE_RECORDED" for row in exact_over)
    assert all(row["observed_backend_request"]["model_evaluated"] is False for row in observed)
    assert all(row["observed_backend_request"]["terminal_code"] == "MODEL_UNAVAILABLE" for row in observed)
    assert all(row["observed_backend_request"]["acquisition_mode"] == "NOT_ATTEMPTED_ROUTE_BLOCKED" for row in observed)
    assert all(row["observed_backend_request"] is None for row in rows if row["acceptance_state"] == "NOT_SUBMITTED")
