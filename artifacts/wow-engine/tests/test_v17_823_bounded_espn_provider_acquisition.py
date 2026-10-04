import json

import pytest

from v17 import full_board_runtime as runtime
from v17 import scout_secondary_source as secondary


def _event(index: int) -> dict:
    return {
        "id": str(index),
        "date": "2026-10-04T12:00:00Z",
        "status": {"type": {"name": "STATUS_SCHEDULED"}},
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"displayName": f"Home {index}"}},
                {"homeAway": "away", "team": {"displayName": f"Away {index}"}},
            ],
            "unused_competition_blob": "x" * 2048,
        }],
        "unused_event_blob": "y" * 4096,
    }


def test_scoreboard_clamps_limit_partitions_cache_and_surfaces_truncation(monkeypatch):
    seen = []

    def fake_http_json(url, params=None):
        seen.append(int(params["limit"]))
        return secondary.SecondaryResult(True, {"events": [_event(1)]}, 200)

    monkeypatch.setattr(secondary, "_http_json", fake_http_json)
    secondary._SCOREBOARD_CACHE.clear()
    params = {"commenceTimeFrom": "2026-10-04", "commenceTimeTo": "2026-10-04"}

    one = secondary._scoreboard("baseball_mlb", params, provider_limit=0)
    two = secondary._scoreboard("baseball_mlb", params, provider_limit=2)
    cached_one = secondary._scoreboard("baseball_mlb", params, provider_limit=0)
    fallback = secondary._scoreboard("baseball_mlb", params, provider_limit="bad")

    assert seen == [1, 2, 1000]
    assert one.data["_wow_provider_limit"] == 1
    assert one.data["_wow_provider_truncated"] is True
    assert two.data["_wow_provider_limit"] == 2
    assert two.data["_wow_provider_truncated"] is False
    assert cached_one.data == one.data
    assert fallback.data["_wow_provider_limit"] == 1000
    assert fallback.data["_wow_provider_truncated"] is False


@pytest.mark.parametrize(
    "sport_key",
    ["baseball_mlb", "basketball_wnba", "icehockey_nhl"],
)
def test_compact_fixture_bounds_provider_and_never_hides_truncation(monkeypatch, sport_key):
    raw = {
        "events": [_event(i) for i in range(250)],
        "_wow_provider_limit": 250,
        "_wow_provider_result_count": 250,
        "_wow_provider_truncated": True,
    }
    seen = {}

    def fake_scoreboard(requested_sport, params=None, *, provider_limit=1000):
        seen["sport"] = requested_sport
        seen["limit"] = provider_limit
        return secondary.SecondaryResult(True, raw, 200)

    monkeypatch.setattr(secondary, "_scoreboard", fake_scoreboard)
    result = runtime.compact_espn_scoreboard(
        sport_key=sport_key,
        date="2026-10-04",
        page=1,
        page_size=100,
    )

    assert seen == {"sport": sport_key, "limit": 250}
    assert result["status"] == "DISCOVERY_INCOMPLETE_PROVIDER_LIMIT"
    assert result["provider_code"] == "ESPN_SCOREBOARD_PROVIDER_LIMIT_REAHNED"
    assert result["provider_truncated"] is True
    assert result["coverage_complete"] is False
    assert result["prediction_authority"] is False
    assert result["exact_line_authority"] is False
    assert result["can_execute"] is False
    assert len(json.dumps(result)) < len(json.dumps(raw)) / 10
