"""Executes the real ``github_json`` helper from the Render production deploy workflow.

Incident: deploy run 38779941800 (2026-10-15 17:45Z) failed with HTTP 403 on
``GET /commits/{sha}/check-runs``. The workflow's ``checks: read`` permission
and helper were unchanged since 2026-10-12 and passed on three later runs; the
failing hour had 1,304 Actions runs (25-36 in hours where deploys passed), all
sharing the per-repository GITHUB_TOKEN budget. These tests pin the bounded,
header-proven rate-limit wait plus sanitized typed diagnostics for every other
HTTP error (supersedes the diagnostic-only #1786).
"""

from __future__ import annotations

import contextlib
import email.message
import io
import json
import textwrap
import urllib.error
import urllib.request
from pathlib import Path

import pytest

SECRET = "SECRET_TOKEN_VALUE"
WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "wow-v17-render-production-deploy.yml"
START_MARKER = "          # GITHUB_TOKEN reads share one per-repository hourly budget"
END_MARKER = "          def validate_release_provenance"


class FakeTime:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now
        self.sleeps: list[int] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: int) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeOs:
    def __init__(self, env: dict[str, str]) -> None:
        self.environ = env


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _load(monkeypatch, outcomes, *, env=None, clock=None):
    text = _workflow_text()
    start = text.index(START_MARKER)
    end = text.index(END_MARKER, start)
    clock = clock or FakeTime()
    namespace = {
        "json": json,
        "urllib": urllib,
        "os": FakeOs(env or {}),
        "time": clock,
        "repo": "owner/repo",
        "github_headers": {"Authorization": "Bearer " + SECRET},
    }
    exec(textwrap.dedent(text[start:end]), namespace)
    calls: list[str] = []
    queue = list(outcomes)

    def _urlopen(request, timeout=None):
        calls.append(request.full_url)
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return contextlib.nullcontext(io.BytesIO(json.dumps(outcome).encode("utf-8")))

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    return namespace, clock, calls


def _http_error(status: int, body: bytes, **headers: str) -> urllib.error.HTTPError:
    message = email.message.Message()
    for key, value in headers.items():
        message[key.replace("_", "-")] = value
    return urllib.error.HTTPError(
        f"https://api.github.com/repos/owner/repo/commits/abc/check-runs?token={SECRET}",
        status,
        "error",
        message,
        io.BytesIO(body),
    )


def _diagnostics(out: str) -> list[dict]:
    lines = [line for line in out.splitlines() if line.startswith("GITHUB_RELEASE_API_ERROR:")]
    return [json.loads(line.split(":", 1)[1]) for line in lines]


def test_access_denied_403_is_sanitized_reraised_and_never_waits(monkeypatch, capsys):
    error = _http_error(
        403,
        json.dumps({"message": "Resource not accessible by integration " + SECRET}).encode(),
        x_accepted_github_permissions="checks=read",
        x_ratelimit_remaining="4999",
    )
    ns, clock, calls = _load(monkeypatch, [error])

    with pytest.raises(urllib.error.HTTPError) as raised:
        ns["github_json"]("/commits/abc/check-runs?per_page=100")

    assert raised.value is error
    assert clock.sleeps == []
    assert len(calls) == 1
    out = capsys.readouterr().out
    assert SECRET not in out
    [diag] = _diagnostics(out)
    assert diag["endpoint"] == "/commits/abc/check-runs"
    assert diag["status"] == 403
    assert diag["message_category"] == "ACCESS_DENIED"
    assert diag["accepted_permissions"] == "checks=read"
    assert diag["rate_limit_remaining"] == "4999"
    assert diag["wait_seconds"] is None


def test_non_json_403_body_is_sanitized_and_reraised(monkeypatch, capsys):
    error = _http_error(403, f"<html>{SECRET}</html>".encode(), x_ratelimit_remaining="12")
    ns, clock, _ = _load(monkeypatch, [error])

    with pytest.raises(urllib.error.HTTPError):
        ns["github_json"]("/commits/abc/check-runs")

    out = capsys.readouterr().out
    assert SECRET not in out
    assert _diagnostics(out)[0]["message_category"] == "ACCESS_DENIED"
    assert clock.sleeps == []


@pytest.mark.parametrize("status", [404, 422, 500])
def test_other_http_errors_reraise_without_waiting(monkeypatch, capsys, status):
    error = _http_error(status, b'{"message":"nope"}')
    ns, clock, _ = _load(monkeypatch, [error])

    with pytest.raises(urllib.error.HTTPError) as raised:
        ns["github_json"]("/pulls/1")

    assert raised.value is error
    assert clock.sleeps == []
    assert _diagnostics(capsys.readouterr().out)[0]["message_category"] == "HTTP_ERROR"


def test_primary_rate_limit_waits_until_reset_then_returns_exact_payload(monkeypatch, capsys):
    clock = FakeTime()
    error = _http_error(
        403,
        b'{"message":"API rate limit exceeded for installation."}',
        x_ratelimit_remaining="0",
        x_ratelimit_reset=str(int(clock.now) + 120),
        x_ratelimit_resource="core",
    )
    payload = {"check_runs": [{"name": "WOW required-three regression"}]}
    ns, clock, calls = _load(monkeypatch, [error, payload], clock=clock)

    assert ns["github_json"]("/commits/abc/check-runs?per_page=100") == payload
    assert clock.sleeps == [125]
    assert len(calls) == 2
    assert ns["github_rate_limit"]["waited_seconds"] == 125
    diag = _diagnostics(capsys.readouterr().out)[0]
    assert diag["message_category"] == "RATE_LIMIT"
    assert diag["rate_limit_resource"] == "core"


def test_429_retry_after_is_honoured(monkeypatch):
    error = _http_error(429, b"{}", retry_after="30")
    ns, clock, _ = _load(monkeypatch, [error, {"ok": True}])

    assert ns["github_json"]("/commits/abc/pulls") == {"ok": True}
    assert clock.sleeps == [30]


def test_secondary_rate_limit_message_waits_one_minute(monkeypatch):
    error = _http_error(
        403,
        b'{"message":"You have exceeded a secondary rate limit. Please wait."}',
        x_ratelimit_remaining="4000",
    )
    ns, clock, _ = _load(monkeypatch, [error, []])

    assert ns["github_json"]("/commits/abc/pulls") == []
    assert clock.sleeps == [60]


def test_reset_beyond_budget_fails_fast_with_typed_error(monkeypatch, capsys):
    clock = FakeTime()
    error = _http_error(
        403,
        b"{}",
        x_ratelimit_remaining="0",
        x_ratelimit_reset=str(int(clock.now) + 3000),
    )
    ns, clock, calls = _load(monkeypatch, [error], clock=clock)

    with pytest.raises(RuntimeError) as raised:
        ns["github_json"]("/commits/abc/check-runs?per_page=100")

    assert str(raised.value) == (
        "RENDER_DEPLOY_GITHUB_RATE_LIMIT_EXHAUSTED:/commits/abc/check-runs:retry_after_seconds=3005"
    )
    assert raised.value.__cause__ is error
    assert clock.sleeps == []
    assert len(calls) == 1
    assert SECRET not in capsys.readouterr().out


def test_wait_budget_is_cumulative_across_calls(monkeypatch):
    first = _http_error(429, b"{}", retry_after="600")
    second = _http_error(429, b"{}", retry_after="600")
    ns, clock, _ = _load(monkeypatch, [first, {"a": 1}, second])

    assert ns["github_json"]("/pulls/1") == {"a": 1}
    with pytest.raises(RuntimeError, match="RENDER_DEPLOY_GITHUB_RATE_LIMIT_EXHAUSTED:/pulls/2"):
        ns["github_json"]("/pulls/2")
    assert clock.sleeps == [600]


def test_budget_is_operator_configurable(monkeypatch):
    error = _http_error(429, b"{}", retry_after="2400")
    ns, clock, _ = _load(
        monkeypatch, [error, {"ok": 1}], env={"WOW_GITHUB_RATE_LIMIT_MAX_WAIT_SECONDS": "3000"}
    )

    assert ns["github_json"]("/pulls/1") == {"ok": 1}
    assert clock.sleeps == [2400]


def test_reset_in_the_past_retries_promptly(monkeypatch):
    clock = FakeTime()
    error = _http_error(403, b"{}", x_ratelimit_remaining="0", x_ratelimit_reset=str(int(clock.now) - 50))
    ns, clock, _ = _load(monkeypatch, [error, {}], clock=clock)

    assert ns["github_json"]("/pulls/1") == {}
    assert clock.sleeps == [1]


def test_workflow_governance_and_token_scope_are_unchanged():
    text = _workflow_text()
    header = text[: text.index("jobs:")]
    assert "permissions:\n  contents: read\n  pull-requests: read\n  checks: read\n  deployments: write\n" in header
    assert "write" not in header.replace("deployments: write", "")
    assert "GH_API_TOKEN: ${{ github.token }}" in text
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text
    # Required-check polling deadline excludes time spent waiting out rate limits.
    assert 'if time.time() - github_rate_limit["waited_seconds"] >= check_deadline:' in text
    assert "RENDER_DEPLOY_REQUIRED_CHECK_TIMEOUT" in text
