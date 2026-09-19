import logging

from v17.rundown_credential_diagnostic import (
    log_rundown_credential_status,
    rundown_credential_status,
)


# Active Product V2 runtime precedence. This must match
# install_rundown_v2_auth_repair, independent of test/import order.
ALIASES = ("THERUNDOWN_API_KEY", "RUNDOWN_API_KEY", "WOW_RUNDOWN_API_KEY")


def _clear(monkeypatch):
    for alias in ALIASES:
        monkeypatch.delenv(alias, raising=False)


def test_reports_unconfigured_without_secret_fields(monkeypatch):
    _clear(monkeypatch)
    status = rundown_credential_status()
    assert status["configured"] is False
    assert status["status"] == "UNCONFIGURED"
    assert status["selected_alias"] is None
    assert status["aliases_checked"] == list(ALIASES)
    assert status["auth_style"] == "header"
    assert status["auth_name"] == "X-TheRundown-Key"
    assert status["secret_value_exposed"] is False
    assert status["can_execute"] is False


def test_reports_selected_alias_by_active_v2_precedence(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("WOW_RUNDOWN_API_KEY", "lower-precedence-secret")
    monkeypatch.setenv("THERUNDOWN_API_KEY", "higher-precedence-secret")
    status = rundown_credential_status()
    assert status["configured"] is True
    assert status["status"] == "CONFIGURED"
    assert status["selected_alias"] == "THERUNDOWN_API_KEY"
    assert "lower-precedence-secret" not in repr(status)
    assert "higher-precedence-secret" not in repr(status)


def test_first_alias_wins_when_multiple_are_configured(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("THERUNDOWN_API_KEY", "first-secret")
    monkeypatch.setenv("RUNDOWN_API_KEY", "second-secret")
    status = rundown_credential_status()
    assert status["selected_alias"] == "THERUNDOWN_API_KEY"
    assert "first-secret" not in repr(status)
    assert "second-secret" not in repr(status)


def test_log_line_never_contains_secret_value(monkeypatch, caplog):
    _clear(monkeypatch)
    secret = "rundown-secret-that-must-never-leak"
    monkeypatch.setenv("THERUNDOWN_API_KEY", secret)
    monkeypatch.setenv("WOW_MARKET_EVIDENCE_ENABLED", "true")
    monkeypatch.setenv("WOW_LLP_RUNDOWN_MARKET_ENABLED", "true")

    logger = logging.getLogger("test.rundown.credential")
    with caplog.at_level(logging.INFO, logger=logger.name):
        status = log_rundown_credential_status(logger)

    text = caplog.text
    assert "WOW_RUNDOWN_CREDENTIAL" in text
    assert "status=CONFIGURED" in text
    assert "selected_alias=THERUNDOWN_API_KEY" in text
    assert "auth_style=header" in text
    assert "auth_name=X-TheRundown-Key" in text
    assert "market_evidence_enabled=true" in text
    assert "llp_rundown_bridge_enabled=true" in text
    assert "secret_value_exposed=false" in text
    assert secret not in text
    assert status["can_execute"] is False


def test_diagnostic_does_not_call_provider(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("RUNDOWN_API_KEY", "configured-secret")

    def explode(*args, **kwargs):
        raise AssertionError("diagnostic must not make outbound provider calls")

    from v17 import market_evidence_sources as sources

    monkeypatch.setattr(sources, "rundown_market_evidence", explode)
    status = rundown_credential_status()
    assert status["configured"] is True
