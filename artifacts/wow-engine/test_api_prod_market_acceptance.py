import asyncio
import logging

import httpx

import api_prod_market_acceptance as acceptance


def _response(body, status=422):
    request = httpx.Request("POST", "http://test/score-prop")
    return httpx.Response(status, request=request, json=body)


def test_probe_payload_is_symmetric_whole_number_boundary():
    more = acceptance._probe_payload("MORE")
    less = acceptance._probe_payload("LESS")

    assert more["line"] == 4.0
    assert less["line"] == 4.0
    assert more["direction"] == "MORE"
    assert less["direction"] == "LESS"
    assert more["source_snapshot_id"] != less["source_snapshot_id"]


def test_validate_probe_response_accepts_expected_fail_closed_422():
    response = _response(
        {
            "detail": {
                "code": "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND",
                "failure_class": "RUN_INVALID_ACQUISITION_INCOMPLETE",
                "specialist_invoked": False,
                "probability_publishable": False,
                "can_execute": False,
            }
        }
    )

    ok, code, leaked = acceptance._validate_probe_response(response)

    assert ok is True
    assert code == "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND"
    assert leaked == []


def test_validate_probe_response_rejects_numeric_probability_leak():
    response = _response(
        {
            "detail": {
                "code": "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND",
                "failure_class": "RUN_INVALID_ACQUISITION_INCOMPLETE",
                "specialist_invoked": False,
                "probability_publishable": False,
                "calibrated_probability": 0.61,
                "can_execute": False,
            }
        }
    )

    ok, _code, leaked = acceptance._validate_probe_response(response)

    assert ok is False
    assert leaked == ["calibrated_probability"]


def test_live_probe_authenticates_more_and_less_and_logs_only_sanitized_metadata(monkeypatch, caplog):
    calls = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers, json):
            calls.append((url, headers, json))
            return _response(
                {
                    "detail": {
                        "code": "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND",
                        "failure_class": "RUN_INVALID_ACQUISITION_INCOMPLETE",
                        "specialist_invoked": False,
                        "probability_publishable": False,
                        "can_execute": False,
                    }
                }
            )

    async def no_sleep(_seconds):
        return None

    monkeypatch.setenv("WOW_ACTION_API_KEY", "super-secret-test-key")
    monkeypatch.setenv("PORT", "10000")
    monkeypatch.setattr(acceptance.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(acceptance.asyncio, "sleep", no_sleep)

    caplog.set_level(logging.WARNING, logger="wow.prop.acceptance")
    asyncio.run(acceptance._run_prop_live_self_acceptance())

    assert [call[2]["direction"] for call in calls] == ["MORE", "LESS"]
    assert all(call[1]["Authorization"] == "Bearer super-secret-test-key" for call in calls)
    assert all(call[1]["X-WOW-Model-Identity"] == "WOW_BETTING_ENGINE" for call in calls)
    assert "super-secret-test-key" not in caplog.text
    assert "directions=MORE,LESS" in caplog.text
    assert "zero_probability_leak=true" in caplog.text
    assert "settlement_math=NOT_PROVEN" in caplog.text
    assert "model_path=NOT_PROVEN" in caplog.text
    assert "can_execute=false" in caplog.text
