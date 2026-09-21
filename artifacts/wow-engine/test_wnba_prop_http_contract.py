import logging

import pytest

import wnba_prop_auto_hydration as w


def test_stats_headers_match_browser_like_official_contract():
    headers = w._stats_headers()
    assert headers["Host"] == "stats.wnba.com"
    assert headers["Accept"] == "application/json, text/plain, */*"
    assert headers["Accept-Encoding"] == "gzip, deflate, br"
    assert headers["Connection"] == "keep-alive"
    assert headers["Origin"] == "https://stats.wnba.com"
    assert headers["Referer"] == "https://www.wnba.com/"
    assert headers["Pragma"] == "no-cache"
    assert headers["Cache-Control"] == "no-cache"
    assert headers["x-nba-stats-origin"] == "stats"
    assert headers["x-nba-stats-token"] == "true"


def test_official_source_failure_remains_typed_and_logs_safe_endpoint(caplog):
    def failing_get(url, **kwargs):
        raise TimeoutError("upstream timeout")

    with caplog.at_level(logging.WARNING, logger="wow.wnba_prop_auto_hydration"):
        with pytest.raises(w.WNBAPropHydrationError) as exc:
            w._request(
                "https://stats.wnba.com/stats/commonteamroster",
                http_get=failing_get,
                headers=w._stats_headers(),
            )

    assert exc.value.code == "WNBA_OFFICIAL_SOURCE_UNAVAILABLE"
    assert exc.value.detail["attempts"] == w.HTTP_ATTEMPTS
    assert "stats.wnba.com" in caplog.text
    assert "/stats/commonteamroster" in caplog.text
    assert "can_execute=false" in caplog.text
