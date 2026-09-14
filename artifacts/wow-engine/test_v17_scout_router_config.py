from v17 import nightly_multiscout_oidc as scout_oidc


def test_production_scout_uses_read_only_odds_router_by_default(monkeypatch):
    monkeypatch.delenv("WOW_ODDS_ROUTER_URL", raising=False)
    monkeypatch.delenv("WOW_SCOUT_ODDS_ROUTER_KILL_SWITCH", raising=False)
    monkeypatch.setattr(scout_oidc.scout, "PROXY_URL", "https://wow-odds-proxy.onrender.com")
    scout_oidc.configure_acquisition_router()
    assert scout_oidc.scout.PROXY_URL == scout_oidc.DEFAULT_ODDS_ROUTER_URL


def test_router_url_can_be_overridden_without_changing_governance(monkeypatch):
    monkeypatch.setenv("WOW_ODDS_ROUTER_URL", "https://router.example/")
    monkeypatch.delenv("WOW_SCOUT_ODDS_ROUTER_KILL_SWITCH", raising=False)
    monkeypatch.setattr(scout_oidc.scout, "PROXY_URL", "https://legacy.example")
    scout_oidc.configure_acquisition_router()
    assert scout_oidc.scout.PROXY_URL == "https://router.example"


def test_router_kill_switch_preserves_legacy_source(monkeypatch):
    monkeypatch.setenv("WOW_SCOUT_ODDS_ROUTER_KILL_SWITCH", "true")
    monkeypatch.setattr(scout_oidc.scout, "PROXY_URL", "https://legacy.example")
    scout_oidc.configure_acquisition_router()
    assert scout_oidc.scout.PROXY_URL == "https://legacy.example"


def test_research_market_evidence_is_on_by_default_but_stays_nonexecuting(monkeypatch):
    monkeypatch.delenv("WOW_MARKET_EVIDENCE_KILL_SWITCH", raising=False)
    scout_oidc.enable_research_market_evidence()
    assert scout_oidc.market_sources.ENABLED is True
    assert scout_oidc.market_sources.CAN_EXECUTE is False
