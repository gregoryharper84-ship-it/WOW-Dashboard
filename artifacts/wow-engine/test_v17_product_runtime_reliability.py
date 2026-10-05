import json
from pathlib import Path

from v17 import engineering_agent_team as team
from v17 import product_runtime_reliability as subject


def _ok_payload(can_execute=False, **extra):
    return {
        "can_execute": can_execute,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        **extra,
    }


def test_platform_and_product_runtime_failures_route_to_dedicated_support():
    assert team.route_failure("CI_FAILURE") == "platform-reliability"
    assert team.route_failure("CONNECTOR_WRITE_FAILURE") == "platform-reliability"
    assert team.route_failure("LIVE_GPT_EDITOR_SYNC_FAILURE") == "product-runtime-reliability"
    assert team.route_failure("KALSHI_WEATHER_RUNTIME_FAILURE") == "product-runtime-reliability"

    platform = team.select_support_subagent({"typed_failure": "CI_FAILURE"})
    runtime = team.select_support_subagent({"typed_failure": "KALSHI_WEATHER_RUNTIME_FAILURE"})
    assert platform.subagent == "PLATFORM_RELIABILITY_SUBAGENT"
    assert runtime.subagent == "PRODUCT_RUNTIME_RELIABILITY_SUBAGENT"
    assert platform.implementation_lease is False
    assert runtime.implementation_lease is False


def test_safe_wow_probe_requires_transport_and_validation_boundary(monkeypatch):
    def fake(method, url, **kwargs):
        if url.endswith("/health/live"):
            return 200, {"status": "ok"}
        if url.endswith("/governance"):
            return 200, _ok_payload(runtime_generation="V17_ACTIVE")
        if url.endswith("/v17/host-contract"):
            return 200, _ok_payload()
        if url.endswith("/score-pick-request"):
            assert method == "POST"
            assert kwargs["payload"] == {}
            return 422, {"detail": {"code": "PICK_REQUEST_INVALID", "can_execute": False}}
        raise AssertionError(url)

    monkeypatch.setattr(subject, "_http_json", fake)
    result = subject.probe_wow("secret-not-emitted")

    assert result["status"] == "PASS"
    assert result["checks"]["safe_invalid_score_http"] == 422
    assert result["can_execute"] is False


def test_safe_kalshi_probe_never_requests_probability(monkeypatch):
    def fake(method, url, **kwargs):
        if url.endswith("/kalshi-weather/v17/governance"):
            return 200, _ok_payload(service="KALSHI_WEATHER_MARKET_EXPERT")
        if url.endswith("/kalshi-weather/v17/free-sources"):
            return 200, _ok_payload()
        if url.endswith("/kalshi-weather/v17/analyze"):
            assert method == "POST"
            assert kwargs["payload"] == {
                "lane": "SYNTHETIC_RELIABILITY_PROBE",
                "ticker": "SYNTHETIC_ONLY",
            }
            return 200, {
                "code": "WEATHER_LANE_RUNTIME_NOT_CERTIFIED",
                "controlling_specialist": "KALSHI_WEATHER_MARKET_EXPERT",
                "probability_publishable": False,
                "p_yes": None,
                "p_no": None,
                "can_execute": False,
            }
        raise AssertionError(url)

    monkeypatch.setattr(subject, "_http_json", fake)
    result = subject.probe_kalshi_weather("secret-not-emitted")

    assert result["status"] == "PASS"
    assert result["checks"]["safe_unsupported_lane_http"] == 200
    assert result["can_execute"] is False


def test_editor_contract_treats_kalshi_as_in_process_not_external_editor(tmp_path: Path):
    contract = tmp_path / "artifacts" / "wow-engine" / "v17" / "custom_engine_alignment_contract.json"
    contract.parent.mkdir(parents=True)
    contract.write_text(json.dumps({
        "editor_attestation": {
            "WOW_BETTING_ENGINE": {"required": True, "status": "LIVE_EDITOR_SYNC_VERIFIED"},
            "LLP_TEAM_BETTING_ENGINE": {"required": True, "status": "LIVE_EDITOR_SYNC_VERIFIED"},
            "KALSHI_WEATHER_MARKET_EXPERT": {
                "required": False,
                "status": "IN_PROCESS_SPECIALIST_NO_EXTERNAL_EDITOR_SYNC_REQUIRED",
            },
        }
    }), encoding="utf-8")

    state = subject.editor_contract_state(tmp_path)

    assert state["WOW_BETTING_ENGINE"]["healthy"] is True
    assert state["LLP_TEAM_BETTING_ENGINE"]["healthy"] is True
    assert state["KALSHI_WEATHER_MARKET_EXPERT"]["healthy"] is True
    assert state["KALSHI_WEATHER_MARKET_EXPERT"]["required"] is False


def test_missing_action_key_fails_closed_without_attempting_authenticated_probe(monkeypatch):
    calls = []

    def fake(method, url, **kwargs):
        calls.append(url)
        if url.endswith("/health"):
            return 200, {"can_execute": False}
        raise AssertionError(url)

    monkeypatch.setattr(subject, "_http_json", fake)
    result = subject.probe_llp(None)

    assert result["status"] == "BLOCKED_WITH_EXACT_REASON"
    assert "WOW_ACTION_API_KEY_UNAVAILABLE" in result["blockers"]
    assert result["checks"]["host_contract_http"] is None
    assert result["checks"]["safe_invalid_score_http"] is None
    assert calls == [subject.LLP_GATEWAY_ORIGIN + "/health"]
