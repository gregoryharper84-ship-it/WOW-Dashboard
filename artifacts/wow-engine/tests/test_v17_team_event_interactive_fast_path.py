from types import SimpleNamespace

from v17.sep15_runtime_contract_repairs import install_post_mlb_bridge_repairs


class Result:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, data):
        self.data = data

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        return Result(self.data)


class Client:
    def __init__(self, receipt):
        self.receipt = receipt
        self.rpc_calls = []

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        return Query(self.receipt)

    def table(self, _name):
        return Query([])


def projected_receipt():
    return {
        "code": "REAL_FITTED_MODEL_PATH_PROVEN",
        "status": "MODEL_SCORED_HELD",
        "can_execute": False,
        "score_status": "SHADOW_SCORED_LINEUP_PENDING",
        "lineup_status": "NOT_YET_AVAILABLE",
        "model_version": "MLB_FITTED_MODEL",
        "model_timestamp": "2026-09-21T13:00:00+00:00",
        "shadow_event_id": "shadow-1",
        "score_snapshot_id": "score-1",
        "server_snapshot_id": "snap-1",
        "server_snapshot_timestamp": "2026-09-21T12:59:00+00:00",
        "ratification_status": "RATIFIED",
        "controlling_specialist": "wow.mlb-game-win-probability-expert",
        "probability_publishable": False,
        "feature_hydration_status": "PASS",
        "calibration_health_status": "PASS",
        "scoring_evidence_produced": True,
        "probability_fields_withheld": True,
        "current_publication_blockers": [
            "LINEUP_NOT_CONFIRMED",
            "OFFICIAL_LINEUP_REFRESH_OFFICIAL_LINEUP_NOT_AVAILABLE",
            "POST_LINEUP_SCORE_SNAPSHOT_REQUIRED",
        ],
        "governed_probability_capability": "AVAILABLE",
    }


def request():
    return SimpleNamespace(
        official_event_id="824787",
        event_start_time_utc="2026-09-21T20:00:00+00:00",
        requested_slate_date="2026-09-21",
        home_team="Home",
        away_team="Away",
        venue="Park",
        home_starting_pitcher="Home Starter",
        away_starting_pitcher="Away Starter",
        source_snapshot_id="snap-1",
        latest_material_update_timestamp="2026-09-21T12:59:00+00:00",
        sport_specific_evidence={
            "venue": "Park",
            "home_starting_pitcher": "Home Starter",
            "away_starting_pitcher": "Away Starter",
            "home_lineup_status": "NOT_YET_AVAILABLE",
            "away_lineup_status": "NOT_YET_AVAILABLE",
        },
    )


def test_projected_lineup_reuses_immutable_receipt_before_direct_scorer():
    client = Client(projected_receipt())
    direct_scorer_calls = []

    class EventApi:
        def get_client(self):
            return client

        def score_event(self, _req):
            direct_scorer_calls.append(True)
            raise AssertionError("direct scorer must not run before projected receipt reuse")

    event_api = EventApi()
    market_api = SimpleNamespace(prod=SimpleNamespace(event_api=event_api))
    runtime = SimpleNamespace(
        _run_mlb_llp_governance=lambda _req, _route, result, envelope=None, *, event_api=None: result,
    )

    assert install_post_mlb_bridge_repairs(market_api=market_api, team_runtime=runtime) is True
    result = event_api.score_event(request())

    assert direct_scorer_calls == []
    assert len(client.rpc_calls) == 1
    assert client.rpc_calls[0][0] == "wow_mlb_score_event_bridge"
    assert result["sport_model_invocation_source"] == "IMMUTABLE_PROJECTED_SCORE_SNAPSHOT"
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False
