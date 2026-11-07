"""Exercise read-only rate-limit recovery without changing admission policy."""
from io import BytesIO

import pytest
from urllib.error import HTTPError
from v17 import governance_dispatcher_github as subject


class Response:
    status = 200
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, limit):
        return b'{"verified":true}'


def exercise(monkeypatch, *, status=403, headers=None, method="GET", repeat=False):
    calls, sleeps = [], []
    monkeypatch.setattr(subject.time, "time", lambda: 1000.0)
    monkeypatch.setattr(subject.time, "sleep", sleeps.append)
    def request(req, timeout):
        calls.append(req.get_method())
        if len(calls) == 1 or repeat:
            raise HTTPError(req.full_url, status, "denied", headers or {}, BytesIO(b""))
        return Response()
    monkeypatch.setattr(subject, "urlopen", request)
    result = subject.GitHub("test-only-token").request(method, "/repos/example/repo")
    return result, calls, sleeps


@pytest.mark.parametrize("headers,delay", [
    ({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1001"}, 1),
    ({"Retry-After": "2"}, 2),
])
def test_rate_limited_get_recovers_with_positive_bounded_header_evidence(monkeypatch, headers, delay):
    result, calls, sleeps = exercise(monkeypatch, headers=headers)
    assert result == (200, {"verified": True})
    assert calls == ["GET", "GET"]
    assert sleeps == [delay]


@pytest.mark.parametrize("headers", [
    {}, {"X-RateLimit-Remaining": "5"},
    {"X-RateLimit-Remaining": "0"},
    {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "not-a-time"},
    {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "2000"},
    {"Retry-After": "200"}, {"Retry-After": "invalid"},
])
def test_ambiguous_permission_or_long_rate_limit_stays_failed_closed(monkeypatch, headers):
    result, calls, sleeps = exercise(monkeypatch, headers=headers)
    assert result == (403, None)
    assert calls == ["GET"]
    assert sleeps == []


def test_rate_limit_never_retries_a_write(monkeypatch):
    result, calls, sleeps = exercise(monkeypatch, headers={"Retry-After": "1"}, method="POST")
    assert result == (403, None)
    assert calls == ["POST"]
    assert sleeps == []


def test_repeated_rate_limit_preserves_failure_and_existing_attempt_ceiling(monkeypatch):
    result, calls, sleeps = exercise(monkeypatch, headers={"Retry-After": "1"}, repeat=True)
    assert result == (403, None)
    assert len(calls) == subject.MAX_RETRIES
    assert sleeps == [1] * (subject.MAX_RETRIES - 1)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_existing_transient_read_recovery_is_preserved(monkeypatch, status):
    result, calls, sleeps = exercise(monkeypatch, status=status, headers={"Retry-After": "1"})
    assert result == (200, {"verified": True})
    assert len(calls) == 2
    assert sleeps == [1]


@pytest.mark.parametrize('status', [403, 429])
@pytest.mark.parametrize('retry_after,reset,expected_delay', [('1','1004',4),('4','1001',4)])
def test_combined_hints_honor_the_longer_wait(monkeypatch,status,retry_after,reset,expected_delay):
    result,calls,sleeps=exercise(monkeypatch,status=status,headers={
        'Retry-After':retry_after,'X-RateLimit-Remaining':'0','X-RateLimit-Reset':reset})
    assert result == (200,{'verified':True})
    assert calls == ['GET','GET']
    assert sleeps == [expected_delay]


@pytest.mark.parametrize('status', [403, 429])
@pytest.mark.parametrize('retry_after,reset', [('1','1010'),('10','1001')])
def test_either_over_budget_hint_prevents_early_retry(monkeypatch,status,retry_after,reset):
    result,calls,sleeps=exercise(monkeypatch,status=status,headers={
        'Retry-After':retry_after,'X-RateLimit-Remaining':'0','X-RateLimit-Reset':reset})
    assert result == (status,None)
    assert calls == ['GET']
    assert sleeps == []


@pytest.mark.parametrize('headers', [
    {'Retry-After':'200'}, {'Retry-After':'invalid'},
    {'X-RateLimit-Remaining':'0','X-RateLimit-Reset':'invalid'},
])
def test_long_or_malformed_429_hints_stay_failed_closed(monkeypatch,headers):
    result,calls,sleeps=exercise(monkeypatch,status=429,headers=headers)
    assert result == (429,None)
    assert calls == ['GET']
    assert sleeps == []
