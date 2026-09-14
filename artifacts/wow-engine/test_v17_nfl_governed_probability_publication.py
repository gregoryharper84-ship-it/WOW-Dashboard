from dataclasses import is_dataclass
from types import SimpleNamespace

import nfl_event_hydration_runtime as hydration
import nfl_event_model_v17 as model
import team_event_request_runtime as request_runtime
from v17 import team_event_request_runtime as v17_team_event_base
from v17.nfl_team_event_publication import _nfl_envelope, install_nfl_team_event_publication
from v17.v17_candidate_envelopes import V17TeamEventCandidateEnvelope


def test_nfl_default_hydration_includes_current_2026_without_changing_model_split():
    assert hydration.DEFAULT_SEASONS == (2021, 2022, 2023, 2024, 2025, 2026)
    assert model.TRAIN_SEASONS == (2021, 2022, 2023)
    assert model.CALIBRATION_SEASON == 2024
    assert model.VALIDATION_SEASON == 2025
    assert 2026 not in model.TRAIN_SEASONS
    assert model.CALIBRATION_SEASON != 2026
    assert model.VALIDATION_SEASON != 2026


def test_nfl_hydration_explicit_override_is_preserved():
    assert hydration.parse_seasons("2022, 2024,2024, 2025") == (2022, 2024, 2025)


def test_nfl_publication_patch_is_idempotent_and_non_nfl_delegates_unchanged():
    calls = []

    def original(req, *, event_api, canonical_hydration_required=False):
        calls.append((req.sport, req.league, event_api, canonical_hydration_required))
        return {"sentinel": "unchanged", "can_execute": False}

    module = SimpleNamespace(score_team_event_request=original)
    assert install_nfl_team_event_publication(module) is True
    once = module.score_team_event_request
    assert install_nfl_team_event_publication(module) is True
    assert module.score_team_event_request is once

    req = SimpleNamespace(sport="MLB", league="MLB")
    event_api = object()
    result = module.score_team_event_request(
        req,
        event_api=event_api,
        canonical_hydration_required=True,
    )
    assert result == {"sentinel": "unchanged", "can_execute": False}
    assert calls == [("MLB", "MLB", event_api, True)]


def test_nfl_publication_envelope_uses_real_frozen_dataclass_without_attribute_error():
    req = v17_team_event_base.TeamEventRequest(
        requester_host_identity="WOW_BETTING_ENGINE",
        research_run_id="test-nfl-envelope",
        requested_slate_date="2099-09-14",
        requested_timezone="America/Chicago",
        scan_stage="PREGAME",
        candidate_family="TEAM_EVENT",
        decision_intent="WINNER",
        event_key="NFL:provider-event-123",
        official_event_id="2099_01_DEN_KC",
        event_start_time_utc="2099-09-15T00:20:00+00:00",
        sport="NFL",
        league="NFL",
        market_family="OUTRIGHT_WINNER",
        settlement_basis="FULL_GAME_OUTRIGHT",
        home_team="Kansas City Chiefs",
        away_team="Denver Broncos",
        source_snapshot_id="nflverse-snapshot-1",
        latest_material_update_timestamp="2099-09-14T12:00:00+00:00",
        market_prior=None,
        sport_specific_evidence={},
    )

    envelope = _nfl_envelope(v17_team_event_base, req)

    assert isinstance(envelope, V17TeamEventCandidateEnvelope)
    assert is_dataclass(envelope)
    assert envelope.official_event_id == "2099_01_DEN_KC"
    assert envelope.official_event_id_source == "CANONICAL_NFLVERSE_LEDGER"
    assert envelope.official_event_status_source == "CANONICAL_NFLVERSE_LEDGER"
    assert envelope.home_starter_source == "NOT_APPLICABLE_NFL"
    assert envelope.away_starter_source == "NOT_APPLICABLE_NFL"
    assert envelope.injury_source == "NOT_USED_BY_NFL_FITTED_V1"
    assert envelope.weather_source == "NOT_USED_BY_NFL_FITTED_V1"
    assert envelope.bullpen_source == "NOT_APPLICABLE_NFL"
    assert envelope.source_snapshot_id == "nflverse-snapshot-1"


def _fake_app():
    class FakeApp:
        def __init__(self):
            self.router = SimpleNamespace(routes=[])

        def post(self, *args, **kwargs):
            def decorator(fn):
                return fn
            return decorator

    return FakeApp()


def _patch_runtime_installers(monkeypatch, calls):
    monkeypatch.setattr(
        request_runtime,
        "install_nfl_hydration_startup",
        lambda app, *, db_client_fn: calls.append("hydration"),
    )
    monkeypatch.setattr(
        request_runtime,
        "install_nfl_model_startup",
        lambda app, *, db_client_fn: calls.append("model"),
    )
    monkeypatch.setattr(
        request_runtime,
        "install_nfl_team_event_publication",
        lambda module: calls.append(("publication", module)) or True,
    )
    monkeypatch.setattr(request_runtime, "scout_route_auth_dependency", lambda dependency: dependency)


def test_team_event_runtime_installs_nfl_publication_only_when_v17_active(monkeypatch):
    calls = []
    _patch_runtime_installers(monkeypatch, calls)
    monkeypatch.setenv("WOW_V17_ACTIVE", "1")

    request_runtime.install_team_event_request_routes(
        _fake_app(),
        auth_dependency=object(),
        db_client_fn=lambda: object(),
        event_api=object(),
    )

    assert calls[0:2] == ["hydration", "model"]
    assert calls[2][0] == "publication"
    assert calls[2][1] is request_runtime.v17_team_event_base


def test_team_event_runtime_does_not_leak_nfl_publication_into_lower_layers(monkeypatch):
    calls = []
    _patch_runtime_installers(monkeypatch, calls)
    monkeypatch.delenv("WOW_V17_ACTIVE", raising=False)

    request_runtime.install_team_event_request_routes(
        _fake_app(),
        auth_dependency=object(),
        db_client_fn=lambda: object(),
        event_api=object(),
    )

    assert calls == ["hydration", "model"]


def test_governed_nfl_model_contract_never_enables_execution_or_market_blend():
    assert model.CAN_EXECUTE is False
    assert model.PROVIDER_IDENTITY == "WOW_NFL_EVENT_FITTED_MODEL_V1"
    assert model.MODEL_FAMILY == "NFL_OUTRIGHT_WIN_LOGREG_V1"
    assert model.CALIBRATION_METHOD == "PLATT_TIME_SPLIT_V1"
