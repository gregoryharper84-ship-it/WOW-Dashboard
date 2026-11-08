from __future__ import annotations

from datetime import datetime, timezone
import json
import logging

import pytest

import prop_evidence_readback as readback

REQUESTED_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ID = "22222222-2222-4222-8222-222222222222"
BASE = {
    "source_snapshot_id": REQUESTED_ID,
    "event_id": "2026_09_BUF_MIN",
    "sport": "NFL",
    "player": "Test Player",
    "stat_type": "RECEIVING_YARDS",
    "line": 9.5,
}


class _Response:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows, *, fail=False):
        self.rows = rows
        self.filters = []
        self.ordering = None
        self.limit_count = None
        self.fail = fail

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def order(self, key, desc=False):
        self.ordering = (key, desc)
        return self

    def limit(self, count):
        self.limit_count = count
        return self

    def execute(self):
        if self.fail:
            raise TimeoutError("candidate lookup unavailable")
        rows = [dict(row) for row in self.rows]
        for key, value in self.filters:
            rows = [row for row in rows if row.get(key) == value]
        if self.ordering:
            key, desc = self.ordering
            rows.sort(key=lambda row: str(row.get(key) or ""), reverse=desc)
        if self.limit_count is not None:
            rows = rows[: self.limit_count]
        return _Response(rows)


class _Table:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_args, **_kwargs):
        return _Query(self.rows)


class _RPC:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error

    def execute(self):
        if self.error is not None:
            raise self.error
        return _Response(self.data)


class _Client:
    def __init__(self, *, rpc=None, rpc_error=None, rows=None, receipts=None):
        self.rpc_data = rpc
        self.rpc_error = rpc_error
        self.rows = rows or []
        self.receipts = receipts or []
        self.rpc_calls = []
        self.snapshot_table_calls = 0

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        return _RPC(self.rpc_data, self.rpc_error)

    def table(self, name):
        if name == "wow_prop_evidence_snapshots":
            self.snapshot_table_calls += 1
            return _Table(self.rows)
        if name == "wow_action_invocation_receipts":
            return _Table(self.receipts)
        raise AssertionError(f"unexpected table {name}")


class _CandidateFailClient(_Client):
    def table(self, name):
        if name == "wow_prop_evidence_snapshots":
            self.snapshot_table_calls += 1
            return _Table(self.rows) if self.snapshot_table_calls == 1 else _FailTable()
        return super().table(name)


class _FailTable(_Table):
    def select(self, *_args, **_kwargs):
        return _Query(self.rows, fail=True)


def _row(**overrides):
    row = {
        "source_snapshot_id": REQUESTED_ID,
        "captured_at": datetime(2026, 11, 8, 7, 54, tzinfo=timezone.utc),
        "event_id": BASE["event_id"],
        "sport": BASE["sport"],
        "player": BASE["player"],
        "stat_type": BASE["stat_type"],
        "line": BASE["line"],
        "hydration_status": "PASS",
        "blockers": [],
        "game_log": [1.0] * 10,
        "box_score_log": [{}] * 10,
        "role_status": {"status": "ACTIVE"},
        "role_timestamp": datetime(2026, 11, 8, 6, 0, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def _receipt(**overrides):
    row = {
        "invocation_id": "33333333-3333-4333-8333-333333333333",
        "occurred_at": datetime(2026, 11, 8, 5, 37, 7, tzinfo=timezone.utc),
        "route": "/score-prop",
        "http_status": 422,
        "validation_code": readback.EVIDENCE_NOT_FOUND,
        "caller_class": "SELF_ACCEPTANCE",
        "auth_scheme": "BEARER",
        "request_id": "req-probe",
    }
    row.update(overrides)
    return row


def _ready_rpc():
    return {"ok": True, "code": readback.EVIDENCE_READY}


def _not_found_rpc():
    return {"ok": False, "code": readback.EVIDENCE_NOT_FOUND}


def test_fingerprint_is_stable_across_snapshot_id_and_whitespace():
    first = dict(BASE)
    second = {
        **BASE,
        "source_snapshot_id": OTHER_ID,
        "sport": " nfl ",
        "player": "  Test   Player ",
        "stat_type": "receiving-yards",
    }
    assert readback.identity_fingerprint(first) == readback.identity_fingerprint(second)
    assert readback.identity_fingerprint(first) != readback.identity_fingerprint(
        {**BASE, "line": 10.5}
    )


def test_ready_row_is_reported_ready_without_leaking_player_or_logs():
    client = _Client(rpc=_ready_rpc(), rows=[_row()])
    result = readback.diagnose_prop_evidence(client, BASE, request_id="req-1")

    assert result["diagnosis"] == "EVIDENCE_READY"
    assert result["snapshot_row"]["present"] is True
    assert result["snapshot_row"]["game_log_count"] == 10
    assert result["can_execute"] is False
    assert result["read_only"] is True
    serialized = json.dumps(result, default=str)
    assert BASE["player"] not in serialized
    assert "[1.0, 1.0" not in serialized
    assert client.rpc_calls[0][1]["p_player"] == BASE["player"]


def test_missing_row_and_no_candidates_is_absent_evidence():
    client = _Client(rpc=_not_found_rpc(), rows=[])
    result = readback.diagnose_prop_evidence(client, BASE)

    assert result["diagnosis"] == "SNAPSHOT_ROW_MISSING"
    assert result["snapshot_row"]["present"] is False
    assert result["identity_candidates"]["count"] == 0


def test_row_under_other_id_is_not_absent_evidence():
    client = _Client(rpc=_not_found_rpc(), rows=[_row(source_snapshot_id=OTHER_ID)])
    result = readback.diagnose_prop_evidence(client, BASE)

    assert result["diagnosis"] == "SNAPSHOT_EXISTS_UNDER_OTHER_ID"
    assert result["identity_candidates"]["other_snapshot_ids"] == [OTHER_ID]
    assert "not absent evidence" in result["interpretation"]


def test_identity_mismatch_is_not_absent_evidence_and_hides_other_player():
    client = _Client(
        rpc={"ok": False, "code": readback.EVIDENCE_IDENTITY_MISMATCH},
        rows=[_row(player="Other Player")],
    )
    result = readback.diagnose_prop_evidence(client, BASE)

    assert result["diagnosis"] == "SNAPSHOT_ID_POINTS_TO_OTHER_IDENTITY"
    assert "player" in result["snapshot_row"]["mismatch_fields"]
    serialized = json.dumps(result, default=str)
    assert "Other Player" not in serialized
    assert BASE["player"] not in serialized


def test_primary_rpc_alias_mismatch_is_not_reported_as_other_identity():
    request = {**BASE, "sport": "MLB", "stat_type": "K"}
    client = _Client(
        rpc={"ok": False, "code": readback.EVIDENCE_IDENTITY_MISMATCH},
        rows=[_row(sport="MLB", stat_type="PITCHER_STRIKEOUTS")],
        receipts=[_receipt(caller_class="CHATGPT_ACTION", request_id="req-alias")],
    )
    result = readback.diagnose_prop_evidence(client, request, request_id="req-alias")

    assert result["diagnosis"] == "PRIMARY_LOOKUP_STAT_ALIAS_MISMATCH"
    assert result["snapshot_row"]["identity_matches"] is True
    assert client.rpc_calls[0][1]["p_stat_type"] == "K"
    assert result["reported_failure_attribution"] == "LIVE_ACTION_EVIDENCE_FAILURE"
    assert result["reported_cause_supported"] is False
    assert result["reported_cause_basis"] == (
        "GOVERNED_EVIDENCE_EXISTS_BUT_PRIMARY_STAT_ALIAS_MISMATCH"
    )


def test_incomplete_row_is_not_ready():
    client = _Client(
        rpc={"ok": False, "code": readback.EVIDENCE_INCOMPLETE},
        rows=[_row(hydration_status="FAILED", blockers=["ROLE_STATUS_UNAVAILABLE"])],
    )
    result = readback.diagnose_prop_evidence(client, BASE)

    assert result["diagnosis"] == "EVIDENCE_NOT_READY"
    assert result["snapshot_row"]["blockers"] == ["ROLE_STATUS_UNAVAILABLE"]


def test_rpc_infrastructure_failure_is_never_absence():
    client = _Client(rpc_error=TimeoutError("db unavailable"), rows=[_row()])
    result = readback.diagnose_prop_evidence(client, BASE)

    assert result["diagnosis"] == "PRIMARY_LOOKUP_INFRA_UNAVAILABLE"
    assert result["snapshot_row"]["present"] is True
    assert "Absence is unproven" in result["interpretation"]


def test_failed_candidate_lookup_does_not_claim_row_missing():
    client = _CandidateFailClient(rpc=_not_found_rpc(), rows=[])
    result = readback.diagnose_prop_evidence(client, BASE)

    assert result["diagnosis"] == "SNAPSHOT_ROW_MISSING_LOOKUP_PARTIAL"
    assert result["identity_candidates"]["lookup_status"] == "UNAVAILABLE"


def test_self_acceptance_receipt_disproves_live_action_attribution():
    client = _Client(
        rpc=_not_found_rpc(),
        rows=[],
        receipts=[_receipt()],
    )
    result = readback.diagnose_prop_evidence(client, BASE, request_id="req-probe")

    assert result["reported_failure_attribution"] == "EXPECTED_SELF_ACCEPTANCE_PROBE"
    assert result["reported_cause_supported"] is False
    assert result["reported_cause_basis"] == "SELF_ACCEPTANCE_PROBE_EXPECTS_422"
    assert result["receipt"]["latest"]["caller_class"] == "SELF_ACCEPTANCE"


def test_live_action_422_matching_missing_evidence_supports_cause():
    client = _Client(
        rpc=_not_found_rpc(),
        rows=[],
        receipts=[_receipt(caller_class="CHATGPT_ACTION", request_id="req-live")],
    )
    result = readback.diagnose_prop_evidence(client, BASE, request_id="req-live")

    assert result["reported_failure_attribution"] == "LIVE_ACTION_EVIDENCE_FAILURE"
    assert result["reported_cause_supported"] is True
    assert result["reported_cause_basis"] == "LIVE_ACTION_422_MATCHES_EVIDENCE_STATE"


def test_request_id_only_receipt_audit_needs_no_identity():
    client = _Client(receipts=[_receipt()])
    result = readback.diagnose_prop_evidence(client, None, request_id="req-probe")

    assert result["status"] == "OK"
    assert result["diagnosis"] == "IDENTITY_NOT_PROVIDED"
    assert result["reported_failure_attribution"] == "EXPECTED_SELF_ACCEPTANCE_PROBE"
    assert result["reported_cause_supported"] is False
    assert result["source_snapshot_id"] is None
    assert client.rpc_calls == []


def test_failure_log_captures_join_key_without_player_name(caplog):
    with caplog.at_level(logging.WARNING, logger=readback.LOGGER.name):
        readback.log_prop_evidence_failure(
            BASE,
            code=readback.EVIDENCE_NOT_FOUND,
            request_id="req-1",
            caller_class="CHATGPT_ACTION",
        )

    assert readback.identity_fingerprint(BASE) in caplog.text
    assert "event_id=2026_09_BUF_MIN" in caplog.text
    assert "request_id=req-1" in caplog.text
    assert "can_execute=false" in caplog.text
    assert BASE["player"] not in caplog.text


def test_invalid_snapshot_id_is_rejected():
    with pytest.raises(ValueError):
        readback.normalize_identity({**BASE, "source_snapshot_id": "not-a-uuid"})


def test_stat_identity_reuses_writer_alias_map():
    assert readback.normalize_identity(
        {**BASE, "stat_type": "PASS_YARDS"}
    )["stat_type"] == "PASSING_YARDS"

    # The WNBA aliases are registered by the runtime facade against the same
    # governed map the writer uses. The readback must resolve them too.
    import pick_request_runtime  # noqa: F401

    assert readback.normalize_identity(
        {**BASE, "sport": "WNBA", "stat_type": "PTS"}
    )["stat_type"] == "POINTS"


def test_candidate_search_filters_identity_before_bounded_limit():
    distractors = [
        _row(
            source_snapshot_id=f"99999999-9999-4999-8999-{index:012d}",
            player=f"Distractor {index}",
            captured_at=datetime(2026, 11, 8, 8, 0, tzinfo=timezone.utc),
        )
        for index in range(30)
    ]
    exact = _row(
        source_snapshot_id=OTHER_ID,
        captured_at=datetime(2026, 11, 7, 8, 0, tzinfo=timezone.utc),
    )
    client = _Client(rpc=_not_found_rpc(), rows=[*distractors, exact])
    result = readback.diagnose_prop_evidence(client, BASE)

    assert result["diagnosis"] == "SNAPSHOT_EXISTS_UNDER_OTHER_ID"
    assert result["identity_candidates"]["other_snapshot_ids"] == [OTHER_ID]


def test_non_evidence_live_action_422_is_not_attributed_to_evidence():
    client = _Client(
        rpc=_not_found_rpc(),
        rows=[],
        receipts=[
            _receipt(
                caller_class="CHATGPT_ACTION",
                validation_code="REQUEST_SCHEMA_INVALID",
                request_id="req-schema",
            )
        ],
    )
    result = readback.diagnose_prop_evidence(client, BASE, request_id="req-schema")

    assert result["reported_failure_attribution"] == "LIVE_ACTION_OTHER_422"
    assert result["reported_cause_supported"] is None
    assert result["reported_cause_basis"] == "UNATTRIBUTED"


def test_mlb_alias_matches_row_and_candidate_but_keeps_primary_rpc_raw():
    mlb = {
        **BASE,
        "sport": "MLB",
        "player": "Test Pitcher",
        "stat_type": "K",
        "line": 5.5,
    }
    stored = _row(
        source_snapshot_id=OTHER_ID,
        sport="MLB",
        player="Test Pitcher",
        stat_type="PITCHER_STRIKEOUTS",
        line=5.5,
    )
    client = _Client(rpc=_not_found_rpc(), rows=[stored])
    result = readback.diagnose_prop_evidence(client, mlb)

    assert result["requested_identity"]["stat_type"] == "PITCHER_STRIKEOUTS"
    assert result["diagnosis"] == "SNAPSHOT_EXISTS_UNDER_OTHER_ID"
    assert result["identity_candidates"]["other_snapshot_ids"] == [OTHER_ID]
    assert result["identity_fingerprint"] == readback.identity_fingerprint(
        {**mlb, "stat_type": "PITCHER_STRIKEOUTS"}
    )
    assert client.rpc_calls[0][1]["p_stat_type"] == "K"

    requested_row_client = _Client(
        rpc=_ready_rpc(),
        rows=[
            _row(
                sport="MLB",
                player="Test Pitcher",
                stat_type="PITCHER_STRIKEOUTS",
                line=5.5,
            )
        ],
    )
    requested_row = readback.diagnose_prop_evidence(requested_row_client, mlb)
    assert requested_row["snapshot_row"]["identity_matches"] is True
    assert requested_row["snapshot_row"]["mismatch_fields"] == []


def test_candidate_window_at_limit_without_other_id_is_partial():
    rows = [
        _row(source_snapshot_id=REQUESTED_ID, captured_at=datetime(2026, 11, 8, 8, 0, tzinfo=timezone.utc))
        for _ in range(25)
    ]
    client = _Client(rows=rows)
    identity = readback.normalize_identity(BASE)
    candidates = readback._lookup_candidates(client, identity)

    assert candidates["status"] == "PARTIAL"
    assert candidates["other_snapshot_ids"] == []


def test_live_action_422_without_validation_code_is_unattributed():
    client = _Client(
        rpc=_not_found_rpc(),
        rows=[],
        receipts=[
            _receipt(
                caller_class="ACTION_API_KEY",
                validation_code=None,
                request_id="req-null-code",
            )
        ],
    )
    result = readback.diagnose_prop_evidence(client, BASE, request_id="req-null-code")

    assert result["reported_failure_attribution"] == "LIVE_ACTION_OTHER_422"
    assert result["reported_cause_supported"] is None


def test_invalid_identity_does_not_echo_exception_text():
    result = readback.diagnose_prop_evidence(
        _Client(),
        {**BASE, "source_snapshot_id": "not-a-uuid"},
    )

    assert result["status"] == "INVALID_IDENTITY"
    assert result["error"] == "INVALID_IDENTITY"
    serialized = json.dumps(result, default=str)
    assert "must be a UUID" not in serialized
    assert "Traceback" not in serialized
    assert "ValueError" not in serialized


def test_rpc_error_message_never_enters_readback_response():
    client = _Client(rpc_error=RuntimeError("postgres://reader:super-secret@db"))
    result = readback.diagnose_prop_evidence(client, BASE)

    assert result["primary_lookup"]["status"] == "INFRA_UNAVAILABLE"
    serialized = json.dumps(result, default=str)
    assert "super-secret" not in serialized
    assert "postgres://" not in serialized
    assert "Traceback" not in serialized
