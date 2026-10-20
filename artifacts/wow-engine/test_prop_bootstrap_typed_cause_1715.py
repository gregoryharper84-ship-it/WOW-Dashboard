"""#1715: typed, value-free causes for prop self-acceptance bootstrap failures.

Production logged only `reason=BOOTSTRAP_CONFIG_INVALID error_type=ValueError`
on every startup, so a stale one-shot fixture was indistinguishable from a
malformed one. These tests pin one cause per failure, ValueError compatibility,
and that no configured value reaches the log.
"""
from __future__ import annotations

import asyncio
import json
import logging

import pytest

import prop_live_model_acceptance as pla
from prop_live_model_acceptance import BootstrapConfigError, _bootstrap_pick_payload

SENTINEL_PLAYER = "Zzsentinel Playername"
SENTINEL_EVENT = "SENTINEL_EVENT_9f3a"


def _candidate(**overrides):
    base = {
        "event_id": SENTINEL_EVENT,
        "event_start_time": "2099-10-04T17:00:00Z",
        "sport": "NFL",
        "player": SENTINEL_PLAYER,
        "stat_type": "PLAYER_RECEIVING_YARDS",
        "line": 47.5,
        "direction": "MORE",
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not _DROP}


_DROP = object()


@pytest.mark.parametrize(
    "raw,cause",
    [
        ("{not json", "BOOTSTRAP_JSON_UNPARSEABLE"),
        (json.dumps([1, 2]), "BOOTSTRAP_JSON_NOT_OBJECT"),
        (json.dumps(_candidate(direction="OVER")), "BOOTSTRAP_DIRECTION_INVALID"),
        (json.dumps(_candidate(line="abc")), "BOOTSTRAP_LINE_INVALID"),
        (json.dumps(_candidate(line=True)), "BOOTSTRAP_LINE_INVALID"),
        (json.dumps(_candidate(line=False)), "BOOTSTRAP_LINE_INVALID"),
        (json.dumps(_candidate(line="Infinity")), "BOOTSTRAP_LINE_NONFINITE"),
        (json.dumps(_candidate(event_start_time="2099-10-04T17:00:00")), "BOOTSTRAP_EVENT_START_INVALID"),
        (json.dumps(_candidate(event_start_time="not-a-time")), "BOOTSTRAP_EVENT_START_INVALID"),
        (json.dumps(_candidate(event_start_time="2020-01-01T00:00:00Z")), "BOOTSTRAP_EVENT_ALREADY_STARTED"),
    ],
)
def test_each_bootstrap_failure_has_distinct_typed_cause(raw, cause):
    with pytest.raises(BootstrapConfigError) as info:
        _bootstrap_pick_payload(raw)
    assert info.value.cause == cause
    assert isinstance(info.value, ValueError)  # existing callers/tests catch ValueError
    assert SENTINEL_PLAYER not in str(info.value) and SENTINEL_EVENT not in str(info.value)


def test_missing_fields_reports_only_field_names():
    raw = json.dumps(_candidate(direction=_DROP, line=_DROP))
    with pytest.raises(BootstrapConfigError) as info:
        _bootstrap_pick_payload(raw)
    assert info.value.cause == "BOOTSTRAP_FIELDS_MISSING"
    assert info.value.missing_fields == ("line", "direction")
    assert set(info.value.missing_fields) <= set(pla.BOOTSTRAP_REQUIRED_FIELDS)


def test_valid_future_bootstrap_is_unchanged():
    payload = _bootstrap_pick_payload(json.dumps(_candidate(direction="less")))
    row = payload["rows"][0]
    assert row["direction"] == "LESS" and row["line"] == 47.5
    assert row["source_type"] == "AUTONOMOUS_DISCOVERY"


class _NoNetwork:
    def __init__(self, *_a, **_k):
        raise AssertionError("bootstrap failure must not reach /score-pick-request")


def _run_bootstrap(monkeypatch, caplog, raw):
    monkeypatch.setattr(pla.httpx, "AsyncClient", _NoNetwork)
    caplog.set_level(logging.ERROR, logger="test.1715")
    result = asyncio.run(
        pla._bootstrap_fresh_snapshot("key-not-secret", "1", raw, logging.getLogger("test.1715"))
    )
    assert result is None
    return "\n".join(r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("boolean_line", [True, False])
def test_boolean_line_fails_before_network_and_redacts_values(monkeypatch, caplog, boolean_line):
    raw = json.dumps(_candidate(line=boolean_line))
    text = _run_bootstrap(monkeypatch, caplog, raw)
    assert "reason=BOOTSTRAP_CONFIG_INVALID" in text
    assert "error_type=BootstrapConfigError" in text
    assert "cause=BOOTSTRAP_LINE_INVALID" in text
    assert "can_execute=false" in text
    for sensitive in (SENTINEL_PLAYER, SENTINEL_EVENT, "key-not-secret"):
        assert sensitive not in text


def test_stale_fixture_logs_typed_cause_without_values(monkeypatch, caplog):
    raw = json.dumps(_candidate(event_start_time="2020-01-01T00:00:00Z", line=12.5))
    text = _run_bootstrap(monkeypatch, caplog, raw)
    assert "reason=BOOTSTRAP_CONFIG_INVALID" in text  # compatibility with existing log queries
    assert "error_type=BootstrapConfigError" in text
    assert "cause=BOOTSTRAP_EVENT_ALREADY_STARTED" in text
    assert "missing_fields=NONE" in text
    assert "can_execute=false" in text
    for value in (SENTINEL_PLAYER, SENTINEL_EVENT, "12.5", "2020-01-01", "key-not-secret"):
        assert value not in text


def test_missing_fields_log_names_fields_only(monkeypatch, caplog):
    raw = json.dumps(_candidate(direction=_DROP))
    text = _run_bootstrap(monkeypatch, caplog, raw)
    assert "cause=BOOTSTRAP_FIELDS_MISSING missing_fields=direction" in text
    assert SENTINEL_PLAYER not in text


def test_unexpected_exception_is_unclassified_not_dropped(monkeypatch, caplog):
    def boom(_raw):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(pla, "_bootstrap_pick_payload", boom)
    text = _run_bootstrap(monkeypatch, caplog, "{}")
    assert "error_type=RuntimeError cause=BOOTSTRAP_UNCLASSIFIED" in text
    assert "MODEL_UNAVAILABLE" not in text
