import httpx

from v17.research_source_adapters import adapter_health, capability_for, sportsdataio_fetch


def test_provider_is_disabled_without_credential(monkeypatch):
    monkeypatch.delenv("SPORTSDATAIO_API_KEY", raising=False)
    monkeypatch.delenv("WOW_SPORTSDATAIO_API_KEY", raising=False)
    result = sportsdataio_fetch("americanfootball_nfl", "injuries")
    assert result.ok is False
    assert result.code == "SOURCE_CREDENTIAL_UNCONFIGURED"
    assert result.prediction_authority is False
    assert result.can_execute is False


def test_known_provider_gap_is_explicit_not_guessed(monkeypatch):
    monkeypatch.setenv("SPORTSDATAIO_API_KEY", "test")
    result = sportsdataio_fetch("americanfootball_ncaaf", "depth_charts")
    assert result.ok is False
    assert result.code == "SOURCE_CAPABILITY_UNSUPPORTED"
    cap = capability_for("americanfootball_ncaaf", "depth_charts")
    assert cap is not None
    assert cap.supported is False
    assert "does not supply" in (cap.limitation or "")


def test_wnba_pregame_lineup_gap_is_explicit(monkeypatch):
    monkeypatch.setenv("SPORTSDATAIO_API_KEY", "test")
    result = sportsdataio_fetch("basketball_wnba", "starting_lineups")
    assert result.ok is False
    assert result.code == "SOURCE_CAPABILITY_UNSUPPORTED"


def test_configurable_capability_fails_closed_until_path_configured(monkeypatch):
    monkeypatch.setenv("SPORTSDATAIO_API_KEY", "test")
    monkeypatch.delenv("WOW_SPORTSDATAIO_BASKETBALL_NBA_DEPTH_CHARTS_PATH", raising=False)
    result = sportsdataio_fetch("basketball_nba", "depth_charts")
    assert result.ok is False
    assert result.code == "SOURCE_ENDPOINT_UNCONFIGURED"


def test_verified_endpoint_uses_header_auth_and_returns_evidence_only(monkeypatch):
    monkeypatch.setenv("SPORTSDATAIO_API_KEY", "secret-test")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["key"] = request.headers.get("Ocp-Apim-Subscription-Key")
        return httpx.Response(200, json=[{"PlayerID": 1, "InjuryStatus": "Questionable"}])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = sportsdataio_fetch("americanfootball_nfl", "injuries", client=client)
    assert result.ok is True
    assert captured["url"].endswith("/v3/nfl/projections/json/InjuredPlayers")
    assert captured["key"] == "secret-test"
    assert result.data[0]["InjuryStatus"] == "Questionable"
    assert result.prediction_authority is False
    assert result.can_execute is False


def test_mlb_lineup_path_requires_date_and_never_leaks_key(monkeypatch):
    monkeypatch.setenv("SPORTSDATAIO_API_KEY", "secret-test")
    missing = sportsdataio_fetch("baseball_mlb", "starting_lineups")
    assert missing.ok is False
    assert missing.code == "SOURCE_PATH_PARAMETER_MISSING"
    assert "secret-test" not in str(missing.to_dict())


def test_health_reports_configuration_without_secret_value(monkeypatch):
    monkeypatch.setenv("SPORTSDATAIO_API_KEY", "secret-test")
    health = adapter_health()
    assert health["credential_configured"] is True
    assert health["prediction_authority"] is False
    assert health["can_execute"] is False
    assert "secret-test" not in str(health)
