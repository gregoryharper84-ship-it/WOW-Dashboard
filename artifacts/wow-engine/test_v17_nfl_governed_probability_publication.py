from types import SimpleNamespace

import nfl_event_hydration_runtime as hydration
import nfl_event_model_v17 as model
import team_event_request_runtime as request_runtime
from v17.nfl_team_event_publication import install_nfl_team_event_publication


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


def test_team_event_runtime_installs_nfl_hydration_model_and_publication_hooks(monkeypatch):
    calls = []

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

    class FakeApp:
        def __init__(self):
            self.router = SimpleNamespace(routes=[])

        def post(self, *args, **kwargs):
            def decorator(fn):
                return fn
            return decorator

    app = FakeApp()
    request_runtime.install_team_event_request_routes(
        app,
        auth_dependency=object(),
        db_client_fn=lambda: object(),
        event_api=object(),
    )

    assert calls[0:2] == ["hydration", "model"]
    assert calls[2][0] == "publication"
    assert calls[2][1] is request_runtime.v17_team_event_base


def test_governed_nfl_model_contract_never_enables_execution_or_market_blend():
    assert model.CAN_EXECUTE is False
    assert model.PROVIDER_IDENTITY == "WOW_NFL_EVENT_FITTED_MODEL_V1"
    assert model.MODEL_FAMILY == "NFL_OUTRIGHT_WIN_LOGREG_V1"
    assert model.CALIBRATION_METHOD == "PLATT_TIME_SPLIT_V1"
