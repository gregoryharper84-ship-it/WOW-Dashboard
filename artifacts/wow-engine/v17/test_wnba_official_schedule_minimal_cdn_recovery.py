import pytest

import wnba_prop_auto_hydration as wnba
from v17 import wnba_official_schedule_web_fallback as fallback


class FakeResponse:
    def __init__(self, *, payload=None, status_code=200, content=b""):
        self._payload = payload
        self.status_code = status_code
        self.content = content

    def json(self):
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


def _schedule_payload():
    return {"leagueSchedule": {"gameDates": []}}


def test_minimal_cdn_contract_stays_on_same_official_source_and_drops_browser_only_headers():
    headers = fallback._minimal_cdn_headers()
    assert headers["User-Agent"].startswith("Mozilla/5.0")
    assert headers["Accept"] == "application/json, text/plain, */*"
    assert "Host" not in headers
    assert "Origin" not in headers
    assert "Referer" not in headers
    assert "Accept-Encoding" not in headers


def test_browser_contract_non_json_recovers_from_same_official_cdn_before_ssr():
    calls = []

    def fetcher(url, **kwargs):
        calls.append((url, dict(kwargs.get("headers") or {})))
        assert url == wnba.WNBA_SCHEDULE_URL
        request_headers = dict(kwargs.get("headers") or {})
        if "Host" in request_headers:
            return FakeResponse(payload=ValueError("non-json browser-context body"))
        return FakeResponse(payload=_schedule_payload())

    payload = fallback.request_with_official_web_fallback(
        wnba.WNBA_SCHEDULE_URL,
        http_get=fetcher,
        headers=wnba._cdn_headers(),
    )

    assert payload == _schedule_payload()
    assert len(calls) == wnba.HTTP_ATTEMPTS + 1
    assert all(url == wnba.WNBA_SCHEDULE_URL for url, _headers in calls)
    assert all(headers.get("Host") == "cdn.wnba.com" for _url, headers in calls[:wnba.HTTP_ATTEMPTS])
    assert "Host" not in calls[-1][1]
    assert "Origin" not in calls[-1][1]


def test_all_official_transports_still_fail_closed_and_preserve_minimal_stage_receipt():
    def fetcher(url, **kwargs):
        if url == wnba.WNBA_SCHEDULE_URL:
            return FakeResponse(payload=ValueError("not json"))
        return FakeResponse(status_code=502, content=b"bad gateway")

    with pytest.raises(wnba.WNBAPropHydrationError) as excinfo:
        fallback.request_with_official_web_fallback(
            wnba.WNBA_SCHEDULE_URL,
            http_get=fetcher,
            headers=wnba._cdn_headers(),
        )

    assert excinfo.value.code == "WNBA_OFFICIAL_SOURCE_UNAVAILABLE"
    detail = excinfo.value.detail
    assert detail["primary_source"] == fallback.CDN_PROVIDER
    assert detail["fallback_source"] == fallback.WEB_PROVIDER
    assert len(detail["primary_errors"]) == wnba.HTTP_ATTEMPTS
    assert len(detail["primary_minimal_errors"]) == wnba.HTTP_ATTEMPTS
    assert len(detail["fallback_errors"]) == wnba.HTTP_ATTEMPTS
    assert fallback.CAN_EXECUTE is False
