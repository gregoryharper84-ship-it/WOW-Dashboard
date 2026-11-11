"""Nonpublishing exact-once NFL multi-date transport adapter regressions."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from v17.cross_sport_acceptance_factory import Fixture, SourceInventory
from v17.nfl_multiday_snapshot_adapter import (
    AUDIENCE, AuthenticatedSnapshotResponse, execute_nfl_week_snapshot_preflight,
)

BASE = datetime(2026, 10, 15, tzinfo=timezone.utc)
SHA = "a" * 64
NOW = BASE - timedelta(minutes=30)


def _inputs():
    fixtures = tuple(
        Fixture("NFL", f"g{i}", BASE + timedelta(days=i % 4, hours=i % 5), "SCHEDULED")
        for i in range(13)
    )
    inventory = SourceInventory("official", SHA, BASE - timedelta(hours=1), fixtures)
    selection = {
        "status": "COHORT_ELIGIBLE_FOR_TEST",
        "selected_week": {"sport": "NFL", "season_key": "2026", "week_key": "7"},
        "cohort": {
            "status": "COHORT_ELIGIBLE_FOR_TEST",
            "canonical_event_ids": [f"g{i}" for i in range(13)],
            "source_id": "official",
            "source_payload_sha256": SHA,
        },
    }
    return selection, inventory


def _run(post, selection=None, inventory=None, *, now=NOW):
    base_selection, base_inventory = _inputs()
    return execute_nfl_week_snapshot_preflight(
        base_selection if selection is None else selection,
        base_inventory if inventory is None else inventory,
        independently_authenticated_source_sha256=SHA,
        trusted_now=now,
        post_daily_snapshot=post,
    )


def _response(payload, *, authenticated=True, status=200):
    day = payload["requested_slate_date"]
    # A deterministic mock date inventory matching the synthetic event schedule.
    _, inventory = _inputs()
    expected = [
        f for f in inventory.fixtures
        if f.start_utc.astimezone(__import__("zoneinfo").ZoneInfo("America/Chicago")).date().isoformat() == day
    ]
    n = len(expected)
    result = {
        "requested_slate_date": day, "requested_timezone": "America/Chicago",
        "run_id": "daily-" + day, "run_status": "COMPLETED", "terminal": True,
        "automation_auth": "GITHUB_ACTIONS_OIDC",
        "can_execute": False, "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "reconciliation": {"balanced": True},
        "cross_sport_discovery_audit": {"reconciliation": {
            "row_reconciliation": "PASS", "events_discovered": n, "events_accounted": n,
        }},
        "rows": [
            {"lane": "MONEYLINE", "can_execute": False,
             "identity": {"sport": "NFL", "official_event_id": f.official_event_id},
             "result": {}}
            for f in expected
        ],
    }
    return AuthenticatedSnapshotResponse(status, AUDIENCE, authenticated, result)


def test_serialized_complete_week_nonpublishing():
    calls = []

    def post(payload):
        calls.append(dict(payload))
        return _response(payload)

    result = _run(post)
    assert result["status"] == "WEEK_SNAPSHOT_RECONCILED_NOT_ACTION_OR_MODEL_CERTIFIED"
    assert result["nfl_rows"] == 13
    assert len(calls) >= 3
    assert len({x["requested_slate_date"] for x in calls}) == len(calls)
    assert all(x["lanes"] == ["MONEYLINE"] and x["max_props"] == 0 for x in calls)
    assert all(x["max_team_events"] == 32 and x["response_mode"] == "FULL" for x in calls)
    assert all(x["requested_timezone"] == "America/Chicago" for x in calls)
    assert result["can_execute"] is False and result["probability_publishable"] is False


def test_stale_inventory_blocks_dispatch():
    selection, inventory = _inputs()
    inventory = replace(inventory, fetched_at=NOW-timedelta(hours=7))
    calls = []
    result = _run(lambda p: calls.append(p), selection, inventory)
    assert result["status"] == "PREFLIGHT_HELD"
    assert not calls


def test_started_fixture_blocks_dispatch():
    selection, inventory = _inputs()
    inventory = replace(inventory, fixtures=(replace(inventory.fixtures[0], start_utc=NOW),) + inventory.fixtures[1:])
    calls = []
    result = _run(lambda p: calls.append(p), selection, inventory)
    assert result["status"] == "PREFLIGHT_HELD"
    assert not calls


def test_unauthenticated_https_hold_stops_later_dates():
    calls = []

    def post(p):
        calls.append(p)
        return _response(p, authenticated=False)

    result = _run(post)
    assert result["status"] == "PREFLIGHT_HELD"
    assert result["blockers"] == ["NFL_WEEK_AUTHENTICATED_TRANSPORT_REQUIRED"]
    assert len(calls) == 1


def test_http_error_hold_stops_later_dates():
    calls = []

    def post(p):
        calls.append(p)
        return _response(p, status=503)

    result = _run(post)
    assert result["status"] == "PREFLIGHT_HELD"
    assert len(calls) == 1


def test_ambiguous_network_error_never_retries():
    calls = []

    def post(p):
        calls.append(p)
        raise TimeoutError("response outcome unknown")

    result = _run(post)
    assert result["blockers"] == ["NFL_WEEK_SNAPSHOT_TRANSPORT_UNVERIFIED"]
    assert len(calls) == 1


def test_unverified_plain_dict_receipt_rejected():
    result = _run(lambda p: _response(p).response)
    assert result["blockers"] == ["NFL_WEEK_AUTHENTICATED_TRANSPORT_REQUIRED"]


def test_mismatched_date_hold():
    def post(p):
        valid = _response(p)
        invalid = dict(valid.response)
        invalid["requested_slate_date"] = "2026-11-01"
        return replace(valid, response=invalid)

    result = _run(post)
    assert result["blockers"] == ["NFL_WEEK_RESPONSE_DATE_MISMATCH"]


def test_degraded_daily_terminal_hold():
    def post(p):
        valid = _response(p)
        invalid = dict(valid.response)
        invalid["run_status"] = "COMPLETED_WITH_ACQUISITION_BLOCKERS"
        return replace(valid, response=invalid)

    result = _run(post)
    assert result["status"] == "PREFLIGHT_HELD"
    assert result["blockers"] == ["NFL_WEEK_DAILY_AUTH_OR_TERMINAL_INVALID"]


def test_duplicate_event_row_hold():
    def post(p):
        valid = _response(p)
        invalid = dict(valid.response)
        rows = list(invalid["rows"])
        rows.append(rows[0])
        invalid["rows"] = rows
        return replace(valid, response=invalid)

    result = _run(post)
    assert result["status"] == "PREFLIGHT_HELD"


def test_model_budget_terminal_hold():
    def post(p):
        valid = _response(p)
        invalid = dict(valid.response)
        rows = [dict(row) for row in invalid["rows"]]
        rows[0]["result"] = {"code": "MODEL_INVOCATION_BUDGET_REACHED"}
        invalid["rows"] = rows
        return replace(valid, response=invalid)

    result = _run(post)
    assert result["status"] == "PREFLIGHT_HELD"
