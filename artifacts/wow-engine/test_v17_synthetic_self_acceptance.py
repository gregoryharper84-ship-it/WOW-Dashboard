import logging

import pytest

import v17_synthetic_self_acceptance as mod


def test_prop_payload_targets_unsupported_sport_with_no_certified_model():
    payload = mod._prop_payload(event_start="2026-09-03T00:00:00+00:00")
    row = payload["rows"][0]
    assert row["sport"] == "TABLE_TENNIS"
    assert row["stat_type"] == "ACES"
    assert row["direction"] == "MORE"
    assert row["line"] == 2.5


def test_team_event_payload_targets_unsupported_sport_with_no_certified_model():
    payload = mod._team_event_payload(event_date="2026-09-03")
    row = payload["rows"][0]
    assert row["sport"] == "TABLE_TENNIS"
    assert row["league"] == "TEST_LEAGUE"
    assert row["price_required_for_objective"] is False


@pytest.mark.anyio
async def test_missing_api_key_fails_closed_without_any_http_call(monkeypatch):
    monkeypatch.delenv("WOW_ACTION_API_KEY", raising=False)
    logger = logging.getLogger("test-v17-synthetic-self-acceptance")
    result = await mod.run_v17_synthetic_self_acceptance(logger)
    assert result == {"status": "FAILED", "code": "WOW_ACTION_API_KEY_MISSING", "can_execute": False}


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, responses, *, timeout=None):
        self._responses = list(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, *, headers, json):
        return self._responses.pop(0)


def _patch_client(monkeypatch, responses):
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(responses, **kwargs))


def _projected_candidate():
    return {
        "official_event_id": "824226",
        "official_date": "2026-09-09",
        "event_start_time": "2026-09-10T00:10:00+00:00",
        "home_team": "Home Team",
        "away_team": "Away Team",
        "snapshot_id": "snapshot-824226",
        "snapshot_timestamp": "2026-09-09T13:30:00+00:00",
    }


@pytest.mark.anyio
async def test_both_scenarios_failing_closed_reports_pass(monkeypatch):
    monkeypatch.setenv("WOW_ACTION_API_KEY", "test-key")
    _patch_client(
        monkeypatch,
        [
            _FakeResponse(200, {
                "rows": [{
                    "row_key": "TEST-PROP-001", "terminal_status": "HELD",
                    "terminal_label": "MODEL_UNAVAILABLE", "probability_publishable": False,
                    "can_execute": False,
                }],
            }),
            _FakeResponse(200, {
                "rows": [{
                    "research_run_id": "TEST-EVENT-001-RUN", "terminal_status": "HELD",
                    "code": "MODEL_UNAVAILABLE", "probability_publishable": False, "can_execute": False,
                }],
            }),
        ],
    )
    logger = logging.getLogger("test-v17-synthetic-self-acceptance")
    result = await mod.run_v17_synthetic_self_acceptance(logger)
    assert result["status"] == "PASS"
    assert result["prop_ok"] is True
    assert result["team_event_ok"] is True
    assert result["can_execute"] is False


@pytest.mark.anyio
async def test_a_fabricated_probability_would_fail_this_acceptance_check(monkeypatch):
    monkeypatch.setenv("WOW_ACTION_API_KEY", "test-key")
    _patch_client(
        monkeypatch,
        [
            _FakeResponse(200, {
                "rows": [{
                    "row_key": "TEST-PROP-001", "terminal_status": "COMPLETED",
                    "terminal_label": "MODEL_QUALIFIED_HOLD", "probability_publishable": True,
                    "can_execute": False,
                }],
            }),
            _FakeResponse(200, {
                "rows": [{
                    "research_run_id": "TEST-EVENT-001-RUN", "terminal_status": "HELD",
                    "code": "MODEL_UNAVAILABLE", "probability_publishable": False, "can_execute": False,
                }],
            }),
        ],
    )
    logger = logging.getLogger("test-v17-synthetic-self-acceptance")
    result = await mod.run_v17_synthetic_self_acceptance(logger)
    assert result["status"] == "FAIL"
    assert result["prop_ok"] is False


@pytest.mark.anyio
async def test_projected_422_preserves_typed_backend_diagnostics(monkeypatch):
    monkeypatch.setenv("WOW_ACTION_API_KEY", "test-key")
    monkeypatch.setattr(mod, "_real_projected_mlb_candidate", lambda now: _projected_candidate())
    _patch_client(
        monkeypatch,
        [
            _FakeResponse(200, {
                "rows": [{
                    "row_key": "TEST-PROP-001", "terminal_status": "HELD",
                    "terminal_label": "MODEL_UNAVAILABLE", "probability_publishable": False,
                    "can_execute": False,
                }],
            }),
            _FakeResponse(200, {
                "rows": [{
                    "research_run_id": "TEST-EVENT-001-RUN", "terminal_status": "HELD",
                    "code": "MODEL_UNAVAILABLE", "probability_publishable": False, "can_execute": False,
                }],
            }),
            _FakeResponse(422, {
                "detail": {
                    "code": "RUN_INVALID_ACQUISITION_INCOMPLETE",
                    "blocker_code": "MLB_TEAM_EVENT_CANONICAL_EVIDENCE_UNAVAILABLE",
                    "failure_class": "RUN_INVALID_ACQUISITION_INCOMPLETE",
                    "missing_fields": ["away_lineup_status", "home_lineup_status"],
                    "probability_publishable": False,
                    "can_execute": False,
                }
            }),
        ],
    )
    logger = logging.getLogger("test-v17-synthetic-self-acceptance")
    result = await mod.run_v17_synthetic_self_acceptance(logger)

    assert result["status"] == "FAIL"
    assert result["projected_lineup_http_status"] == 422
    assert result["projected_lineup_code"] == "RUN_INVALID_ACQUISITION_INCOMPLETE"
    assert result["projected_lineup_blocker_code"] == "MLB_TEAM_EVENT_CANONICAL_EVIDENCE_UNAVAILABLE"
    assert result["projected_lineup_failure_class"] == "RUN_INVALID_ACQUISITION_INCOMPLETE"
    assert result["projected_lineup_missing_fields"] == ["away_lineup_status", "home_lineup_status"]
    assert result["projected_lineup_failed_checks"] == ["http_status"]
    assert result["projected_lineup_code"] != "MODEL_UNAVAILABLE"
    assert result["can_execute"] is False
