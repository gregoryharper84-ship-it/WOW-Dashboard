from types import SimpleNamespace

import mlb_1ip_ingress_runtime as ingress
from mlb_1ip_artifact_pipeline import TrainingRow
from mlb_1ip_empirical_pmf import fit_empirical_pmf


VALIDATED_LINES = [11.5, 13.5, 15.5, 17.5, 19.5, 21.5]


class _Decision:
    terminal_label = "MODEL_QUALIFIED_HOLD"
    pick_rejected = False
    verdict_class = "MODEL_SUPPORTED_HOLD"
    infrastructure_blocked = False


class _Table:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.payload = None

    def upsert(self, payload, on_conflict=None):
        self.payload = payload
        self.on_conflict = on_conflict
        return self

    def execute(self):
        if self.fail:
            raise RuntimeError("db unavailable")
        return SimpleNamespace(data=[{"queue_id": "queue-1"}])


class _Client:
    def __init__(self, *, fail=False):
        self.table_obj = _Table(fail=fail)

    def table(self, name):
        assert name == "wow_mlb_1ip_refresh_queue"
        return self.table_obj


def _artifact():
    rows = []
    rows.extend(TrainingRow(bf=3, pitches=12 + i % 4) for i in range(450))
    rows.extend(TrainingRow(bf=4, pitches=16 + i % 5) for i in range(400))
    rows.extend(TrainingRow(bf=5, pitches=21 + i % 6) for i in range(300))
    payload = fit_empirical_pmf(rows)
    return {
        "ok": True,
        "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY",
        "model_family": payload["model_family"],
        "model_artifact_version": "MLB_1IP_TEST_ARTIFACT_V1",
        "artifact_checksum": payload["artifact_checksum"],
        "certification_id": "PROP-CERT-TEST-MLB-1IP",
        "artifact_payload": payload,
        "supported_line_min": 11.5,
        "supported_line_max": 21.5,
        "feature_schema_version": "PROP_FEATURES_V1",
        "validation_metrics": {"validated_lines": VALIDATED_LINES},
        "probability_publishable": False,
        "can_execute": False,
    }


class _MarketAPI:
    def __init__(self, *, fail=False):
        client = _Client(fail=fail)
        self.client = client
        self.prod = SimpleNamespace(get_client=lambda: client)
        self._artifact = _artifact()

    def _prop_route_artifact(self, sport, stat):
        assert sport == "MLB"
        assert stat == "1ST_INNING_PITCHES_THROWN"
        return dict(self._artifact)


def _row(evidence=None):
    return SimpleNamespace(
        event_id="777",
        event_start_time="2099-09-01T20:00:00+00:00",
        player="Test Pitcher",
        line=15.5,
        direction="MORE",
        source_type="NORMALIZED",
        platform=None,
        money_lane_status="PAYOUT_UNRESOLVED",
        market_side_a=None,
        market_side_b=None,
        evidence=evidence,
    )


def _terminal(row_key, status, code, *, detail=None, acquisition=None, **kwargs):
    return {
        "row_key": row_key,
        "terminal_status": status,
        "code": code,
        "terminal_label": (detail or {}).get("terminal_label", "MODEL_UNAVAILABLE"),
        "detail": detail or {},
        "acquisition": acquisition or {},
        "probability_publishable": False,
        "can_execute": False,
    }


def _reduce(**kwargs):
    return _Decision()


def _hydrated(*, lineup_status="TBD"):
    return {
        "starter_name": "Test Pitcher",
        "starter_name_at_capture": "Test Pitcher",
        "starter_status": "CONFIRMED",
        "official_lineup_status": lineup_status,
        "projected_top_four": [
            {"player": "A", "handedness": "R", "p_pa_vs_pitcher_profile": 4.0},
            {"player": "B", "handedness": "L", "p_pa_vs_pitcher_profile": 4.1},
            {"player": "C", "handedness": "R", "p_pa_vs_pitcher_profile": 4.2},
            {"player": "D", "handedness": "L", "p_pa_vs_pitcher_profile": 4.3},
        ],
        "pitcher_bf_distribution": {"p_bf_3": 0.4, "p_bf_4": 0.4, "p_bf_gte5": 0.2},
        "baseline_pitches_per_batter": {"mean": 4.1, "std": 1.0},
        "failure_path_prior": {"status": "RESOLVED_FROM_OFFICIAL_PRIOR_STARTS"},
        "can_execute": False,
    }


def test_no_caller_evidence_auto_hydrates_then_runs_research_before_specialist(monkeypatch):
    order = []

    def hydrate(**kwargs):
        order.append("hydrate")
        return _hydrated(lineup_status="CONFIRMED")

    def research(**kwargs):
        order.append("research")
        return True, {"stages": [{"worker_id": "wow.global-scout-coordinator", "status": "SUCCEEDED"}]}

    real_score = ingress.score_mlb_1ip_empirical

    def score(**kwargs):
        order.append("specialist")
        return real_score(**kwargs)

    monkeypatch.setattr(ingress, "hydrate_mlb_1ip_evidence", hydrate)
    monkeypatch.setattr(ingress, "score_mlb_1ip_empirical", score)

    out = ingress.score_mlb_1ip_ingress(
        row=_row(),
        row_key="r1",
        market_api=_MarketAPI(),
        request_id="req-1",
        run_research=research,
        terminal=_terminal,
        reduce_terminal=_reduce,
    )

    assert order == ["hydrate", "research", "specialist"]
    assert out["acquisition"]["mode"] == "AUTO_HYDRATION"
    assert out["acquisition"]["status"] == "PASS"
    assert out["model_evaluated"] is True
    assert out["result"]["model_family"] == "MLB_1IP_CONDITIONAL_TOTAL_PITCH_PMF_V1"
    assert out["final_refresh_required"] is False
    assert out["refresh_queue"]["status"] == "NOT_REQUIRED"
    assert out["probability_publishable"] is True
    assert out["value_qualification_status"] == "PENDING_EXACT_PRICE"
    assert "MARKET_DATA_UNAVAILABLE" in out["result"]["blockers"]
    assert out["can_execute"] is False


def test_provisional_auto_hydrated_result_is_queued(monkeypatch):
    monkeypatch.setattr(ingress, "hydrate_mlb_1ip_evidence", lambda **kwargs: _hydrated(lineup_status="TBD"))
    market_api = _MarketAPI()
    out = ingress.score_mlb_1ip_ingress(
        row=_row(), row_key="r2", market_api=market_api, request_id="req-2",
        run_research=lambda **kwargs: (True, {"stages": []}), terminal=_terminal, reduce_terminal=_reduce,
    )
    assert out["model_evaluated"] is True
    assert out["final_refresh_required"] is True
    assert out["refresh_queue"]["status"] == "QUEUED"
    assert out["refresh_queue"]["queue_id"] == "queue-1"
    assert market_api.client.table_obj.payload["status"] == "WAITING_FOR_OFFICIAL_LINEUP"
    assert market_api.client.table_obj.payload["can_execute"] is False


def test_refresh_queue_failure_preserves_completed_sporting_probability(monkeypatch):
    monkeypatch.setattr(ingress, "hydrate_mlb_1ip_evidence", lambda **kwargs: _hydrated(lineup_status="TBD"))
    out = ingress.score_mlb_1ip_ingress(
        row=_row(), row_key="r3", market_api=_MarketAPI(fail=True), request_id="req-3",
        run_research=lambda **kwargs: (True, {"stages": []}), terminal=_terminal, reduce_terminal=_reduce,
    )
    assert out["model_evaluated"] is True
    assert out["result"]["P_MORE"] is not None
    assert out["refresh_queue"]["status"] == "PERSISTENCE_UNAVAILABLE"
    assert "FINAL_REFRESH_QUEUE_PERSISTENCE_UNAVAILABLE" in out["result"]["blockers"]
    assert out["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert out["probability_publishable"] is False
    assert out["can_execute"] is False


def test_provider_failure_is_not_model_unavailable(monkeypatch):
    from prop_auto_hydration import PropAutoHydrationError

    def fail(**kwargs):
        raise PropAutoHydrationError(
            "PROP_AUTO_HYDRATION_PROVIDER_UNAVAILABLE",
            "official source unavailable",
            detail={"provider": "MLB_STATS_API_OFFICIAL_1IP_V1"},
        )

    monkeypatch.setattr(ingress, "hydrate_mlb_1ip_evidence", fail)
    out = ingress.score_mlb_1ip_ingress(
        row=_row(), row_key="r4", market_api=_MarketAPI(), request_id="req-4",
        run_research=lambda **kwargs: (_ for _ in ()).throw(AssertionError("research must not run")),
        terminal=_terminal, reduce_terminal=_reduce,
    )
    assert out["code"] == "PROP_AUTO_HYDRATION_PROVIDER_UNAVAILABLE"
    assert out["terminal_label"] == "RESEARCH_INTEREST"
    assert out["terminal_label"] != "MODEL_UNAVAILABLE"
    assert out["acquisition"]["status"] == "FAILED"
    assert out["can_execute"] is False


def test_specialist_invocation_exception_is_model_scorer_failed(monkeypatch):
    monkeypatch.setattr(ingress, "hydrate_mlb_1ip_evidence", lambda **kwargs: _hydrated(lineup_status="CONFIRMED"))

    def broken_specialist(**kwargs):
        raise TimeoutError("empirical specialist timed out")

    monkeypatch.setattr(ingress, "score_mlb_1ip_empirical", broken_specialist)

    out = ingress.score_mlb_1ip_ingress(
        row=_row(), row_key="r5", market_api=_MarketAPI(), request_id="req-5",
        run_research=lambda **kwargs: (True, {"stages": []}), terminal=_terminal, reduce_terminal=_reduce,
    )
    assert out["code"] == "MODEL_SCORER_FAILED"
    assert out["terminal_label"] == "MODEL_UNAVAILABLE"
    assert out["detail"]["specialist_invoked"] is True
    assert out["detail"]["error_type"] == "TimeoutError"
    assert out["probability_publishable"] is False
    assert out["can_execute"] is False
