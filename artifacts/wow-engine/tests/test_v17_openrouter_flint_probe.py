"""Offline negative tests for the unprivileged Flint fixed-input canary."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

PATH = Path(__file__).resolve().parents[1] / "v17/engineering_openrouter_flint_probe.py"
spec = importlib.util.spec_from_file_location("engineering_openrouter_flint_probe", PATH)
assert spec and spec.loader
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

SHA = "a" * 40
ENV = {
    "WOW_FLINT_PROBE_ENABLED": "true",
    "WOW_FLINT_EXTERNAL_CONSENT": "true",
    "OPENROUTER_API_KEY": "synthetic-provider-secret",
    "GITHUB_ACTIONS": "true",
    "GITHUB_REPOSITORY": probe.REPO,
    "GITHUB_EVENT_NAME": "workflow_dispatch",
    "GITHUB_REF": "refs/heads/main",
    "GITHUB_WORKFLOW_REF": probe.APPROVED_WORKFLOW_REF,
    "GITHUB_SHA": SHA,
    "GITHUB_RUN_ID": "123",
    "GITHUB_TOKEN": "synthetic-github-token",
}
RUN = {
    "id": 123, "event": "workflow_dispatch", "head_sha": SHA,
    "head_branch": "main", "path": probe.APPROVED_WORKFLOW_REF,
    "status": "in_progress", "repository": {"full_name": probe.REPO},
}
REPLY = {
    "model": probe.MODEL, "id": "gen-123",
    "choices": [{"finish_reason": "stop", "message": {"content": "assert even(2) is True"}}],
    "usage": {"prompt_tokens": 15, "completion_tokens": 8, "total_tokens": 23},
}


class Reply:
    def __init__(self, data, url=None):
        self.data = data
        if url is not None:
            self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def read(self, size):
        return self.data[:size]


def mock_transport(*, run=None, completion=None, status=None):
    calls = []
    captured = []
    def opener(request, timeout):
        calls.append((request.full_url, timeout))
        captured.append(request)
        assert request.get_header("Authorization") is not None
        assert "Authorization" in request.unredirected_hdrs
        assert "Authorization" not in request.headers
        if "api.github.com" in request.full_url:
            assert request.get_header("Authorization") == "Bearer synthetic-github-token"
            return Reply(json.dumps(RUN if run is None else run).encode())
        assert request.full_url == probe.ENDPOINT
        assert request.get_header("Authorization") == "Bearer synthetic-provider-secret"
        if status is not None:
            raise HTTPError(probe.ENDPOINT, status, "private-provider-text", {}, None)
        return Reply(json.dumps(REPLY if completion is None else completion).encode())
    return opener, calls, captured


def test_local_opt_in_and_key_never_authorize_network():
    for env, expected in [
        ({}, "DISABLED"),
        ({"WOW_FLINT_PROBE_ENABLED": "true"}, "KEY_MISSING"),
        ({"WOW_FLINT_PROBE_ENABLED": "true", "OPENROUTER_API_KEY": "fake"}, "CONSENT_MISSING"),
        ({**ENV, "GITHUB_TOKEN": ""}, "HOST_IDENTITY_UNVERIFIED"),
        ({**ENV, "GITHUB_EVENT_NAME": "schedule"}, "HOST_IDENTITY_UNVERIFIED"),
        ({**ENV, "GITHUB_SHA": "not a sha"}, "HOST_IDENTITY_UNVERIFIED"),
        ({**ENV, "GITHUB_REF": "refs/heads/some-pr"}, "HOST_IDENTITY_UNVERIFIED"),
        ({**ENV, "GITHUB_WORKFLOW_REF": "other"}, "HOST_IDENTITY_UNVERIFIED"),
        ({**ENV, "GITHUB_ACTIONS": ""}, "HOST_IDENTITY_UNVERIFIED"),
    ]:
        result = probe.smoke(env, opener=lambda *a, **k: 1 / 0)
        assert result["status"] == expected
        assert result["network_attempted"] is False
        assert result["can_execute"] is False
        assert result["production_verified"] is False


def test_preflight_makes_no_provider_request_and_is_not_completion():
    result = probe.preflight(ENV)
    assert result["status"] == "HOST_READBACK_REQUIRED"
    assert result["network_attempted"] is False
    assert result["provider_model_claim_matched"] is False


def test_verified_host_allows_exactly_one_fixed_provider_request():
    opener, calls, captured = mock_transport()
    result = probe.smoke(ENV, opener=opener)
    assert result["status"] == "SMOKE_PASSED"
    assert result["provider_model_claim_matched"] is True
    assert result["total_tokens"] == 23
    assert result["production_verified"] is False
    assert result["can_execute"] is False
    assert result["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert calls == [
        ("https://api.github.com/repos/" + probe.REPO + "/actions/runs/123", 12),
        (probe.ENDPOINT, 30),
    ]
    assert json.loads(captured[1].data)["model"] == probe.MODEL
    assert json.loads(captured[1].data)["messages"] == [{"role": "user", "content": probe.PROMPT}]
    for req in captured:
        assert req.get_header("Authorization") is None
        assert "Authorization" not in req.unredirected_hdrs
    assert "synthetic-provider-secret" not in str(result)
    assert "synthetic-github-token" not in str(result)


def test_spoofed_environment_does_not_bypass_signed_in_host_readback():
    for field, value in (
        ("head_sha", "b" * 40),
        ("head_branch", "feature"),
        ("path", "untrusted.yml@refs/heads/main"),
        ("event", "schedule"),
        ("id", 999),
        ("status", "completed"),
        ("repository", {"full_name": "attacker/repo"}),
    ):
        opener, calls, _ = mock_transport(run={**RUN, field: value})
        result = probe.smoke(ENV, opener=opener)
        assert result["status"] == "HOST_IDENTITY_UNVERIFIED"
        assert len(calls) == 1
        assert result["network_attempted"] is False


def test_unreachable_or_malformed_github_run_fails_closed_without_provider_attempt():
    for runner in [
        lambda *_a, **_k: Reply(b"{bad"),
        lambda *_a, **_k: Reply(b'{"id":123}'),
        lambda *_a, **_k: (_ for _ in ()).throw(URLError("private")),
        lambda *_a, **_k: (_ for _ in ()).throw(HTTPError("github", 403, "private", {}, None)),
        lambda *_a, **_k: Reply(json.dumps(RUN).encode(), url="http://evil.example"),
    ]:
        count = []
        def capture(req, timeout):
            count.append(req.full_url)
            return runner(req, timeout)
        result = probe.smoke(ENV, opener=capture)
        assert result["status"] == "HOST_IDENTITY_UNVERIFIED"
        assert len(count) == 1
        assert result["network_attempted"] is False


def test_model_echo_mismatch_is_not_a_verified_flint_completion():
    for payload in [
        {**REPLY, "model": "other/model"},
        {**REPLY, "model": ""},
        {k: v for k, v in REPLY.items() if k != "model"},
        {**REPLY, "id": None},
        {**REPLY, "id": "x" * 129},
    ]:
        opener, calls, _ = mock_transport(completion=payload)
        result = probe.smoke(ENV, opener=opener)
        assert result["status"] == "MODEL_IDENTITY_UNVERIFIED"
        assert result["provider_model_claim_matched"] is False
        assert len(calls) == 2


def test_incomplete_or_invalid_response_never_passes():
    for finish in ("length", "content_filter", "tool_calls", None, "unknown"):
        result = probe.validate_completion({**REPLY, "choices": [{
            "finish_reason": finish, "message": {"content": "plausible"}}]})
        assert result["status"] == "FLINT_MODEL_COMPLETION_INCOMPLETE"
    for payload in (None, {}, {**REPLY, "choices": []},
                    {**REPLY, "choices": [{"finish_reason": "stop", "message": {"content": ""}}]}):
        assert probe.validate_completion(payload)["status"] in ("OUTPUT_INVALID", "MODEL_IDENTITY_UNVERIFIED")


def test_provider_redirects_are_denied_even_when_caller_opener_is_permissive():
    def response(request, timeout):
        if "api.github.com" in request.full_url:
            return Reply(json.dumps(RUN).encode())
        return Reply(json.dumps(REPLY).encode(), url="http://outside.example")
    result = probe.smoke(ENV, opener=response)
    assert result["status"] == "OUTPUT_INVALID"
    assert result["network_attempted"] is True
    assert isinstance(probe._opener.__code__.co_consts, tuple)  # transport factory defined
    assert probe.NoRedirect.redirect_request(None, None, None, 302, "move", {}, "http://evil") is None
    assert probe.classify_http_status(302) == "REDIRECT_DENIED"


def test_classified_upstream_errors_do_not_retry_or_log_secret():
    for http_code, status in ((301, "REDIRECT_DENIED"), (401, "AUTH_FAILED"),
                              (402, "PAYMENT_REQUIRED"), (404, "FLINT_HTTP_404_UNATTRIBUTED"),
                              (429, "RATE_LIMITED"), (503, "UPSTREAM_FAILURE")):
        opener, calls, captured = mock_transport(status=http_code)
        result = probe.smoke(ENV, opener=opener)
        assert result["status"] == status
        assert len(calls) == 2
        assert captured[1].get_header("Authorization") is None
        assert "private-provider-text" not in str(result)


def test_redacted_transport_failure_removes_header_and_does_not_retry():
    requests = []
    def opener(request, timeout):
        requests.append(request)
        if request.full_url.startswith("https://api.github.com"):
            return Reply(json.dumps(RUN).encode())
        raise URLError("sensitive-error-body")
    result = probe.smoke(ENV, opener=opener)
    assert result["status"] == "TRANSPORT_FAILURE"
    assert len(requests) == 2
    assert requests[1].get_header("Authorization") is None
    assert "sensitive-error-body" not in str(result)


def test_main_preflight_returns_nonzero_and_never_claims_a_smoke():
    with patch.object(probe.os, "environ", {"WOW_FLINT_PROBE_ENABLED": "true"}):
        with patch("sys.argv", ["flint", "preflight"]):
            assert probe.main() != 0
    with patch.object(probe.os, "environ", {}):
        with patch("sys.argv", ["flint", "smoke"]):
            assert probe.main() != 0


def test_non_authoritative_research_statuses_not_a_fitted_model_contract():
    for status in ("DISABLED", "CONSENT_MISSING", "HOST_IDENTITY_UNVERIFIED",
                   "MODEL_IDENTITY_UNVERIFIED", "TRANSPORT_FAILURE", "SMOKE_PASSED"):
        r = probe.receipt(status)
        assert r["can_execute"] is False
        assert r["production_verified"] is False
        assert r["terminal_authority"] == "V17_TERMINAL_REDUCER"
        assert "probability" not in r
