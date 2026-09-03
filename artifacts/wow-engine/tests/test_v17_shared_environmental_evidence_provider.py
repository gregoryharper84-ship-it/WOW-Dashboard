from pathlib import Path


SQL_PATH = (
    Path(__file__).resolve().parents[1]
    / "v17"
    / "sql"
    / "20260903_v17_shared_environmental_evidence_provider.sql"
)
SQL = SQL_PATH.read_text()


def test_shared_environment_provider_uses_official_mlb_feed_and_canonical_weather_kind():
    assert "wow_v17_hydrate_shared_environmental_evidence" in SQL
    assert "https://statsapi.mlb.com/api/v1.1/game/%s/feed/live" in SQL
    assert "'WEATHER_STATUS'" in SQL
    assert "MLB_STATS_API_OFFICIAL_LIVE_FEED" in SQL
    assert "source_grade,evidence_status" in SQL
    assert "'OFFICIAL','RETRIEVED'" in SQL


def test_shared_environment_provider_is_context_only_for_uncertified_weather_features():
    assert "WOW_V17_SHARED_ENVIRONMENTAL_EVIDENCE_V1" in SQL
    assert "CONTEXT_ONLY_UNLESS_CERTIFIED_FEATURE_PRESENT" in SQL
    assert "'probability_adjustment_applied',false" in SQL
    assert "probability_adjustment_applied',true" not in SQL


def test_shared_environment_provider_remains_fail_closed_and_non_executable():
    assert "EVENT_NOT_PREGAME" in SQL
    assert "OFFICIAL_WEATHER_UNAVAILABLE" in SQL
    assert "ENVIRONMENT_SOURCE_HTTP_ERROR" in SQL
    assert "'can_execute',false" in SQL
    assert "can_execute=true" not in SQL
    assert "'probability_publishable',true" not in SQL
