"""Exercise the actual manual diagnostic with fake credentials and HTTP responses."""
import io
import json
from pathlib import Path
import urllib.error
import urllib.request

import pytest
import yaml

WF = Path(__file__).resolve().parents[2] / ".github/workflows/wow-v17-anthropic-key-diagnostic.yml"
FAKE_KEY = "sk-ant-api03-fake-key-for-offline-tests"
FAKE_WORKSPACE = "wrkspc_OfflineTest123"


def workflow():
    return yaml.safe_load(WF.read_text())


def exercise(monkeypatch, capsys, results, key=FAKE_KEY, workspace=FAKE_WORKSPACE):
    monkeypatch.setenv("K", key)
    monkeypatch.setenv("WORKSPACE_ID", workspace)
    calls = []
    responses = iter(results)

    def fake_urlopen(request, timeout):
        calls.append(request)
        assert timeout == 30
        assert request.full_url == "https://api.anthropic.com/v1/messages"
        assert request.get_header("X-api-key") == key
        assert request.get_header("Anthropic-workspace-id") == workspace
        payload = json.loads(request.data)
        assert payload["max_tokens"] == 1
        assert payload["messages"] == [{"role": "user", "content": "hi"}]
        status, body = next(responses)
        if status is None:
            raise urllib.error.URLError("transport echoed " + key)
        stream = io.BytesIO(body if isinstance(body, bytes) else json.dumps(body).encode())
        if status != 200:
            raise urllib.error.HTTPError(request.full_url, status, "failure", {}, stream)
        stream.code = status
        return stream

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    run = workflow()["jobs"]["api-key-diagnostic"]["steps"][0]["run"]
    assert run.startswith("python3 <<'PY'\n")
    script = run.removeprefix("python3 <<'PY'\n").removesuffix("PY\n")
    namespace = {"__name__": "diagnostic_test"}
    exec(compile(script, str(WF), "exec"), namespace)
    status = namespace["main"]()
    return status, capsys.readouterr().out, calls


def test_manual_only_and_no_token_permissions():
    doc = workflow()
    assert set(doc.get(True, doc.get("on"))) == {"workflow_dispatch"}
    assert doc["permissions"] == {}
    job = doc["jobs"]["api-key-diagnostic"]
    assert job["timeout-minutes"] == 5
    assert len(job["steps"]) == 1
    assert job["steps"][0]["env"] == {
        "K": "${{ secrets.ANTHROPIC_API_KEY }}",
        "WORKSPACE_ID": "${{ inputs.workspace_id }}",
    }


def test_success_prints_status_only(monkeypatch, capsys):
    result, output, calls = exercise(monkeypatch, capsys, [(200, {"content": "PRIVATE COMPLETION"})] * 2)
    assert result == 0
    assert len(calls) == 2
    assert [json.loads(c.data)["model"] for c in calls] == ["claude-opus-5-5", "claude-sonnet-5-5"]
    assert "format: sk-ant-api03" in output
    assert output.count("HTTP 200") == 4  # text and notice for each model
    assert "PRIVATE COMPLETION" not in output
    assert FAKE_KEY not in output


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500])
def test_http_failure_reports_message_and_fails_after_both_models(monkeypatch, capsys, status):
    error = {"error": {"type": "invalid_request_error", "message": "Insufficient credit balance"}, "request_id": "req_test123", "private": "UNRELATED BODY"}
    result, output, calls = exercise(monkeypatch, capsys, [(status, error), (200, {})])
    assert result == 1
    assert len(calls) == 2
    assert f"HTTP {status}" in output
    assert "error.message=Insufficient credit balance" in output
    assert "request_id=req_test123" in output
    assert "UNRELATED BODY" not in output


def test_redacts_secrets_and_escapes_annotation_commands(monkeypatch, capsys):
    other_key = "sk-ant-usr-another-fake-secret"
    message = FAKE_KEY + " " + other_key + " Bearer oauth-fake-token x-api-key: unknown-token\r\n::error::injected %0A " + "z" * 800
    error = {"error": {"type": FAKE_KEY, "message": message}, "request_id": FAKE_KEY}
    result, output, _ = exercise(monkeypatch, capsys, [(400, error)] * 2)
    assert result == 1
    for secret in (FAKE_KEY, other_key, "oauth-fake-token", "unknown-token"):
        assert secret not in output
    assert "error.type=unknown" in output
    assert "[REDACTED]" in output
    assert not any(line.startswith("::error::") for line in output.splitlines())
    assert "%250A" in output
    assert all(len(line) <= 650 for line in output.splitlines())


@pytest.mark.parametrize("body", [b"not JSON PRIVATE", b"x" * 65537, [], None, {"error": {"message": []}}, {"error": "PRIVATE"}])
def test_bad_response_is_suppressed_and_fails(monkeypatch, capsys, body):
    result, output, calls = exercise(monkeypatch, capsys, [(400, body)] * 2)
    assert result == 1
    assert len(calls) == 2
    assert "error.type=unparseable" in output
    assert "PRIVATE" not in output


def test_transport_exception_never_prints_credential(monkeypatch, capsys):
    result, output, calls = exercise(monkeypatch, capsys, [(None, None), (200, {})])
    assert result == 1
    assert len(calls) == 2
    assert "transport error" in output
    assert "transport echoed" not in output
    assert FAKE_KEY not in output


@pytest.mark.parametrize("key", ["", FAKE_KEY + "\n", " " + FAKE_KEY, FAKE_KEY + "\t"])
def test_empty_or_whitespace_key_fails_without_requests(monkeypatch, capsys, key):
    result, output, calls = exercise(monkeypatch, capsys, [], key=key)
    assert result == 1
    assert calls == []
    assert FAKE_KEY not in output


@pytest.mark.parametrize("workspace", ["", "wrong-prefix", "wrkspc_", "wrkspc_bad\nheader", "wrkspc_bad header", "wrkspc_bad\\id"])
def test_invalid_workspace_fails_without_requests(monkeypatch, capsys, workspace):
    result, output, calls = exercise(monkeypatch, capsys, [], workspace=workspace)
    assert result == 1
    assert calls == []
    assert "workspace_id must be a wrkspc_ ID" in output
