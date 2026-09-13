from types import SimpleNamespace

from v17 import nightly_multiscout as scout
from v17 import nightly_multiscout_oidc as scout_oidc
from v17 import multiscout_auto_advance_oidc as advance_oidc


def test_partial_source_handoff_is_dispatchable_without_erasing_source_status():
    handoff = {
        "run_id": "run-partial",
        "research_run_id": "run-partial",
        "status": "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS",
        "model_handoff_ready": True,
        "source_blockers": [{"reason_code": "ODDS_API_FEATURED_ODDS_FALLBACK_ERROR"}],
        "governance": {"can_execute": False},
        "model_handoff": {
            "prop_candidates": [],
            "team_event_candidates": [{
                "official_event_id": "event-1",
                "sport_key": "baseball_mlb",
                "commence_time": "2026-09-13T20:00:00Z",
                "home_team": "Home",
                "away_team": "Away",
                "market_evidence": [],
                "market_evidence_status": "SOURCE_BLOCKED",
            }],
        },
    }

    dispatchable = advance_oidc._dispatchable_handoff(handoff)

    assert dispatchable is not handoff
    assert dispatchable["status"] == "DISCOVERY_COMPLETE"
    assert dispatchable["source_acquisition_status"] == "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS"
    assert handoff["status"] == "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS"
    assert dispatchable["model_handoff"]["team_event_candidates"][0]["market_evidence_status"] == "SOURCE_BLOCKED"


def test_nonready_partial_source_handoff_remains_fail_closed():
    handoff = {
        "status": "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS",
        "model_handoff_ready": False,
    }
    assert advance_oidc._dispatchable_handoff(handoff) is handoff


def test_secondary_failure_diagnostics_are_preserved(monkeypatch):
    monkeypatch.delenv("WOW_ODDS_PROXY_ACTION_KEY", raising=False)
    monkeypatch.delenv("WOW_GITHUB_OIDC_TOKEN", raising=False)

    def primary(_path, _params=None):
        return scout.FetchResult(
            False,
            status=401,
            code="ODDS_API_FEATURED_ODDS_FALLBACK_ERROR",
        )

    monkeypatch.setattr(scout_oidc.scout, "proxy_get", primary)
    monkeypatch.setattr(scout_oidc, "mint_github_actions_oidc", lambda: "fresh-oidc")
    monkeypatch.setattr(
        scout_oidc,
        "secondary_for_request",
        lambda *args, **kwargs: SimpleNamespace(
            ok=False,
            data=None,
            status=404,
            code="SECONDARY_SOURCE_H2H_UNAVAILABLE",
        ),
    )

    scout_oidc.install_refreshable_oidc_proxy_auth()
    result = scout_oidc.scout.proxy_get(
        "/odds-api/v4/sports/baseball_mlb/events/event-1/markets",
        {"regions": "us", "dateFormat": "iso"},
    )

    assert result.ok is False
    assert result.code == "ODDS_API_FEATURED_ODDS_FALLBACK_ERROR"
    assert result.status == 401
    assert result.data["secondary_attempted"] is True
    assert result.data["secondary_status"] == "FAILED"
    assert result.data["secondary_reason_code"] == "SECONDARY_SOURCE_H2H_UNAVAILABLE"
    assert result.data["secondary_http_status"] == 404
    assert result.data["primary_reason_code"] == "ODDS_API_FEATURED_ODDS_FALLBACK_ERROR"
    assert result.data["primary_http_status"] == 401
