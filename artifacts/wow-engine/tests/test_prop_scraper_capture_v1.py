"""Offline regression suite: never invokes a live PropsMadness or other site."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.error import HTTPError
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v17.prop_scraper_capture_v1 import (
    APPROVED_LIVE_SCRAPE_SOURCES,
    PropScrapeHold,
    SourceScrapePermit,
    _capture_with_permit,
    _safe_url,
    scrape_approved_once,
)

URL = "https://example.org/research/player-props"
POLICY = SourceScrapePermit(
    "SYNTHETIC_FIXTURE", (URL,), "TEST_ONLY_NOT_A_VENDOR_GRANT",
    automated_fetch_authorized=True,
)
STAMP = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
HTML = b"<html><body>Offline permitted fixture</body></html>"
ROBOTS = b"User-agent: *\nAllow: /research/\n"


class Response:
    def __init__(self, url, data, *, mime, actual_url=None, status=200):
        self.url = actual_url or url
        self.data = data
        self.headers = {"Content-Type": mime}
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def geturl(self):
        return self.url

    def read(self, size):
        return self.data[:size]


def opener(*, robots=ROBOTS, html=HTML, redirect=None,
           bad_mime=False, error_code=None):
    seen = []

    def open_request(request, *, timeout):
        url = request.full_url
        assert timeout == 8
        assert request.get_header("User-agent")
        seen.append(url)
        if error_code and url == URL:
            raise HTTPError(url, error_code, "denied", {}, None)
        if url.endswith("/robots.txt"):
            return Response(url, robots, mime="text/plain")
        return Response(url, html, mime="application/json" if bad_mime else "text/html",
                        actual_url=redirect)

    return open_request, seen


def rows(_html=HTML):
    return {"rows": [{
        "participant_alias": "Fixture Player",
        "stat_type": "POINTS",
        "period": "FULL_GAME",
        "display_line": 20.5,
        "fixture_text": "TST vs OPP",
    }]}


def capture(*, policy=POLICY, url=URL, parse=rows, fetch=None):
    fetch = fetch or opener()[0]
    return _capture_with_permit(permit=policy, url=url, parse_html=parse,
                                captured_at=STAMP, opener=fetch)


def holds(code, function):
    with pytest.raises(PropScrapeHold) as exc:
        function()
    assert exc.value.code == code


def test_production_live_registry_starts_empty_and_no_vendor_fetch():
    assert APPROVED_LIVE_SCRAPE_SOURCES == {}
    holds("PROP_SCRAPE_SOURCE_NOT_APPROVED",
          lambda: scrape_approved_once("PROPSMADNESS_PUBLIC_EXPLORE", URL,
                                       parse_html=rows))


def test_synthetic_scrape_two_requests_and_research_only_receipt():
    fetch, seen = opener()
    result = capture(fetch=fetch)
    assert seen == ["https://example.org/robots.txt", URL]
    assert result["html_sha256"] == sha256(HTML).hexdigest()
    assert result["captured_at"] == "2026-11-16T12:00:00Z"
    assert result["row_count"] == 1
    assert result["rows"][0]["line_display"] == 20.5
    assert result["rows"][0]["canonical_event_id"] is None
    assert result["rows"][0]["model_probability"] is None
    assert result["request_count"] == 2
    for key in ("source_identity_verified", "pregame_snapshot_verified",
                "historical_training_authorized", "market_quote_verified",
                "probability_publishable", "production_write_authorized",
                "can_execute"):
        assert result[key] is False


def test_missing_rights_block_before_network():
    fetch, seen = opener()
    denied = SourceScrapePermit(
        "SYNTHETIC_FIXTURE", (URL,), "TEST_ONLY",
        automated_fetch_authorized=False,
    )
    holds("PROP_SCRAPE_RIGHTS_NOT_APPROVED",
          lambda: capture(policy=denied, fetch=fetch))
    assert seen == []


def test_host_path_and_credentials_must_match_approved_scope():
    fetch, seen = opener()
    for bad in ("https://example.org/research/other",
                "https://evil.org/research/player-props",
                "https://example.org@evil.org/research/player-props"):
        holds("PROP_SCRAPE_URL_NOT_APPROVED",
              lambda bad=bad: capture(url=bad, fetch=fetch))
    assert seen == []


@pytest.mark.parametrize("url", [
    "http://example.org/research/player-props",
    "https://127.0.0.1/private",
    "https://192.168.1.2/private",
    "https://localhost/private",
    "https://example.local/research",
    "https://user:password@example.org/test",
    "https://example.org:8080/test",
    "https://example.org/path#fragment",
])
def test_invalid_source_url_never_approves(url):
    holds("PROP_SCRAPE_URL_NOT_APPROVED", lambda: _safe_url(url))


def test_robots_disallow_prevents_html_request():
    fetch, seen = opener(robots=b"User-agent: *\nDisallow: /research/\n")
    holds("PROP_SCRAPE_ROBOTS_DISALLOW",
          lambda: capture(fetch=fetch))
    assert seen == ["https://example.org/robots.txt"]


def test_unknown_robots_policy_fails_closed():
    fetch, seen = opener(robots=b"Sorry, no policy here")
    holds("PROP_SCRAPE_ROBOTS_UNVERIFIED",
          lambda: capture(fetch=fetch))
    assert len(seen) == 1


def test_disallowed_redirect_and_source_mime_fail_closed():
    fetch, _ = opener(redirect="https://another.example.org/final")
    holds("PROP_SCRAPE_REDIRECT_BLOCKED", lambda: capture(fetch=fetch))
    fetch, _ = opener(bad_mime=True)
    holds("PROP_SCRAPE_CONTENT_TYPE_INVALID", lambda: capture(fetch=fetch))


@pytest.mark.parametrize("code,expected", [
    (403, "PROP_SCRAPE_ACCESS_DENIED"),
    (429, "PROP_SCRAPE_RATE_LIMITED"),
    (500, "PROP_SCRAPE_HTTP_REJECTED"),
])
def test_http_errors_are_typed_and_never_retried(code, expected):
    fetch, seen = opener(error_code=code)
    holds(expected, lambda: capture(fetch=fetch))
    assert seen == ["https://example.org/robots.txt", URL]


def test_oversized_html_is_held():
    fetch, _ = opener(html=b"x" * 1_000_001)
    holds("PROP_SCRAPE_PAYLOAD_INVALID", lambda: capture(fetch=fetch))


def test_authority_injection_and_duplicate_rows_rejected():
    elevated = rows()
    elevated["rows"][0]["model_probability"] = 0.98
    holds("PROP_SCRAPE_AUTHORITY_INJECTION",
          lambda: capture(parse=lambda _: elevated))
    holds("PROP_SCRAPE_DUPLICATE_ROW",
          lambda: capture(parse=lambda _: {"rows": rows()["rows"] * 2}))


def test_empty_scrape_does_not_claim_an_empty_slate():
    receipt = capture(parse=lambda _: {"rows": []})
    assert receipt["status"] == "RESEARCH_EMPTY_NOT_NO_SLATE"
    assert receipt["row_count"] == 0
    assert receipt["probability_publishable"] is False


def test_bad_parser_and_naive_time_rejected():
    holds("PROP_SCRAPE_PARSER_INVALID",
          lambda: capture(parse=lambda _: {"rows": "not-list"}))
    holds("PROP_SCRAPE_CAPTURE_TIME_INVALID",
          lambda: _capture_with_permit(
              permit=POLICY, url=URL, parse_html=rows,
              captured_at=datetime(2026, 11, 16), opener=opener()[0],
          ))


def test_parser_exception_fails_typed():
    def broken(_):
        raise RuntimeError("schema drift")
    holds("PROP_SCRAPE_PARSER_FAILED", lambda: capture(parse=broken))


def test_robots_unrelated_agent_does_not_authorize_html():
    fetch, seen = opener(robots=b"User-agent: Googlebot\nAllow: /research/\n")
    holds("PROP_SCRAPE_ROBOTS_UNVERIFIED", lambda: capture(fetch=fetch))
    assert seen == ["https://example.org/robots.txt"]


def test_robots_empty_group_and_invalid_directive_hold_before_html():
    for policy in (
        b"User-agent: *\n",
        b"User-agent: *\nAllow /research/\n",
        b"User-agent: *\nDisallow\n",
    ):
        fetch, seen = opener(robots=policy)
        holds("PROP_SCRAPE_ROBOTS_UNVERIFIED", lambda: capture(fetch=fetch))
        assert seen == ["https://example.org/robots.txt"]


def test_large_integer_does_not_raise_overflow_error():
    def bad(_):
        item = rows()["rows"][0]
        item["display_line"] = 10 ** 1000
        return {"rows": [item]}
    holds("PROP_SCRAPE_ROW_INVALID", lambda: capture(parse=bad))


def test_chunked_body_incomplete_read_is_typed_hold():
    from http.client import IncompleteRead

    def fetch(request, *, timeout):
        url = request.full_url
        if url.endswith("/robots.txt"):
            return Response(url, ROBOTS, mime="text/plain")
        class Broken(Response):
            def read(self, size):
                raise IncompleteRead(b"<html>", 12)
        return Broken(url, HTML, mime="text/html")

    holds("PROP_SCRAPE_SOURCE_UNREACHABLE", lambda: capture(fetch=fetch))


def test_truncated_robots_content_length_holds_before_html():
    seen = []

    def fetch(request, *, timeout):
        url = request.full_url
        seen.append(url)
        if url.endswith("/robots.txt"):
            out = Response(url, ROBOTS, mime="text/plain")
            out.headers["Content-Length"] = str(len(ROBOTS) + 15)
            return out
        raise AssertionError("HTML fetch should never occur")

    holds("PROP_SCRAPE_SOURCE_UNREACHABLE", lambda: capture(fetch=fetch))
    assert seen == ["https://example.org/robots.txt"]


def test_declared_oversize_content_length_returns_payload_hold():
    def fetch(request, *, timeout):
        url = request.full_url
        if url.endswith("/robots.txt"):
            return Response(url, ROBOTS, mime="text/plain")
        out = Response(url, HTML, mime="text/html")
        out.headers["Content-Length"] = str(1_000_002)
        return out

    holds("PROP_SCRAPE_PAYLOAD_INVALID", lambda: capture(fetch=fetch))


def test_robots_wildcard_disallow_is_not_silently_ignored():
    fetch, seen = opener(robots=b"User-agent: *\nDisallow: /research/*\n")
    holds("PROP_SCRAPE_ROBOTS_UNVERIFIED", lambda: capture(fetch=fetch))
    assert seen == ["https://example.org/robots.txt"]


def test_same_player_stat_line_different_fixture_remains_distinct():
    first = rows()["rows"][0]
    second = dict(first, fixture_text="TST vs OPP - game two")
    receipt = capture(parse=lambda _: {"rows": [first, second]})
    assert receipt["row_count"] == 2
    assert receipt["rows"][0]["fixture_text_unverified"] != receipt["rows"][1]["fixture_text_unverified"]


def test_second_wildcard_group_blocks_even_when_first_allows():
    policy = (b"User-agent: *\nAllow: /research/\n\n"
              b"User-agent: *\nDisallow: /research/\n")
    fetch, seen = opener(robots=policy)
    holds("PROP_SCRAPE_ROBOTS_UNVERIFIED", lambda: capture(fetch=fetch))
    assert seen == ["https://example.org/robots.txt"]


@pytest.mark.parametrize("policy", [
    b"User-agent: *\nAllow: /research/\nDisallow: /research/player-props\n",
    b"User-agent: *\nDisallow: /research/\nAllow: /research/player-props\n",
])
def test_conflicting_matching_robots_rules_hold_before_html(policy):
    fetch, seen = opener(robots=policy)
    holds("PROP_SCRAPE_ROBOTS_UNVERIFIED", lambda: capture(fetch=fetch))
    assert seen == ["https://example.org/robots.txt"]


def test_consistent_allow_prefixes_still_collect():
    policy = b"User-agent: *\nAllow: /research/\nAllow: /research/player-props\n"
    fetch, seen = opener(robots=policy)
    result = capture(fetch=fetch)
    assert result["row_count"] == 1
    assert seen == ["https://example.org/robots.txt", URL]


def test_consistent_disallow_prefixes_still_block():
    policy = b"User-agent: *\nDisallow: /research/\nDisallow: /research/player-props\n"
    fetch, seen = opener(robots=policy)
    holds("PROP_SCRAPE_ROBOTS_DISALLOW", lambda: capture(fetch=fetch))
    assert seen == ["https://example.org/robots.txt"]


def test_nonmatching_disallow_does_not_change_allowed_path():
    policy = b"User-agent: *\nAllow: /research/\nDisallow: /admin/\n"
    fetch, seen = opener(robots=policy)
    result = capture(fetch=fetch)
    assert result["row_count"] == 1
    assert seen == ["https://example.org/robots.txt", URL]
