"""Phase A: the active-season Daily chain for NFL candidates.

The November 9 regression shape is the reference: most acquired candidates
never reached a model, yet the run could still read like a completed board. The
chain must give every acquired candidate exactly one disposition — scored,
explicitly excluded, or explicitly not scored — reconcile once, and never let a
completed run read as full qualification.

These tests drive the real ``run_daily_snapshot``; they do not re-implement the
selection rules.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from v17.daily_response_contract import read_row_detail_page
from v17.daily_snapshot_runtime import DailySnapshotRequest, run_daily_snapshot

NOW = datetime.now(timezone.utc)
FUTURE_DT = NOW + timedelta(hours=12)
FUTURE = FUTURE_DT.isoformat()
SLATE = FUTURE_DT.astimezone(ZoneInfo("America/Chicago")).date().isoformat()
OFF_SLATE = (FUTURE_DT + timedelta(days=3)).isoformat()
PAST = (NOW - timedelta(hours=2)).isoformat()


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows):
        self._rows = rows
        self._start = 0
        self._end = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, count):
        self._end = int(count)
        return self

    def range(self, start, end):
        self._start, self._end = int(start), int(end) + 1
        return self

    def upsert(self, payload, *_args, **_kwargs):
        incoming = payload if isinstance(payload, list) else [payload]
        keyed = {(row.get("run_id"), row.get("row_index")): row for row in self._rows}
        for row in incoming:
            keyed[(row.get("run_id"), row.get("row_index"))] = dict(row)
        self._rows[:] = list(keyed.values())
        return self

    def execute(self):
        return _Result(self._rows[self._start:self._end])


class _DB:
    def __init__(self, snapshots):
        self.snapshots = [dict(row) for row in snapshots]
        self.row_detail = []
        self.acquisition_detail = []

    def table(self, name):
        if name == "wow_prop_evidence_snapshots":
            return _Query(self.snapshots)
        if name == "wow_v17_daily_run_row_detail":
            return _Query(self.row_detail)
        return _Query(self.acquisition_detail)


def _snapshot(player: str, *, event_id: str | None = None, start: str = FUTURE,
              sport: str = "NFL", stat_type: str = "PASSING_YARDS",
              blockers: list[str] | None = None, index: int = 0) -> dict:
    return {
        "source_snapshot_id": f"snap-{index}-{player}",
        "captured_at": NOW.isoformat(),
        "event_id": event_id or f"NFL:TEST:{index}",
        "event_start_time": start,
        "sport": sport,
        "player": player,
        "stat_type": stat_type,
        "line": 221.5,
        "hydration_status": "PASS",
        "blockers": list(blockers or []),
    }


class _Market:
    class ScorePropRequest:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    def __init__(self, publishable=()):
        self.publishable = set(publishable)

    def score_prop(self, req, _identity):
        player = str(req.kwargs.get("player") or "")
        if player in self.publishable:
            return {
                "probability_publishable": True,
                "rank_eligible": True,
                "can_execute": False,
                "probability_qualification": {
                    "terminal_label": "MODEL_QUALIFIED",
                    "model_evaluated": True,
                    "rank_eligible": True,
                },
            }
        return {
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
            "probability_qualification": {
                "terminal_label": "NO_LOW_PROBABILITY",
                "pick_rejected": True,
                "model_evaluated": True,
                "rank_eligible": False,
                "blockers": ["BELOW_THRESHOLD"],
            },
        }


def _run(db, market):
    return run_daily_snapshot(
        DailySnapshotRequest(
            requested_slate_date=SLATE,
            requested_timezone="America/Chicago",
            lanes=["PROPS"],
        ),
        db=db,
        market_api=market,
        event_api=object(),
    )


def test_every_acquired_candidate_gets_one_explicit_disposition():
    snapshots = [
        _snapshot("Eligible Player", index=1),
        _snapshot("Ruled Out Player", blockers=["PLAYER_RULED_OUT"], index=2),
        _snapshot("Unverified Player", blockers=["PLAYER_AVAILABILITY_UNVERIFIED"], index=3),
        _snapshot("Past Player", start=PAST, index=4),
        _snapshot("Other Slate Player", start=OFF_SLATE, index=5),
    ]
    db = _DB(snapshots)
    payload = _run(db, _Market(publishable={"Eligible Player"}))

    accounting = payload["prop_candidate_accounting"]
    assert accounting["manifest_rows_seen"] == 5
    assert accounting["eligible_rows"] == 1
    assert accounting["scored_rows"] == 1
    assert accounting["excluded_rows"] == 4
    assert accounting["accounted_rows"] == 5
    assert accounting["balanced"] is True
    assert accounting["excluded_by_reason"] == {
        "EVIDENCE_BLOCKED": 2,
        "EVENT_NOT_PREGAME": 1,
        "NOT_REQUESTED_SLATE": 1,
    }
    excluded = {item["player"]: item for item in accounting["exclusions"]}
    assert excluded["Ruled Out Player"]["blockers"] == ["PLAYER_RULED_OUT"]
    assert excluded["Unverified Player"]["blockers"] == ["PLAYER_AVAILABILITY_UNVERIFIED"]

    # Excluded candidates never become scored rows, and the scored row is NFL.
    assert [row["identity"]["player"] for row in payload["rows"]] == ["Eligible Player"]
    assert payload["rows"][0]["identity"]["sport"] == "NFL"
    assert all(row["terminal"] is True for row in payload["rows"])
    assert payload["rows"][0]["row_status"] == "COMPLETED"
    assert payload["can_execute"] is False

    reconciliation = payload["reconciliation"]
    assert reconciliation["balanced"] is True
    assert reconciliation["rows_in"] == 1 == reconciliation["rows_completed"]
    assert "RUN_INVALID_PROP_CANDIDATE_ACCOUNTING" not in payload["blockers"]


def test_large_slate_reconciles_scored_and_unscored_rows_without_claiming_qualification():
    total = 49
    publishable = {f"Player {index}" for index in range(12)}
    snapshots = [_snapshot(f"Player {index}", index=index) for index in range(total)]
    db = _DB(snapshots)
    payload = _run(db, _Market(publishable=publishable))

    accounting = payload["prop_candidate_accounting"]
    assert accounting["manifest_rows_seen"] == total
    assert accounting["scored_rows"] == len(payload["rows"])
    assert (
        accounting["scored_rows"]
        + accounting["eligible_not_scored_rows"]
        + accounting["excluded_rows"]
    ) == total
    assert accounting["balanced"] is True

    reconciliation = payload["reconciliation"]
    assert reconciliation["rows_in"] == len(payload["rows"])
    assert reconciliation["balanced"] is True
    assert reconciliation["rows_completed"] == min(12, len(payload["rows"]))
    assert reconciliation["rows_completed"] < total

    identities = [row["identity"]["event_id"] for row in payload["rows"]]
    assert len(identities) == len(set(identities))
    assert all(row["terminal"] is True for row in payload["rows"])
    assert payload["row_detail_retrieval"]["detail_available"] is True

    # A completed run is not a full qualification run.
    assert payload["run_status"] in {"COMPLETED", "COMPLETED_WITH_ACQUISITION_BLOCKERS"}
    completed = [row for row in payload["rows"] if row["row_status"] == "COMPLETED"]
    assert len(completed) == reconciliation["rows_completed"]
    assert all(row["probability_publishable"] for row in completed)
    # The November 9 shape: only part of the slate is scored at all, or scored
    # rows are held. Either way the run must not read as full qualification.
    assert accounting["eligible_not_scored_rows"] > 0 or (
        reconciliation["rows_held"] + reconciliation["rows_rejected"] > 0
    )


def test_durable_row_detail_agrees_with_the_compact_response_and_pages():
    total = 30
    snapshots = [_snapshot(f"Player {index}", index=index) for index in range(total)]
    db = _DB(snapshots)
    payload = _run(db, _Market(publishable={f"Player {index}" for index in range(5)}))
    run_id = payload["run_id"]

    available = payload["row_detail_retrieval"]["rows_available"]
    assert available == len(payload["rows"])
    assert available > 5

    details = []
    offset = 0
    pages = 0
    while True:
        page = read_row_detail_page(db, run_id=run_id, offset=offset, limit=5)
        assert page["status"] == "OK"
        details.extend(page["rows"])
        pages += 1
        if page["next_offset"] is None:
            break
        assert page["returned"] == 5
        offset = page["next_offset"]
    assert pages >= 2
    assert len(details) == available

    compact = {
        (row["identity"]["event_id"], row["row_status"])
        for row in payload["rows"]
    }
    persisted = {
        (row["identity"]["event_id"], row["row_status"])
        for row in details
    }
    assert persisted == compact
    assert all(row["detail"]["can_execute"] is False for row in details)


def test_duplicate_run_is_idempotent_and_ruled_out_stays_purged():
    class RuledOutMarket(_Market):
        def score_prop(self, req, _identity):
            if str(req.kwargs.get("player") or "") != "Late Scratch":
                return super().score_prop(req, _identity)
            return {
                "probability_publishable": True,
                "rank_eligible": True,
                "terminal_label": "PLAYER_RULED_OUT",
                "pick_rejected": True,
                "can_execute": False,
            }

    db = _DB([_snapshot("Late Scratch", index=1), _snapshot("Steady Player", index=2)])
    market = RuledOutMarket(publishable={"Steady Player"})
    first = _run(db, market)
    second = _run(db, market)

    def terminals(payload):
        return {
            row["identity"]["event_id"]: row["row_status"] for row in payload["rows"]
        }

    assert terminals(first) == terminals(second)
    assert first["reconciliation"] == second["reconciliation"]

    scratch = next(row for row in first["rows"] if row["identity"]["player"] == "Late Scratch")
    assert scratch["row_status"] == "PURGED"
    assert scratch["probability_publishable"] is False
    # The default Daily response is COMPACT; full nested evidence lives in
    # the durable paged readback, not on the transport row.
    page = read_row_detail_page(db, run_id=first["run_id"])
    assert page["status"] == "OK"
    detail = next(
        row["detail"] for row in page["rows"]
        if row["identity"]["player"] == "Late Scratch"
    )
    assert detail["result"]["probability_publishable"] is False
    assert detail["result"]["rank_eligible"] is False
    assert detail["can_execute"] is False
    assert first["reconciliation"]["rows_purged"] == 1
    assert first["reconciliation"]["balanced"] is True
