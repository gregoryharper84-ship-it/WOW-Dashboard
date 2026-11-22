"""Fail-closed snapshot authority at the evidence repair boundary.

Reproduces the newer-blocked / older-clean issue through the real
repair_prop_evidence function. The database query and RPC are deterministic fakes,
not hosted PostgreSQL or external availability services.
"""
from datetime import datetime
from types import SimpleNamespace

from prop_evidence_repair import repair_prop_evidence


class _Request(SimpleNamespace):
    def model_copy(self, *, update):
        return _Request(**{**vars(self), **update})


class _Query:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []
        self.before = None
        self.descending = False
        self.maximum = None

    def select(self, *_args):
        return self

    def eq(self, name, value):
        self.filters.append((name, value))
        return self

    def lt(self, name, value):
        assert name == "captured_at"
        self.before = datetime.fromisoformat(value)
        return self

    def order(self, name, *, desc):
        assert name == "captured_at"
        self.descending = desc
        return self

    def limit(self, count):
        self.maximum = count
        return self

    def execute(self):
        rows = [
            row for row in self.rows
            if all(row.get(name) == value for name, value in self.filters)
            and (self.before is None or datetime.fromisoformat(row["captured_at"]) < self.before)
        ]
        rows.sort(key=lambda row: row["captured_at"], reverse=self.descending)
        return SimpleNamespace(data=rows[:self.maximum])


class _DB:
    def __init__(self, rows):
        self.rows = rows
        self.lookups = 0

    def table(self, name):
        assert name == "wow_prop_evidence_snapshots"
        self.lookups += 1
        return _Query(self.rows)


def _request(source):
    return _Request(
        source_snapshot_id=source,
        event_id="NFL:TEST:101",
        sport="NFL",
        player="Fixture Player",
        stat_type="PASSING_YARDS",
        line=201.5,
        event_start_time="2026-11-20T00:00:00+00:00",
    )


def _row(source, captured, hydration="PASS"):
    request = _request(source)
    return {
        "source_snapshot_id": source,
        "captured_at": captured,
        "event_id": request.event_id,
        "sport": request.sport,
        "player": request.player,
        "stat_type": request.stat_type,
        "line": request.line,
        "hydration_status": hydration,
    }


def _ready(source):
    return {"ok": True, "code": "PROP_EVIDENCE_READY", "source_snapshot_id": source}


def _blocked(reason):
    return {
        "ok": False,
        "code": "RUN_INVALID_ACQUISITION_INCOMPLETE",
        "hydration_status": "FAILED",
        "blockers": [reason],
    }


def test_requested_ruled_out_snapshot_never_falls_back_to_older_ready():
    db = _DB([_row("older", "2026-11-18T00:00:00+00:00")])
    seen = []

    def fetch(request):
        seen.append(request.source_snapshot_id)
        return _blocked("PLAYER_RULED_OUT") if request.source_snapshot_id == "newer" else _ready("older")

    result = repair_prop_evidence(_request("newer"), primary_fetch=fetch, client=db)
    assert result["code"] == "RUN_INVALID_ACQUISITION_INCOMPLETE"
    assert result["blockers"] == ["PLAYER_RULED_OUT"]
    assert result["acquisition_repair_status"] == "NOT_REPAIRABLE"
    assert seen == ["newer"]
    assert db.lookups == 0
    assert result["can_execute"] is False


def test_latest_failed_observation_overrides_older_pass_on_missing_id():
    db = _DB([
        _row("older", "2026-11-18T00:00:00+00:00"),
        _row("newer", "2026-11-19T00:00:00+00:00", "FAILED"),
    ])
    seen = []

    def fetch(request):
        source = request.source_snapshot_id
        seen.append(source)
        if source == "missing":
            return {"ok": False, "code": "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND"}
        return _blocked("PLAYER_AVAILABILITY_UNVERIFIED") if source == "newer" else _ready(source)

    result = repair_prop_evidence(_request("missing"), primary_fetch=fetch, client=db)
    assert result["code"] == "RUN_INVALID_ACQUISITION_INCOMPLETE"
    assert result["blockers"] == ["PLAYER_AVAILABILITY_UNVERIFIED"]
    assert result["acquisition_repair_status"] == "NEWEST_EVIDENCE_NOT_READY"
    assert seen == ["missing", "newer"]
    assert result["can_execute"] is False


def test_provisional_recheck_hold_is_not_repaired_away():
    db = _DB([_row("older", "2026-11-18T00:00:00+00:00")])
    result = repair_prop_evidence(
        _request("newer"),
        primary_fetch=lambda request: _blocked("PLAYER_AVAILABILITY_RECHECK_REQUIRED"),
        client=db,
    )
    assert result["blockers"] == ["PLAYER_AVAILABILITY_RECHECK_REQUIRED"]
    assert result["acquisition_repair_status"] == "NOT_REPAIRABLE"
    assert db.lookups == 0


def test_missing_snapshot_still_recovers_from_latest_clean_observation():
    db = _DB([_row("newer", "2026-11-19T00:00:00+00:00")])
    result = repair_prop_evidence(
        _request("missing"),
        primary_fetch=lambda request: _ready("newer") if request.source_snapshot_id == "newer"
        else {"ok": False, "code": "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND"},
        client=db,
    )
    assert result["code"] == "PROP_EVIDENCE_READY"
    assert result["effective_source_snapshot_id"] == "newer"
    assert result["acquisition_repair_status"] == "RECOVERED_FROM_GOVERNED_SNAPSHOT"
    assert result["can_execute"] is False


def test_direct_older_ready_cannot_override_newer_ruled_out_snapshot():
    db = _DB([
        _row("older", "2026-11-18T00:00:00+00:00"),
        _row("newer", "2026-11-19T00:00:00+00:00", "FAILED"),
    ])
    seen = []

    def fetch(request):
        seen.append(request.source_snapshot_id)
        return _ready("older") if request.source_snapshot_id == "older" else _blocked("PLAYER_RULED_OUT")

    result = repair_prop_evidence(_request("older"), primary_fetch=fetch, client=db)
    assert result["code"] == "RUN_INVALID_ACQUISITION_INCOMPLETE"
    assert result["blockers"] == ["PLAYER_RULED_OUT"]
    assert result["acquisition_repair_status"] == "LATEST_OBSERVATION_NOT_READY"
    assert seen == ["older", "newer"]
    assert result["can_execute"] is False


def test_older_ready_is_replaced_by_actual_newest_ready():
    db = _DB([
        _row("older", "2026-11-18T00:00:00+00:00"),
        _row("newer", "2026-11-19T00:00:00+00:00"),
    ])
    result = repair_prop_evidence(
        _request("older"),
        primary_fetch=lambda request: _ready(request.source_snapshot_id),
        client=db,
    )
    assert result["effective_source_snapshot_id"] == "newer"
    assert result["acquisition_repair_status"] == "LATEST_OBSERVATION_SELECTED"
    assert result["can_execute"] is False


def test_ready_primary_holds_when_authority_lookup_unavailable():
    class BrokenDB:
        def table(self, _name):
            raise TimeoutError("newest evidence could not be verified")

    result = repair_prop_evidence(
        _request("older"),
        primary_fetch=lambda request: _ready("older"),
        client=BrokenDB(),
    )
    assert result["ok"] is False
    assert result["code"] == "RUN_INVALID_ACQUISITION_INCOMPLETE"
    assert result["blockers"] == ["PROP_EVIDENCE_BRIDGE_UNAVAILABLE"]
    assert result["acquisition_repair_status"] == "LATEST_AUTHORITY_LOOKUP_FAILED"
    assert result["can_execute"] is False

def test_same_capture_time_two_snapshots_always_hold():
    """An observed real-data ambiguity, not a valid newest-source identity."""
    db = _DB([
        _row("older_uuid", "2026-11-19T00:00:00+00:00"),
        _row("newer_uuid", "2026-11-19T00:00:00+00:00"),
    ])
    calls = []

    def fetch(request):
        calls.append(request.source_snapshot_id)
        return _ready(request.source_snapshot_id)

    result = repair_prop_evidence(_request("older_uuid"), primary_fetch=fetch, client=db)
    assert calls == ["older_uuid"]
    assert result["ok"] is False
    assert result["code"] == "RUN_INVALID_ACQUISITION_INCOMPLETE"
    assert result["blockers"] == ["PROP_LATEST_SNAPSHOT_AMBIGUOUS"]
    assert result["acquisition_repair_status"] == "LATEST_AUTHORITY_AMBIGUOUS"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_missing_id_does_not_recover_one_of_two_equal_timestamp_snapshots():
    db = _DB([
        _row("clean_older_uuid", "2026-11-19T00:00:00+00:00"),
        _row("blocked_other_uuid", "2026-11-19T00:00:00+00:00", hydration="FAILED"),
    ])
    calls = []

    def fetch(request):
        calls.append(request.source_snapshot_id)
        if request.source_snapshot_id == "missing":
            return {"ok": False, "code": "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND"}
        return _ready(request.source_snapshot_id)

    result = repair_prop_evidence(_request("missing"), primary_fetch=fetch, client=db)
    assert calls == ["missing"]
    assert result["blockers"] == ["PROP_LATEST_SNAPSHOT_AMBIGUOUS"]
    assert result["acquisition_repair_status"] == "LATEST_AUTHORITY_AMBIGUOUS"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_equal_utc_instants_in_different_offsets_hold():
    db = _DB([
        _row("first", "2026-11-19T00:00:00+00:00"),
        _row("second", "2026-11-18T18:00:00-06:00"),
    ])
    result = repair_prop_evidence(
        _request("first"),
        primary_fetch=lambda req: _ready(req.source_snapshot_id),
        client=db,
    )
    assert result["blockers"] == ["PROP_LATEST_SNAPSHOT_AMBIGUOUS"]
    assert result["can_execute"] is False

