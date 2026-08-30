from dataclasses import dataclass, replace
from types import SimpleNamespace

from prop_evidence_repair import repair_prop_evidence


@dataclass(frozen=True)
class FakeRequest:
    source_snapshot_id: str
    event_id: str = "WNBA:P0:REPAIR"
    event_start_time: str = "2026-08-30T02:00:00+00:00"
    sport: str = "WNBA"
    player: str = "Test Player"
    stat_type: str = "REB"
    line: float = 10.5

    def model_copy(self, *, update):
        return replace(self, **update)


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self.lt_filters = []

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def lt(self, *args, **_kwargs):
        self.lt_filters.append((args, _kwargs))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class FakeClient:
    def __init__(self, rows=None, *, fail=False):
        self.rows = rows or []
        self.fail = fail
        self.table_calls = 0
        self.last_query = None

    def table(self, _name):
        self.table_calls += 1
        if self.fail:
            raise RuntimeError("ledger unavailable")
        self.last_query = FakeQuery(self.rows)
        return self.last_query


def _ready(source_snapshot_id):
    return {
        "ok": True,
        "code": "PROP_EVIDENCE_READY",
        "source_snapshot_id": source_snapshot_id,
        "hydration_status": "PASS",
        "can_execute": False,
    }


def _missing():
    return {
        "ok": False,
        "code": "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND",
        "hydration_status": "FAILED",
        "probability_publishable": False,
        "can_execute": False,
    }


def _incomplete(source_snapshot_id=None):
    payload = {
        "ok": False,
        "code": "RUN_INVALID_ACQUISITION_INCOMPLETE",
        "hydration_status": "INCOMPLETE",
        "probability_publishable": False,
        "can_execute": False,
    }
    if source_snapshot_id:
        payload["source_snapshot_id"] = source_snapshot_id
    return payload


def test_ready_requested_snapshot_does_not_query_fallback_ledger():
    req = FakeRequest(source_snapshot_id="requested")
    client = FakeClient(rows=[{"source_snapshot_id": "newer"}])

    result = repair_prop_evidence(
        req,
        primary_fetch=lambda current: _ready(current.source_snapshot_id),
        client=client,
    )

    assert result["code"] == "PROP_EVIDENCE_READY"
    assert result["acquisition_repair_status"] == "NOT_NEEDED"
    assert result["requested_source_snapshot_id"] == "requested"
    assert result["effective_source_snapshot_id"] == "requested"
    assert len(result["acquisition_attempts"]) == 1
    assert client.table_calls == 0
    assert result["can_execute"] is False


def test_missing_requested_snapshot_recovers_from_newer_governed_snapshot():
    req = FakeRequest(source_snapshot_id="requested")
    client = FakeClient(
        rows=[
            {"source_snapshot_id": "requested"},
            {"source_snapshot_id": "fallback-1"},
            {"source_snapshot_id": "fallback-2"},
        ]
    )

    def primary_fetch(current):
        if current.source_snapshot_id == "requested":
            return _missing()
        if current.source_snapshot_id == "fallback-1":
            return _ready("fallback-1")
        raise AssertionError("must stop after first validated ready fallback")

    result = repair_prop_evidence(req, primary_fetch=primary_fetch, client=client)

    assert result["code"] == "PROP_EVIDENCE_READY"
    assert result["acquisition_repair_status"] == "RECOVERED_FROM_GOVERNED_SNAPSHOT"
    assert result["requested_source_snapshot_id"] == "requested"
    assert result["effective_source_snapshot_id"] == "fallback-1"
    assert [attempt["status"] for attempt in result["acquisition_attempts"]] == ["FAILED", "PASS"]
    assert client.last_query.lt_filters == [(("captured_at", req.event_start_time), {})]
    assert result["can_execute"] is False


def test_incomplete_fallbacks_are_revalidated_and_exhausted_fail_closed():
    req = FakeRequest(source_snapshot_id="requested")
    client = FakeClient(
        rows=[
            {"source_snapshot_id": "fallback-1"},
            {"source_snapshot_id": "fallback-2"},
        ]
    )

    def primary_fetch(current):
        if current.source_snapshot_id == "requested":
            return _incomplete("requested")
        return _incomplete(current.source_snapshot_id)

    result = repair_prop_evidence(req, primary_fetch=primary_fetch, client=client)

    assert result["ok"] is False
    assert result["code"] == "RUN_INVALID_ACQUISITION_INCOMPLETE"
    assert result["acquisition_repair_status"] == "FALLBACKS_EXHAUSTED"
    assert len(result["acquisition_attempts"]) == 3
    assert all(attempt["status"] == "FAILED" for attempt in result["acquisition_attempts"])
    assert result["effective_source_snapshot_id"] == "requested"
    assert client.last_query.lt_filters == [(("captured_at", req.event_start_time), {})]
    assert result["can_execute"] is False


def test_fallback_lookup_failure_preserves_original_blocker():
    req = FakeRequest(source_snapshot_id="requested")
    client = FakeClient(fail=True)

    result = repair_prop_evidence(
        req,
        primary_fetch=lambda _current: _missing(),
        client=client,
    )

    assert result["ok"] is False
    assert result["code"] == "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND"
    assert result["acquisition_repair_status"] == "FALLBACK_LOOKUP_FAILED"
    assert result["acquisition_attempts"][-1]["path"] == "GOVERNED_SNAPSHOT_LOOKUP"
    assert result["can_execute"] is False
