"""Offline negative and success tests for opt-in Flint API canary."""
import importlib.util
from pathlib import Path
from urllib.error import HTTPError, URLError

MODULE_PATH = Path(__file__).resolve().parents[1] / "v17/engineering_openrouter_flint_probe.py"
spec = importlib.util.spec_from_file_location("engineering_openrouter_flint_probe", MODULE_PATH)
assert spec and spec.loader
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class FakeResponse:
    def __init__(self, data: bytes):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit: int):
        return self.data


def test_disabled_default_does_not_touch_network():
    result = probe.smoke({"OPENROUTER_API_KEY": "synthetic"}, opener=lambda *_a, **_kw: 1 / 0)
    assert result["status"] == "DISABLED" and result["network_attempted"] is False
    assert result["can_execute"] is False


def test_enabled_without_key_does_not_touch_network():
    result = probe.smoke({"WOW_FLINT_PROBE_ENABLED": "true"}, opener=lambda *_a, **_kw: 1 / 0)
    assert result["status"] == "KEY_MISSING" and result["network_attempted"] is False


def test_ready_preflight_is_not_api_success():
    result = probe.preflight({"WOW_FLINT_PROBE_ENABLED": "true", "OPENROUTER_API_KEY": "synthetic"})
    assert result["status"] == "READY_FOR_ONE_SYNTHETIC_REQUEST"
    assert result["network_attempted"] is False and result["agent_activation"] is False


def test_one_synthetic_request_and_redacted_receipt():
    calls = []
    def open_once(request, timeout):
        calls.append((request, timeout))
        return FakeResponse(b'{"choices":[{"finish_reason":"stop","message":{"content":"assert even(2) is True"}}],"usage":{"prompt_tokens":15,"completion_tokens":8,"total_tokens":23}}')
    result = probe.smoke({"WOW_FLINT_PROBE_ENABLED": "true", "OPENROUTER_API_KEY": "test-only-secret"}, opener=open_once)
    assert result["status"] == "SMOKE_PASSED" and result["total_tokens"] == 23
    assert len(calls) == 1 and calls[0][1] == 30
    assert b'test-only-secret' not in str(result).encode()
    assert probe.MODEL.encode() in calls[0][0].data
    assert calls[0][0].full_url == probe.ENDPOINT
    assert result["can_execute"] is False and result["production_routing_changed"] is False


def test_all_error_codes_are_typed_and_do_not_retry():
    for status, expected in ((401, "AUTH_FAILED"), (403, "AUTH_FAILED"), (402, "PAYMENT_REQUIRED"), (429, "RATE_LIMITED"), (503, "UPSTREAM_FAILURE"), (404, "FLINT_HTTP_404_UNATTRIBUTED"), (400, "HTTP_FAILURE")):
        calls = []
        def raise_http(*_args, **_kw):
            calls.append(True)
            raise HTTPError(probe.ENDPOINT, status, "secret-danger", {}, None)
        result = probe.smoke({"WOW_FLINT_PROBE_ENABLED": "true", "OPENROUTER_API_KEY": "synthetic"}, opener=raise_http)
        assert result["status"] == expected and len(calls) == 1
        assert "secret-danger" not in str(result)
        assert result["can_execute"] is False


def test_transport_and_malformed_response_are_distinct():
    def broken(*_args, **_kw):
        raise URLError("sensitive transport context")
    env = {"WOW_FLINT_PROBE_ENABLED": "true", "OPENROUTER_API_KEY": "synthetic"}
    assert probe.smoke(env, opener=broken)["status"] == "TRANSPORT_FAILURE"
    for data in (b"{bad", b'{}', b'{"choices":[]}', b'{"choices":[{"message":{"content":""}}]}'):
        result = probe.smoke(env, opener=lambda *_a, **_kw: FakeResponse(data))
        assert result["status"] == "OUTPUT_INVALID"
        assert result["network_attempted"] is True


def test_smoke_rejects_incomplete_or_unattributed_completion():
    for reason in ("length", "content_filter", "tool_calls", None, "unexpected"):
        response = {"choices": [{"finish_reason": reason, "message": {"content": "plausible answer"}}]}
        result = probe.validate_completion(response)
        assert result["status"] == "FLINT_MODEL_COMPLETION_INCOMPLETE"
        assert result["network_attempted"] is True
        assert result["can_execute"] is False


def test_canary_cannot_override_governance():
    for result in (probe.preflight({}), probe.validate_completion({"choices":[{"message":{"content":"ok"}}]})):
        assert result["terminal_authority"] == "V17_TERMINAL_REDUCER"
        assert result["can_execute"] is False
        assert result["agent_activation"] is False
        assert result["production_routing_changed"] is False
