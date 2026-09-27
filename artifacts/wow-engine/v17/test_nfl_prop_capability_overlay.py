from __future__ import annotations

from v17 import nfl_prop_capability_overlay as subject


class _BaseApi:
    def _controlling_specialist_provider(self, _sport, stat):
        # Simulate the live condition that motivated #932: aggregate props can be
        # available while these exact NFL stat routes still resolve unavailable.
        if stat in {"PASSING_YARDS", "RUSHING_YARDS"}:
            return {"controlling_specialist": "MODEL_UNAVAILABLE"}
        return {"controlling_specialist": "WOW_NFL_PROP_SPECIALIST@v1"}


class _Prod:
    PROP_CAPABILITY_KEY = "PROP_PROBABILITY"
    base_api = _BaseApi()

    @staticmethod
    def _runtime_capability(_key):
        return {"capability_status": "AVAILABLE", "evidence": {}}


class _Market:
    prod = _Prod()

    @staticmethod
    def _prop_route_artifact(_sport, stat):
        if stat in {"PASSING_YARDS", "RUSHING_YARDS"}:
            return {"ok": False, "code": "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND"}
        return {
            "ok": True,
            "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY",
            "model_artifact_version": f"TEST_{stat}",
            "specialist_version": "WOW_NFL_PROP_SPECIALIST@v1",
        }


def test_exact_nfl_runtime_matrix_exposes_all_ten_declared_lanes():
    result = subject.build_nfl_prop_lane_runtime(_Market())

    assert result["declared_lane_count"] == 10
    assert result["declared_certified_production_lane_count"] == 4
    assert result["declared_candidate_lane_count"] == 1
    assert result["declared_build_required_lane_count"] == 5
    assert result["aggregate_prop_capability_is_not_lane_authority"] is True
    assert result["can_execute"] is False

    by_stat = {row["stat_type"]: row for row in result["lanes"]}
    assert by_stat["PASSING_YARDS"]["declared_lane_status"] == "CERTIFIED_PRODUCTION"
    assert by_stat["PASSING_YARDS"]["runtime_scoreable"] is False
    assert by_stat["PASSING_YARDS"]["runtime_code"] == "MODEL_UNAVAILABLE"
    assert by_stat["RUSHING_YARDS"]["runtime_scoreable"] is False
    assert by_stat["RUSHING_YARDS"]["runtime_code"] == "MODEL_UNAVAILABLE"
    assert by_stat["FANTASY_SCORE"]["declared_lane_status"] == "CANDIDATE_ONLY"
    assert by_stat["PASS_ATTEMPTS"]["declared_lane_status"] == "BUILD_REQUIRED"


def test_capability_overlay_uses_existing_capability_action_without_new_route(monkeypatch):
    from v17 import full_board_runtime as runtime

    monkeypatch.setattr(
        runtime,
        "capability_matrix",
        lambda: {
            "runtime_generation": "V17_ACTIVE",
            "global_terminal_authority": "V17_TERMINAL_REDUCER",
            "can_execute": False,
        },
    )
    monkeypatch.setattr(runtime, subject._PATCH_FLAG, False, raising=False)

    assert subject.install_nfl_prop_capability_overlay(_Market()) is True
    result = runtime.capability_matrix()

    assert "player_prop_lane_runtime" in result
    assert result["player_prop_lane_runtime"]["NFL"]["declared_lane_count"] == 10
    assert result["global_terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert result["can_execute"] is False
