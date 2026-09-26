from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from v17 import cross_sport_winner_discovery as discovery
from v17 import daily_snapshot_runtime as runtime
from v17.daily_response_contract import (
    DAILY_ACQUISITION_DETAIL_TABLE,
    compact_cross_sport_discovery_audit,
    persist_acquisition_detail,
    read_acquisition_detail_page,
)
from v17.full_board_stabilization import publication_chain_audit


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.offset = 0
        self.end = 999
        self.run_id = None

    def select(self, *_args):
        return self

    def eq(self, column, value):
        if column == "run_id":
            self.run_id = value
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, limit):
        self.end = int(limit) - 1
        return self

    def range(self, offset, end):
        self.offset, self.end = int(offset), int(end)
        return self

    def upsert(self, payload, *_args, **_kwargs):
        for row in payload:
            key = (row["run_id"], row["family"], row["target_key"])
            self.db.acquisition_rows[key] = dict(row)
        return self

    def execute(self):
        if self.table == DAILY_ACQUISITION_DETAIL_TABLE:
            rows = sorted(
                (
                    row
                    for row in self.db.acquisition_rows.values()
                    if self.run_id is None or row["run_id"] == self.run_id
                ),
                key=lambda row: (row["family"], row["target_key"]),
            )
            return _Result(rows[self.offset:self.end + 1])
        return _Result([])


class _DB:
    def __init__(self, *, fail_acquisition=False):
        self.acquisition_rows = {}
        self.fail_acquisition = fail_acquisition

    def table(self, name):
        if name == DAILY_ACQUISITION_DETAIL_TABLE and self.fail_acquisition:
            raise ConnectionError("audit store unavailable")
        return _Query(self, name)


def _target(family, sport_id, league):
    return discovery.DiscoveryTarget(
        family=family,
        provider="TEST_PROVIDER",
        league=league,
        sport_id=sport_id,
    )


def test_every_configured_target_and_boxing_sentinel_gets_one_final_state():
    targets = {
        "NFL": (_target("NFL", 2, "NFL"), _target("NFL", 102, "NFL_PRESEASON")),
        "BOXING": (),
    }

    inventory = discovery.discover_winner_slate(
        requested_slate_date="2026-09-26",
        requested_timezone="UTC",
        fetch_sport_events=lambda _family, target: ([{
            "id": "nfl-1",
            "home_team": "A",
            "away_team": "B",
            "commence_time": "2026-09-26T20:00:00Z",
            "status": "SCHEDULED",
        }] if target.sport_id == 2 else []),
        supported_sports=("NFL", "BOXING"),
        discovery_targets=targets,
        now=datetime(2026, 9, 26, 12, tzinfo=timezone.utc),
        budget_seconds=30,
    )

    assert len(inventory.acquisition_details) == 3
    keyed = {(row["family"], row["target_key"]): row for row in inventory.acquisition_details}
    assert keyed[("NFL", targets["NFL"][0].target_key)]["final_state"] == "EVENTS_RETURNED"
    assert keyed[("NFL", targets["NFL"][1].target_key)]["final_state"] == "NO_EVENTS_RETURNED"
    boxing = keyed[("BOXING", "NO_CONFIGURED_DISCOVERY_FEED")]
    assert boxing["exhaustion_status"] == "NO_CONFIGURED_PATH"
    assert boxing["can_execute"] is False
    assert all("raw" not in row and "price" not in row for row in inventory.acquisition_details)


def test_acquisition_persistence_is_idempotent_sanitized_and_bounded():
    db = _DB()
    detail = {
        "family": "NFL",
        "target_key": "TEST_PROVIDER|2|NFL|REGULAR_SEASON",
        "provider": "TEST_PROVIDER",
        "league": "NFL",
        "regime": "REGULAR_SEASON",
        "provider_sport_id": 2,
        "sport_key": None,
        "final_state": "EVENTS_RETURNED",
        "provider_status": "PROVIDER_SUCCEEDED",
        "fallback_status": "FALLBACK_NOT_REPORTED_BY_COMPOSITE_FEED",
        "exhaustion_status": "PATHS_NOT_EXHAUSTED",
        "events_returned": 2,
        "duplicate_rows_suppressed": 0,
        "blocker_code": "HTTP_401:secret=value",
        "raw_payload": {"secret": "must-not-persist"},
        "price": -120,
        "probability": 0.72,
        "participant_name": "Must Not Persist",
        "can_execute": False,
    }

    expected = [{"family": "NFL", "target_key": detail["target_key"]}]
    first = persist_acquisition_detail(
        db, run_id="run-1", details=[detail], expected_targets=expected
    )
    second = persist_acquisition_detail(
        db, run_id="run-1", details=[detail], expected_targets=expected
    )
    assert first["board_completeness"] is True
    assert second["details_persisted"] == 1
    assert len(db.acquisition_rows) == 1
    stored = next(iter(db.acquisition_rows.values()))
    assert not {"raw_payload", "price", "probability", "participant_name"}.intersection(stored)
    assert stored["blocker_code"] == "HTTP_401"
    assert "secret" not in repr(stored)
    assert stored["can_execute"] is False

    page = read_acquisition_detail_page(db, run_id="run-1", offset=0, limit=500)
    assert page["limit"] == 100
    assert page["returned"] == 1
    assert page["can_execute"] is False


def test_union_provenance_primary_failure_fallback_success_and_all_paths_fail():
    from v17 import cross_sport_discovery_feed as feed

    target = _target("NFL", 2, "NFL")

    def primary_failed(_family, _target):
        raise discovery.DiscoveryFeedError("PRIMARY_QUOTA_EXHAUSTED")

    def fallback_succeeded(_family, _target):
        return [{
            "id": "nfl-fallback-1",
            "home_team": "A",
            "away_team": "B",
            "commence_time": "2026-09-26T20:00:00Z",
            "status": "SCHEDULED",
        }]

    recovered = discovery.discover_winner_slate(
        requested_slate_date="2026-09-26",
        requested_timezone="UTC",
        fetch_sport_events=feed.union_feed(primary_failed, fallback_succeeded),
        supported_sports=("NFL",),
        discovery_targets={"NFL": (target,)},
        now=datetime(2026, 9, 26, 12, tzinfo=timezone.utc),
        budget_seconds=30,
    )
    detail = recovered.acquisition_details[0]
    assert detail["provider_status"] == "PROVIDER_FAILED"
    assert detail["fallback_status"] == "FALLBACK_SUCCEEDED"
    assert detail["exhaustion_status"] == "PATHS_NOT_EXHAUSTED"
    assert detail["final_state"] == "EVENTS_RETURNED"

    def fallback_failed(_family, _target):
        raise discovery.DiscoveryFeedError("FALLBACK_AUTH_FAILED")

    exhausted = discovery.discover_winner_slate(
        requested_slate_date="2026-09-26",
        requested_timezone="UTC",
        fetch_sport_events=feed.union_feed(primary_failed, fallback_failed),
        supported_sports=("NFL",),
        discovery_targets={"NFL": (target,)},
        now=datetime(2026, 9, 26, 12, tzinfo=timezone.utc),
        budget_seconds=30,
    )
    detail = exhausted.acquisition_details[0]
    assert detail["provider_status"] == "PROVIDER_FAILED"
    assert detail["fallback_status"] == "FALLBACK_FAILED"
    assert detail["exhaustion_status"] == "PROVIDER_PATHS_EXHAUSTED"
    assert detail["final_state"] == "PROVIDER_RATE_LIMITED"


@pytest.mark.parametrize("mode", ["missing", "extra"])
def test_board_completeness_requires_exact_independent_target_set(mode):
    db = _DB()
    detail = {
        "family": "NFL",
        "target_key": "TEST_PROVIDER|2|NFL|REGULAR_SEASON",
        "provider": "TEST_PROVIDER",
        "league": "NFL",
        "regime": "REGULAR_SEASON",
        "provider_sport_id": 2,
        "final_state": "NO_EVENTS_RETURNED",
        "provider_status": "PROVIDER_SUCCEEDED",
        "fallback_status": "FALLBACK_NOT_APPLICABLE",
        "exhaustion_status": "PATHS_NOT_EXHAUSTED",
        "events_returned": 0,
        "can_execute": False,
    }
    expected = [{"family": "NFL", "target_key": detail["target_key"]}]
    details = [] if mode == "missing" else [detail, {**detail, "target_key": "EXTRA"}]
    result = persist_acquisition_detail(
        db, run_id="run-mismatch", details=details, expected_targets=expected
    )
    assert result["board_completeness"] is False
    assert result["status"] in {"NO_DETAILS", "TARGET_SET_MISMATCH"}


def _completed_moneyline_row():
    return {
        "lane": "MONEYLINE",
        "identity": {"sport": "NFL", "official_event_id": "nfl-1"},
        "terminal": True,
        "row_status": "COMPLETED",
        "probability_publishable": True,
        "result": {
            "calibrated_probability": 0.62,
            "calibrated_lower_bound": 0.57,
            "probability_publishable": True,
            "rank_eligible": True,
            "can_execute": False,
        },
        "terminal_reduction": {
            "lowest_stage_terminal": "COMPLETED",
            "final_terminal": "COMPLETED",
            "terminal_upgraded_from_rejection": False,
            "can_execute": False,
        },
        "can_execute": False,
    }


def _scan_packet():
    return {
        "discovery": {"sports_queried": ["NFL"], "acquisition_details_count": 1},
        "rows": [],
        "reconciliation": {"row_reconciliation": "PASS"},
        "acquisition_details": [{
            "family": "NFL",
            "target_key": "TEST_PROVIDER|2|NFL|REGULAR_SEASON",
            "provider": "TEST_PROVIDER",
            "league": "NFL",
            "regime": "REGULAR_SEASON",
            "provider_sport_id": 2,
            "sport_key": None,
            "final_state": "EVENTS_RETURNED",
            "provider_status": "PROVIDER_SUCCEEDED",
            "fallback_status": "FALLBACK_NOT_REPORTED_BY_COMPOSITE_FEED",
            "exhaustion_status": "PATHS_NOT_EXHAUSTED",
            "events_returned": 1,
            "duplicate_rows_suppressed": 0,
            "blocker_code": None,
            "can_execute": False,
        }],
        "expected_acquisition_targets": [{
            "family": "NFL",
            "target_key": "TEST_PROVIDER|2|NFL|REGULAR_SEASON",
        }],
        "can_execute": False,
    }


@pytest.mark.parametrize("response_mode", ["COMPACT", "FULL"])
def test_daily_modes_return_bounded_acquisition_reference_not_inline_details(
    monkeypatch, response_mode
):
    monkeypatch.setattr(
        runtime,
        "_cross_sport_moneyline_rows",
        lambda *_args, **_kwargs: ([_completed_moneyline_row()], _scan_packet()),
    )
    payload = runtime.run_daily_snapshot(
        runtime.DailySnapshotRequest(
            requested_slate_date=(datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat(),
            requested_timezone="UTC",
            lanes=["MONEYLINE"],
            response_mode=response_mode,
        ),
        db=_DB(),
        market_api=object(),
        event_api=object(),
    )
    audit = payload["cross_sport_discovery_audit"]
    assert "acquisition_details" not in audit
    assert audit["acquisition_detail_ref"]["details_count"] == 1
    assert audit["acquisition_detail_ref"]["details_inlined"] is False
    assert audit["reconciliation"]["board_completeness"] == "PASS"
    assert payload["can_execute"] is False


def test_acquisition_persistence_failure_blocks_completeness_not_probability(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "_cross_sport_moneyline_rows",
        lambda *_args, **_kwargs: ([_completed_moneyline_row()], _scan_packet()),
    )
    payload = runtime.run_daily_snapshot(
        runtime.DailySnapshotRequest(
            requested_slate_date=(datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat(),
            requested_timezone="UTC",
            lanes=["MONEYLINE"],
            response_mode="FULL",
        ),
        db=_DB(fail_acquisition=True),
        market_api=object(),
        event_api=object(),
    )
    audit = payload["cross_sport_discovery_audit"]
    assert audit["reconciliation"]["board_completeness"] == "BLOCKED"
    assert any("ACQUISITION_DETAIL_PERSISTENCE_UNAVAILABLE" in blocker for blocker in payload["blockers"])
    assert payload["rows"][0]["result"]["calibrated_probability"] == 0.62
    assert payload["rows"][0]["probability_publishable"] is True


def _audit_row(**overrides):
    row = {
        "probability_package_valid": True,
        "dynamic_calibration_complete": True,
        "probability_audit_passed": True,
        "event_governor_complete": True,
        "calibrated_probability": 0.61,
        "calibrated_lower_bound": 0.56,
        "rank_eligible": True,
        "probability_publishable": True,
        "can_execute": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
    }
    row.update(overrides)
    return row


def test_nested_final_refresh_is_authoritative_and_pregame_is_never_proof():
    nested = _audit_row(llp_governance={"final_refresh": {
        "final_refresh_status": "PASS",
        "reasons": [],
        "probability_invalidated": False,
        "rerun_required": False,
        "can_execute": False,
    }})
    assert publication_chain_audit(nested).status == "PASS"

    pregame_only = publication_chain_audit(_audit_row(official_event_status="PREGAME"))
    assert pregame_only.status == "BLOCKED"
    assert pregame_only.first_blocker == "FINAL_REFRESH_NOT_COMPLETE"


@pytest.mark.parametrize(
    "refresh,legacy,blocker",
    [
        ("PASS", False, "FINAL_REFRESH_EVIDENCE_CONFLICT"),
        ("not-an-object", None, "FINAL_REFRESH_EVIDENCE_MALFORMED"),
    ],
)
def test_nested_refresh_conflict_or_malformed_fails_closed(refresh, legacy, blocker):
    row = _audit_row(llp_governance={"final_refresh": refresh})
    if legacy is not None:
        row["final_refresh_passed"] = legacy
        row["llp_governance"]["final_refresh"] = {
            "final_refresh_status": "PASS",
            "reasons": [],
            "probability_invalidated": False,
            "rerun_required": False,
            "can_execute": False,
        }
    audit = publication_chain_audit(row)
    assert audit.status == "BLOCKED"
    assert audit.first_blocker == blocker
    assert audit.rank_eligible is False


def test_legacy_refresh_is_used_only_when_nested_refresh_is_absent():
    legacy = publication_chain_audit(_audit_row(final_refresh_status="PASS"))
    assert legacy.status == "PASS"
    nested_absent = publication_chain_audit(
        _audit_row(llp_governance={"status": "PASS"}, final_refresh_passed=True)
    )
    assert nested_absent.status == "PASS"


def test_sql_store_is_rls_fail_closed_and_contains_no_numeric_authority_columns():
    sql = Path("v17/sql/20260926_v17_daily_acquisition_detail.sql").read_text()
    lowered = sql.lower()
    assert "primary key (run_id, family, target_key)" in lowered
    assert "enable row level security" in lowered
    assert "revoke all" in lowered
    assert "can_execute = false" in lowered
    assert " calibrated_probability " not in lowered
    assert " price " not in lowered
