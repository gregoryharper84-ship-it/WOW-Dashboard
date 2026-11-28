"""Integrated regression for draft PRs #1812 and #1822.

These tests exercise the sport-scoped raw-id filter and the official-publication
guard together. They fail on current main, where a shared raw id drops non-MLB
events and a cross-sport scorer claim can complete without publication proof.
They pass only when both repairs are present on the base Daily callback and the
installed parity wrapper.

No sporting probability, calibration floor, or execution flag is invented here.
can_execute stays false.
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from v17 import daily_snapshot_runtime as runtime
from v17.daily_snapshot_runtime import DailySnapshotRequest, run_daily_snapshot
from v17.team_event_sport_parity import install_cross_sport_discovery_evidence_handoff

FUTURE_DT = datetime.now(timezone.utc) + timedelta(hours=30)
FUTURE = FUTURE_DT.isoformat()
SLATE_DATE = FUTURE_DT.astimezone(ZoneInfo("America/Chicago")).date().isoformat()
SHARED_ID = "1"
WNBA_ALIAS = "espn-401777001"


def _proven():
    return {
        "code": "FINAL_APPROVED",
        "terminal_label": "FINAL_APPROVED",
        "probability_publishable": True,
        "rank_eligible": True,
        "can_execute": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "llp_probability_audit_result": "PASS_PROBABILITY_AUDIT",
        "event_mutex_status": "PASS",
        "calibrated_probability": 0.62,
        "calibrated_lower_bound": 0.58,
        "llp_governance": {
            "probability_publishable": True,
            "rank_eligible": True,
            "global_terminal_reducer": "V17_TERMINAL_REDUCER",
            "can_execute": False,
            "probability_audit_result": "PASS_PROBABILITY_AUDIT",
            "event_mutex_status": "PASS",
            "postmodel_gates_status": "PASS",
            "final_gates_status": "PASS",
            "terminal_label": "FINAL_APPROVED",
        },
    }


def _incomplete():
    return {
        "code": "GOVERNED_PROBABILITY_PUBLISHED",
        "model_status": "GOVERNED_PROBABILITY_PUBLISHED",
        "probability_publishable": True,
        "rank_eligible": True,
        "can_execute": False,
        "calibrated_probability": 0.61,
        "calibrated_lower_bound": 0.57,
    }


def _event(event_id, *, home, away):
    return {
        "id": event_id,
        "home_team": home,
        "away_team": away,
        "commence_time": FUTURE,
        "status": "SCHEDULED",
    }


def _feed(rows_by_sport):
    def fetch(family, _target=None, **_kwargs):
        return list(rows_by_sport.get(str(family).upper(), []))

    return fetch


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, data):
        self.data = data

    def select(self, *_):
        return self

    def eq(self, *_):
        return self

    def order(self, *_, **__):
        return self

    def limit(self, *_):
        return self

    def upsert(self, payload, *_args, **_kwargs):
        self.data[:] = [dict(row) for row in payload]
        return self

    def range(self, start, end):
        self.data = self.data[int(start) : int(end) + 1]
        return self

    def execute(self):
        return _Result(self.data)


class _DB:
    def __init__(self):
        self.acquisition_rows = []

    def table(self, name):
        if name == "wow_v17_daily_run_acquisition_detail":
            return _Query(self.acquisition_rows)
        if name == "wow_prop_evidence_snapshots":
            return _Query([])
        return _Query(
            [
                {
                    "official_event_id": SHARED_ID,
                    "official_date": SLATE_DATE,
                    "event_start_time": FUTURE,
                    "home_team": "Canonical Home",
                    "away_team": "Canonical Away",
                    "venue_name": "Park",
                    "home_probable_pitcher": "H",
                    "away_probable_pitcher": "A",
                    "snapshot_id": "snap-event",
                    "snapshot_timestamp": FUTURE,
                    "feature_hydration_status": "PASS",
                }
            ]
        )


class _Market:
    class ScorePropRequest:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    @staticmethod
    def score_prop(*_):
        raise HTTPException(
            status_code=422,
            detail={"code": "PROP_MODEL_NOT_PUBLISHABLE", "probability_publishable": False, "can_execute": False},
        )


class _EventApi:
    def get_client(self):
        return object()


def _slate():
    return {
        "MLB": [_event(SHARED_ID, home="Milwaukee Brewers", away="San Diego Padres")],
        "NFL": [
            _event(SHARED_ID, home="Buffalo Bills", away="Kansas City Chiefs"),
            _event("NFL-PROVEN", home="Dallas Cowboys", away="Philadelphia Eagles"),
            _event("NFL-CLAIM", home="Green Bay Packers", away="Chicago Bears"),
            _event("NFL-SCORER", home="Detroit Lions", away="Minnesota Vikings"),
            _event("NFL-INPUTS", home="Baltimore Ravens", away="Cincinnati Bengals"),
            _event("NFL-TRANSPORT", home="Miami Dolphins", away="New York Jets"),
            _event("NFL-OUTPUT", home="Las Vegas Raiders", away="Denver Broncos"),
        ],
        "NHL": [_event(SHARED_ID, home="Boston Bruins", away="Toronto Maple Leafs")],
        "WNBA": [_event(WNBA_ALIAS, home="Las Vegas Aces", away="New York Liberty")],
    }


def _score(request, **_kwargs):
    sport = str(request.sport).upper()
    event_id = str(request.official_event_id)
    _score.calls.append((sport, event_id))
    if sport == "MLB" and event_id == SHARED_ID:
        return _proven()
    if sport == "NFL" and event_id == SHARED_ID:
        return {
            "code": "LLP_EVENT_GOVERNANCE_NOT_PROVEN",
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        }
    if event_id == "NFL-PROVEN":
        package = _proven()
        package["calibrated_probability"] = 0.66
        package["calibrated_lower_bound"] = 0.63
        return package
    if event_id == "NFL-CLAIM":
        return _incomplete()
    if event_id == "NFL-SCORER":
        raise RuntimeError("scorer boom")
    if event_id == "NFL-INPUTS":
        raise HTTPException(
            status_code=422,
            detail={
                "code": "MODEL_INPUTS_INSUFFICIENT",
                "probability_publishable": False,
                "rank_eligible": False,
                "can_execute": False,
            },
        )
    if event_id == "NFL-TRANSPORT":
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MODEL_SCORER_FAILED",
                "blockers": ["TEAM_EVENT_SCORER_TIMEOUT_OR_TRANSPORT_FAILURE"],
                "error_type": "ConnectionError",
                "model_invoked": True,
                "probability_publishable": False,
                "rank_eligible": False,
                "can_execute": False,
            },
        )
    if event_id == "NFL-OUTPUT":
        return ["not-a-package"]
    raise AssertionError(f"unexpected scorer call {sport} {event_id}")


_score.calls = []


def _rows_for(rows, *, sport=None, event_id=None, home=None):
    matched = []
    for row in rows:
        identity = row["identity"]
        if sport is not None and identity.get("sport") != sport:
            continue
        if event_id is not None and identity.get("official_event_id") != event_id:
            continue
        if home is not None and identity.get("home_team") != home:
            continue
        matched.append(row)
    return matched


def _assert_exact_once(result, moneyline_rows):
    reconciliation = result["reconciliation"]
    assert reconciliation["rows_unclassified"] == 0
    assert reconciliation["balanced"] is True
    assert reconciliation["rows_in"] == (
        reconciliation["rows_completed"]
        + reconciliation["rows_held"]
        + reconciliation["rows_rejected"]
        + reconciliation["rows_purged"]
        + reconciliation["rows_invalid"]
    )
    assert reconciliation["rows_in"] == len(result["rows"]) == len(moneyline_rows)
    audit = result["cross_sport_discovery_audit"]
    scan = audit["reconciliation"]
    assert scan["row_reconciliation"] == "PASS"
    assert scan["events_accounted"] == scan["events_discovered"]
    assert scan["can_execute"] is False
    scan_rows = audit["rows"]
    assert len(scan_rows) == scan["events_discovered"]
    event_keys = [row["event_key"] for row in scan_rows]
    assert len(event_keys) == len(set(event_keys))
    output_keys = [
        (
            row["identity"].get("sport"),
            row["identity"].get("official_event_id"),
            row["result"].get("event_key"),
        )
        for row in moneyline_rows
    ]
    assert len(output_keys) == len(set(output_keys))
    assert result["can_execute"] is False
    assert all(row["can_execute"] is False for row in moneyline_rows)


@pytest.fixture
def isolated_bridges():
    bridges = runtime.bridge_runtime.TEAM_EVENT_BRIDGES
    original = dict(bridges)
    bridges.clear()
    bridge = SimpleNamespace(supported_regimes=("REGULAR_SEASON",))
    bridges["MLB"] = bridge
    bridges["NFL"] = bridge
    bridges["WNBA"] = bridge
    try:
        yield
    finally:
        bridges.clear()
        bridges.update(original)


@pytest.fixture
def restore_cross_sport_entrypoint():
    saved = runtime._cross_sport_moneyline_rows
    saved_flag = getattr(runtime, "_v17_cross_sport_discovery_evidence_handoff_installed", False)
    try:
        yield
    finally:
        runtime._cross_sport_moneyline_rows = saved
        runtime._v17_cross_sport_discovery_evidence_handoff_installed = saved_flag


def _patch_canonical_resolvers(monkeypatch):
    import v17.mlb_team_event_hydration as mlb
    import v17.nfl_team_event_specialist as nfl
    import v17.nhl_event_identity as nhl

    def resolve_nfl(probe, **_kwargs):
        return {
            "ok": True,
            "canonical_event_id": str(probe.official_event_id),
            "identity_resolution": "EXACT_LEDGER_MATCH",
            "canonical_source_snapshot_id": "nfl-snapshot",
        }

    def resolve_mlb(req, **_kwargs):
        return {
            "ok": True,
            "canonical_official_event_id": str(req.official_event_id),
            "canonical_identity_resolution": "PARTICIPANTS_START_SLATE",
            "canonical_source_snapshot_id": "mlb-snapshot",
            "canonical_snapshot_timestamp": FUTURE,
        }

    def resolve_nhl(**_kwargs):
        return {
            "event_id": SHARED_ID,
            "identity_provider": "NHL_PUBLIC_WEB_API",
            "identity_verified_at": FUTURE,
        }

    monkeypatch.setattr(nfl, "resolve_nfl_team_event_evidence", resolve_nfl)
    monkeypatch.setattr(mlb, "resolve_mlb_team_event_evidence", resolve_mlb)
    monkeypatch.setattr(nhl, "resolve_nhl_current_event_identity", resolve_nhl)


def _patch_discovery(monkeypatch, rows_by_sport):
    from v17 import cross_sport_discovery_feed as feed
    from v17 import cross_sport_winner_discovery as discovery

    slate = _feed(rows_by_sport)
    monkeypatch.setattr(feed, "enabled", lambda: True)
    monkeypatch.setattr(feed, "odds_proxy_feed", lambda **_kwargs: slate)
    monkeypatch.setattr(feed, "rundown_board_feed", lambda **_kwargs: slate)
    monkeypatch.setattr(discovery, "discovery_budget_seconds", lambda: 120.0)


def test_installed_repairs_retain_shared_ids_hold_unverified_and_reconcile_once(
    monkeypatch, isolated_bridges, restore_cross_sport_entrypoint
):
    runtime._v17_cross_sport_discovery_evidence_handoff_installed = False
    assert install_cross_sport_discovery_evidence_handoff() is True
    assert runtime._cross_sport_moneyline_rows.__name__ == "repaired_cross_sport_moneyline_rows"

    _patch_discovery(monkeypatch, _slate())
    _patch_canonical_resolvers(monkeypatch)
    _score.calls = []
    monkeypatch.setattr(runtime, "score_team_event_request", _score)

    result = run_daily_snapshot(
        DailySnapshotRequest(
            requested_slate_date=SLATE_DATE,
            requested_timezone="America/Chicago",
            lanes=["MONEYLINE"],
            response_mode="FULL",
        ),
        db=_DB(),
        market_api=_Market(),
        event_api=_EventApi(),
    )

    moneyline = [row for row in result["rows"] if row["lane"] == "MONEYLINE"]
    _assert_exact_once(result, moneyline)

    canonical = _rows_for(moneyline, event_id=SHARED_ID, home="Canonical Home")
    discovered_mlb = _rows_for(moneyline, sport="MLB")
    shared_nfl = _rows_for(moneyline, sport="NFL", event_id=SHARED_ID)
    shared_nhl = _rows_for(moneyline, sport="NHL", event_id=SHARED_ID)
    assert len(canonical) == 1
    assert discovered_mlb == []
    assert len(shared_nfl) == 1
    assert len(shared_nhl) == 1
    assert shared_nfl[0]["row_status"] == "HELD"
    assert shared_nfl[0]["probability_publishable"] is False
    assert shared_nhl[0]["row_status"] == "HELD"
    assert shared_nhl[0]["result"]["bucket"] == "MODEL_UNAVAILABLE"
    assert shared_nhl[0]["probability_publishable"] is False
    assert ("NHL", SHARED_ID) not in _score.calls
    assert _score.calls.count(("MLB", SHARED_ID)) == 1

    wnba = [row for row in moneyline if row["identity"].get("sport") == "WNBA"]
    assert len(wnba) == 1
    held_alias = wnba[0]
    assert held_alias["row_status"] == "HELD"
    assert held_alias["identity"]["official_event_id"] is None
    assert held_alias["result"]["bucket"] == "EVENT_IDENTITY_UNRESOLVED"
    assert held_alias["result"]["official_event_id"] is None
    assert held_alias["probability_publishable"] is False
    assert held_alias["result"]["rank_eligible"] is False
    assert held_alias["result"]["detail"]["model_invoked"] is False
    assert held_alias["result"]["detail"]["canonical_identity_blocker"] == "WNBA_PROVIDER_TEAM_ALIAS_MISSING"
    assert WNBA_ALIAS not in str(held_alias["result"].get("event_key"))
    assert held_alias["result"]["event_key"].startswith("WNBA:UNRESOLVED:")
    assert all(sport != "WNBA" for sport, _event_id in _score.calls)

    proven = _rows_for(moneyline, sport="NFL", event_id="NFL-PROVEN")
    assert len(proven) == 1
    assert proven[0]["row_status"] == "COMPLETED"
    assert proven[0]["probability_publishable"] is True
    assert proven[0]["result"]["bucket"] == "MODEL_COMPLETED"
    proven_detail = proven[0]["result"]["detail"]
    assert proven_detail["official_publication_guard"]["official_publication_allowed"] is True
    assert proven_detail["calibrated_probability"] == 0.66
    assert proven_detail["calibrated_lower_bound"] == 0.63
    assert proven_detail["can_execute"] is False
    assert proven_detail["global_terminal_authority"] == "V17_TERMINAL_REDUCER"

    claim = _rows_for(moneyline, sport="NFL", event_id="NFL-CLAIM")
    assert len(claim) == 1
    assert claim[0]["row_status"] == "HELD"
    assert claim[0]["probability_publishable"] is False
    assert claim[0]["result"]["bucket"] != "MODEL_COMPLETED"
    claim_detail = claim[0]["result"]["detail"]
    assert claim_detail["probability_publishable"] is False
    assert claim_detail["rank_eligible"] is False
    assert claim_detail["calibrated_probability"] == 0.61
    assert claim_detail["calibrated_lower_bound"] == 0.57
    assert claim_detail["official_publication_guard"]["official_publication_allowed"] is False
    assert claim_detail["can_execute"] is False

    scorer = _rows_for(moneyline, sport="NFL", event_id="NFL-SCORER")[0]
    assert scorer["row_status"] == "HELD"
    assert scorer["result"]["bucket"] == "MODEL_SCORER_FAILED"
    assert scorer["result"]["bucket"] != "MODEL_UNAVAILABLE"
    assert scorer["result"]["detail"]["code"] == "MODEL_SCORER_FAILED"
    assert scorer["result"]["detail"]["model_invoked"] is True

    inputs = _rows_for(moneyline, sport="NFL", event_id="NFL-INPUTS")[0]
    assert inputs["row_status"] == "HELD"
    assert inputs["result"]["bucket"] == "MODEL_INPUTS_INSUFFICIENT"
    assert inputs["result"]["detail"]["code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert inputs["result"]["bucket"] != "MODEL_UNAVAILABLE"

    transport = _rows_for(moneyline, sport="NFL", event_id="NFL-TRANSPORT")[0]
    assert transport["row_status"] == "HELD"
    assert transport["result"]["bucket"] == "MODEL_SCORER_FAILED"
    assert transport["result"]["bucket"] != "MODEL_UNAVAILABLE"
    assert transport["result"]["detail"]["code"] == "MODEL_SCORER_FAILED"
    assert "TEAM_EVENT_SCORER_TIMEOUT_OR_TRANSPORT_FAILURE" in transport["result"]["detail"]["blockers"]
    assert transport["result"]["detail"]["model_invoked"] is True

    invalid = _rows_for(moneyline, sport="NFL", event_id="NFL-OUTPUT")[0]
    assert invalid["row_status"] == "HELD"
    assert invalid["result"]["bucket"] == "MODEL_OUTPUT_INVALID"
    assert invalid["result"]["detail"]["code"] == "MODEL_OUTPUT_INVALID"
    assert "calibrated_probability" not in invalid["result"]["detail"]

    assert result["reconciliation"]["rows_completed"] == 2
    assert result["reconciliation"]["rows_held"] == 8
    assert result["reconciliation"]["rows_rejected"] == 0
    publishable = [row for row in moneyline if row["probability_publishable"] is True]
    assert len(publishable) == 2
    assert {row["identity"].get("home_team") for row in publishable} == {
        "Canonical Home",
        "Dallas Cowboys",
    }


def test_base_entrypoint_retains_shared_ids_and_typed_publication_failures(
    monkeypatch, isolated_bridges, restore_cross_sport_entrypoint
):
    importlib.reload(runtime)
    assert runtime._cross_sport_moneyline_rows.__name__ == "_cross_sport_moneyline_rows"
    from v17 import cross_sport_winner_discovery as discovery

    monkeypatch.setattr(discovery, "discovery_budget_seconds", lambda: 120.0)
    _score.calls = []
    monkeypatch.setattr(runtime, "score_team_event_request", _score)
    bridges = runtime.bridge_runtime.TEAM_EVENT_BRIDGES
    bridges.clear()
    bridge = SimpleNamespace(supported_regimes=("REGULAR_SEASON",))
    bridges["MLB"] = bridge
    bridges["NFL"] = bridge

    rows, scan = runtime._cross_sport_moneyline_rows(
        SimpleNamespace(
            requested_slate_date=SLATE_DATE,
            requested_timezone="America/Chicago",
            max_team_events=12,
        ),
        run_id="v17-daily-combined-base",
        event_api=_EventApi(),
        covered_event_ids={SHARED_ID},
        fetch_sport_events=_feed(_slate()),
    )

    assert scan["reconciliation"]["row_reconciliation"] == "PASS"
    assert scan["reconciliation"]["events_accounted"] == scan["reconciliation"]["events_discovered"]
    assert scan["can_execute"] is False
    assert all(row["can_execute"] is False for row in rows)
    event_keys = [row["result"]["event_key"] for row in rows]
    assert len(event_keys) == len(set(event_keys))
    assert _rows_for(rows, sport="MLB") == []
    assert len(_rows_for(rows, sport="NFL", event_id=SHARED_ID)) == 1
    assert len(_rows_for(rows, sport="NHL", event_id=SHARED_ID)) == 1
    assert _rows_for(rows, sport="NHL", event_id=SHARED_ID)[0]["result"]["bucket"] == "MODEL_UNAVAILABLE"

    proven = _rows_for(rows, sport="NFL", event_id="NFL-PROVEN")[0]
    assert proven["row_status"] == "COMPLETED"
    assert proven["result"]["detail"]["official_publication_guard"]["status"] == "PASS"
    assert proven["result"]["detail"]["can_execute"] is False

    claim = _rows_for(rows, sport="NFL", event_id="NFL-CLAIM")[0]
    assert claim["row_status"] == "HELD"
    assert claim["result"]["detail"]["calibrated_probability"] == 0.61
    assert claim["result"]["detail"]["probability_publishable"] is False

    assert _rows_for(rows, sport="NFL", event_id="NFL-SCORER")[0]["result"]["bucket"] == "MODEL_SCORER_FAILED"
    assert _rows_for(rows, sport="NFL", event_id="NFL-INPUTS")[0]["result"]["bucket"] == "MODEL_INPUTS_INSUFFICIENT"
    transport = _rows_for(rows, sport="NFL", event_id="NFL-TRANSPORT")[0]
    assert transport["result"]["bucket"] == "MODEL_SCORER_FAILED"
    assert "TEAM_EVENT_SCORER_TIMEOUT_OR_TRANSPORT_FAILURE" in transport["result"]["detail"]["blockers"]
    assert _rows_for(rows, sport="NFL", event_id="NFL-OUTPUT")[0]["result"]["bucket"] == "MODEL_OUTPUT_INVALID"
    assert ("MLB", SHARED_ID) not in _score.calls
