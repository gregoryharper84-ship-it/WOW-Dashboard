from types import SimpleNamespace

from v17 import market_evidence_native_live as live
from v17 import market_evidence_sources as sources
from v17 import runtime_acceptance_probe as probe
from v17 import rundown_credential_diagnostic as credential
from v17 import rundown_provider_health as provider_health
from v17.sep15_runtime_contract_repairs import sanitize_model_market_prior


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def gt(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        return _Result(self.rows)


class _Client:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "wow_mlb_forward_shadow_events"
        return _Query(self.rows)


def test_rundown_acceptance_exposes_counts_not_odds_or_payload(monkeypatch):
    event = {
        "id": "rundown-event",
        "bookmakers": [{
            "key": "book-a",
            "markets": [{
                "key": "h2h",
                "outcomes": [
                    {"name": "Home", "price": -135},
                    {"name": "Away", "price": 115},
                ],
            }],
        }],
    }
    fake = sources.MarketEvidenceResult(
        True,
        "RUNDOWN",
        "events",
        data=[event],
        status=200,
        code="MARKET_EVIDENCE_NORMALISED",
        request_audit={"payload_classification": "EVENT_LIST_SNAPSHOT"},
    )
    monkeypatch.setattr(live, "get_sport_date_odds_snapshot", lambda *_args, **_kwargs: fake)
    monkeypatch.setattr(
        credential,
        "rundown_credential_status",
        lambda: {"configured": True, "secret_value_exposed": False},
    )

    out = probe._rundown_acceptance()
    assert out["status"] == "PASS"
    assert out["code"] == "RUNDOWN_LIVE_BOARD_VERIFIED"
    assert out["events_returned"] == 1
    assert out["moneyline_events_returned"] == 1
    assert out["secret_value_exposed"] is False
    assert out["prediction_authority"] is False
    assert out["can_execute"] is False
    assert "provider_health_status" not in out
    assert "data" not in out
    assert "bookmakers" not in out
    assert "odds" not in out
    assert "prices" not in out


def test_rundown_403_catalog_rejection_is_localized_as_auth_failure(monkeypatch):
    fake = sources.MarketEvidenceResult(
        False,
        "RUNDOWN",
        "events",
        status=403,
        code="RUNDOWN_HTTP_403",
    )
    monkeypatch.setattr(live, "get_sport_date_odds_snapshot", lambda *_args, **_kwargs: fake)
    monkeypatch.setattr(
        credential,
        "rundown_credential_status",
        lambda: {"configured": True, "secret_value_exposed": False},
    )
    monkeypatch.setattr(
        provider_health,
        "probe_rundown_provider_health",
        lambda **_kwargs: {
            "status": "BLOCKED",
            "catalog_access": {
                "market_acquisition_status": "AUTH_FAILED",
                "provider_code": "RUNDOWN_HTTP_403",
                "http_status": 403,
                "auth_ok": False,
            },
            "event_access": {
                "market_acquisition_status": "NOT_ATTEMPTED",
                "provider_code": None,
                "http_status": None,
                "auth_ok": None,
            },
            "secret": "must-never-be-forwarded",
        },
    )

    out = probe._rundown_acceptance()
    assert out["status"] == "FAIL"
    assert out["code"] == "RUNDOWN_HTTP_403"
    assert out["provider_health_status"] == "BLOCKED"
    assert out["catalog_access_status"] == "AUTH_FAILED"
    assert out["catalog_provider_code"] == "RUNDOWN_HTTP_403"
    assert out["catalog_http_status"] == 403
    assert out["catalog_auth_ok"] is False
    assert out["event_access_status"] == "NOT_ATTEMPTED"
    assert out["event_http_status"] is None
    assert "secret" not in out
    assert out["secret_value_exposed"] is False
    assert out["can_execute"] is False


def test_rundown_403_after_catalog_pass_localizes_event_entitlement_boundary(monkeypatch):
    fake = sources.MarketEvidenceResult(
        False,
        "RUNDOWN",
        "events",
        status=403,
        code="RUNDOWN_HTTP_403",
    )
    monkeypatch.setattr(live, "get_sport_date_odds_snapshot", lambda *_args, **_kwargs: fake)
    monkeypatch.setattr(
        credential,
        "rundown_credential_status",
        lambda: {"configured": True, "secret_value_exposed": False},
    )
    monkeypatch.setattr(
        provider_health,
        "probe_rundown_provider_health",
        lambda **_kwargs: {
            "status": "BLOCKED",
            "catalog_access": {
                "market_acquisition_status": "PASS",
                "provider_code": "MARKET_EVIDENCE_FETCH_OK",
                "http_status": 200,
                "auth_ok": True,
            },
            "event_access": {
                "market_acquisition_status": "AUTH_FAILED",
                "provider_code": "RUNDOWN_HTTP_403",
                "http_status": 403,
                "auth_ok": False,
            },
        },
    )

    out = probe._rundown_acceptance()
    assert out["status"] == "FAIL"
    assert out["catalog_access_status"] == "PASS"
    assert out["catalog_http_status"] == 200
    assert out["catalog_auth_ok"] is True
    assert out["event_access_status"] == "AUTH_FAILED"
    assert out["event_provider_code"] == "RUNDOWN_HTTP_403"
    assert out["event_http_status"] == 403
    assert out["event_auth_ok"] is False
    assert out["secret_value_exposed"] is False
    assert out["prediction_authority"] is False
    assert out["can_execute"] is False


def test_market_prior_acceptance_verifies_ingress_without_exposing_values():
    runtime = SimpleNamespace(_model_market_prior=sanitize_model_market_prior)
    out = probe._market_prior_acceptance(runtime)
    assert out["status"] == "PASS"
    assert out["code"] == "MARKET_PRIOR_INGRESS_INVARIANT_VERIFIED"
    assert out["valid_optional_context_accepted"] is True
    assert out["envelope_only_fields_forwarded"] is False
    assert out["malformed_optional_context_rejected_before_scorer"] is True
    assert out["sporting_model_eligibility_mutation_allowed"] is False
    assert out["can_execute"] is False
    assert "home_probability" not in out
    assert "away_probability" not in out


def test_projected_lineup_acceptance_requires_immutable_runtime_receipt():
    rows = [{
        "official_event_id": "824466",
        "official_date": "2026-09-15",
        "event_start_time": "2099-09-15T22:40:00+00:00",
        "home_team": "Cincinnati Reds",
        "away_team": "Los Angeles Dodgers",
        "venue_name": "Great American Ball Park",
        "home_probable_pitcher": "Rhett Lowder",
        "away_probable_pitcher": "Yoshinobu Yamamoto",
        "snapshot_id": "snap-1",
        "snapshot_timestamp": "2026-09-15T05:02:00+00:00",
        "feature_hydration_status": "PASS",
        "model_score_status": "SHADOW_SCORED_LINEUP_PENDING",
    }]

    class Request:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class EventApi:
        ScoreEventRequest = Request

        def get_client(self):
            return _Client(rows)

        def score_event(self, _req):
            return {
                "code": "REAL_FITTED_MODEL_PATH_PROVEN",
                "sport_model_invoked": True,
                "sport_model_invocation_source": "IMMUTABLE_PROJECTED_SCORE_SNAPSHOT",
                "probability_fields_withheld": True,
                "rank_eligible": False,
                "can_execute": False,
                # This value proves the public acceptance response does not echo
                # any fitted probability even if an upstream receipt grows one.
                "calibrated_home_probability": 0.54321,
            }

    out = probe._projected_lineup_acceptance(EventApi())
    assert out["status"] == "PASS"
    assert out["code"] == "MLB_PROJECTED_LINEUP_RUNTIME_VERIFIED"
    assert out["eligible_event_found"] is True
    assert out["receipt_code"] == "REAL_FITTED_MODEL_PATH_PROVEN"
    assert out["sport_model_invoked"] is True
    assert out["invocation_source"] == "IMMUTABLE_PROJECTED_SCORE_SNAPSHOT"
    assert out["probability_fields_withheld_at_bridge"] is True
    assert out["rank_eligible"] is False
    assert out["can_execute"] is False
    assert "calibrated_home_probability" not in out


def test_overall_probe_preserves_terminal_and_execution_invariants(monkeypatch):
    monkeypatch.setattr(probe, "_rundown_acceptance", lambda: probe._base_result("PASS", "RUNDOWN_LIVE_BOARD_VERIFIED"))
    monkeypatch.setattr(probe, "_market_prior_acceptance", lambda _runtime: probe._base_result("PASS", "MARKET_PRIOR_INGRESS_INVARIANT_VERIFIED"))
    monkeypatch.setattr(probe, "_projected_lineup_acceptance", lambda _api: probe._base_result("PASS", "MLB_PROJECTED_LINEUP_RUNTIME_VERIFIED"))

    result = probe.run_runtime_acceptance_probe(event_api=object(), team_runtime=object())
    assert result["status"] == "PASS"
    assert result["runtime_generation"] == "V17_ACTIVE"
    assert result["global_terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert result["probability_values_exposed"] is False
    assert result["secret_value_exposed"] is False
    assert result["can_execute"] is False
