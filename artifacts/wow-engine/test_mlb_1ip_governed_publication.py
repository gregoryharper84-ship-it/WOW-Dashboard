from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import mlb_1ip_empirical_specialist as specialist
import mlb_1ip_final_refresh_job as refresh_job
import mlb_1ip_ingress_runtime as ingress
from prop_terminal_reducer_v2 import reduce_prop_terminal


def _artifact() -> dict:
    return {
        "ok": True,
        "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY",
        "model_family": specialist.MODEL_FAMILY,
        "feature_schema_version": "PROP_FEATURES_V1",
        "model_artifact_version": "test-artifact-v1",
        "artifact_checksum": "abc123",
        "certification_id": "test-cert",
        "validation_metrics": {
            "validated_lines": [15.5],
            "gates_passed": True,
        },
        "artifact_payload": {},
    }


def _scored(*, p: float = 0.62, lb: float = 0.56, ub: float = 0.68) -> dict:
    return {
        "P_MORE": p,
        "P_LESS": 1.0 - p,
        "prob_push": 0.0,
        "selected_probability": p,
        "lower_bound": lb,
        "upper_bound": ub,
        "selected_support_n": 1000,
        "selected_support_count": 620,
    }


def _projected_top_four() -> list[dict]:
    return [
        {
            "player": f"Batter {idx}",
            "handedness": "R",
            "p_pa_vs_pitcher_profile": 0.25,
        }
        for idx in range(1, 5)
    ]


def test_confirmed_1ip_publishes_governed_sporting_probability_without_market(monkeypatch):
    monkeypatch.setattr(specialist, "score_empirical_pmf", lambda *args, **kwargs: _scored())

    result = specialist.score_mlb_1ip_empirical(
        artifact_record=_artifact(),
        starter_status="CONFIRMED",
        official_lineup_status="CONFIRMED",
        projected_top_four=None,
        line_value=15.5,
        side="MORE",
        market_evidence_present=False,
    )

    assert result["model_evaluated"] is True
    assert result["lineup_evidence_state"] == "OFFICIAL_CONFIRMED"
    assert result["final_refresh_required"] is False
    assert result["probability_publishable"] is True
    assert result["calibrated_probability"] == 0.62
    assert result["calibrated_probability_lower_bound"] == 0.56
    assert result["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert result["model_qualified"] is True
    assert result["probability_rank_eligible"] is True
    assert "MARKET_DATA_UNAVAILABLE" in result["blockers"]
    assert result["can_execute"] is False


def test_confirmed_1ip_below_threshold_returns_probability_bearing_native_terminal(monkeypatch):
    monkeypatch.setattr(
        specialist,
        "score_empirical_pmf",
        lambda *args, **kwargs: _scored(p=0.5262762762762763, lb=0.4994244594722477, ub=0.5529769629718504),
    )

    result = specialist.score_mlb_1ip_empirical(
        artifact_record=_artifact(),
        starter_status="CONFIRMED",
        official_lineup_status="CONFIRMED",
        projected_top_four=None,
        line_value=15.5,
        side="MORE",
        market_evidence_present=False,
    )

    assert result["probability_publishable"] is True
    assert result["model_evaluated"] is True
    assert result["terminal_label"] == "NO_LOW_PROBABILITY"
    assert result["model_qualified"] is False
    assert result["probability_rank_eligible"] is False
    assert result["calibrated_probability"] == 0.5262762762762763
    assert "MARKET_DATA_UNAVAILABLE" in result["blockers"]
    assert result["can_execute"] is False


def test_projected_1ip_keeps_final_refresh_hold_nonpublishable(monkeypatch):
    monkeypatch.setattr(specialist, "score_empirical_pmf", lambda *args, **kwargs: _scored())

    result = specialist.score_mlb_1ip_empirical(
        artifact_record=_artifact(),
        starter_status="CONFIRMED",
        official_lineup_status="PROJECTED",
        projected_top_four=_projected_top_four(),
        line_value=15.5,
        side="MORE",
        market_evidence_present=True,
    )

    assert result["lineup_evidence_state"] == "PROJECTED_OR_RECONSTRUCTED"
    assert result["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert result["final_refresh_required"] is True
    assert result["probability_publishable"] is False
    assert result["model_qualified"] is False
    assert result["probability_rank_eligible"] is False
    assert result["can_execute"] is False


def test_ingress_keeps_market_block_downstream_without_erasing_confirmed_probability(monkeypatch):
    monkeypatch.setattr(specialist, "score_empirical_pmf", lambda *args, **kwargs: _scored())
    row = SimpleNamespace(
        event_id="event-1",
        event_start_time=(datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
        player="Pitcher One",
        line=15.5,
        direction="MORE",
        money_lane_status="PAYOUT_UNRESOLVED",
        market_side_a=None,
        market_side_b=None,
        source_type="NORMALIZED",
        platform="PrizePicks",
        evidence=SimpleNamespace(
            lineup_evidence={
                "starter_name_at_capture": "Pitcher One",
                "starter_name": "Pitcher One",
                "starter_status": "CONFIRMED",
                "official_lineup_status": "CONFIRMED",
                "projected_top_four": None,
            },
            role_status={"status": "CONFIRMED", "role": "STARTING_PITCHER"},
        ),
    )
    market_api = SimpleNamespace(_prop_route_artifact=lambda *args, **kwargs: _artifact())

    outcome = ingress.score_mlb_1ip_ingress(
        row=row,
        row_key="row-1",
        market_api=market_api,
        request_id="request-1",
        run_research=lambda **kwargs: (True, {"stage": "PASS", "blockers": []}),
        terminal=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("terminal should not be used")),
        reduce_terminal=reduce_prop_terminal,
    )

    assert outcome["terminal_status"] == "COMPLETED"
    assert outcome["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert outcome["verdict_class"] == "MARKET_BLOCKED"
    assert outcome["probability_publishable"] is True
    assert outcome["model_qualified"] is True
    assert outcome["value_qualification_status"] == "PENDING_EXACT_PRICE"
    assert outcome["result"]["calibrated_probability"] == 0.62
    assert "MARKET_DATA_UNAVAILABLE" in outcome["result"]["blockers"]
    assert outcome["can_execute"] is False


def test_market_snapshot_survives_provisional_queue_for_final_refresh():
    row = SimpleNamespace(
        money_lane_status="PAYOUT_UNRESOLVED",
        market_side_a={"platform": "PrizePicks", "line": 15.5, "direction": "MORE"},
        market_side_b=None,
    )
    snapshot = ingress._market_evidence_snapshot(row)

    assert ingress._market_evidence_present(row) is True
    assert snapshot["market_side_a"]["line"] == 15.5
    assert refresh_job._market_evidence_present(
        {
            "money_lane_status": "PAYOUT_UNRESOLVED",
            "provisional_evidence": {"_market_evidence": snapshot},
        }
    ) is True


class _Response:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, client, *, data=None):
        self.client = client
        self.data = data
        self.pending_update = None

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def gt(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def update(self, payload):
        self.pending_update = dict(payload)
        return self

    def execute(self):
        if self.pending_update is not None:
            self.client.updates.append(self.pending_update)
            return _Response([])
        return _Response(self.data)


class _FakeClient:
    def __init__(self, row, artifact):
        self.row = row
        self.artifact = artifact
        self.updates = []

    def table(self, name):
        if name == "wow_mlb_1ip_refresh_queue":
            return _FakeQuery(self, data=[self.row])
        raise AssertionError(name)

    def rpc(self, name, payload):
        assert name == "wow_prop_certified_model_artifact"
        return _FakeQuery(self, data=self.artifact)


def test_final_refresh_persists_publishable_confirmed_rerun_and_recovered_market(monkeypatch):
    now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    market_snapshot = {
        "money_lane_status": "PAYOUT_UNRESOLVED",
        "market_side_a": {"platform": "PrizePicks", "line": 15.5, "direction": "MORE"},
        "market_side_b": None,
    }
    row = {
        "queue_id": "queue-1",
        "row_key": "row-1",
        "event_start_time": (now + timedelta(hours=4)).isoformat(),
        "player": "Pitcher One",
        "starter_name_at_capture": "Pitcher One",
        "line": 15.5,
        "direction": "MORE",
        "money_lane_status": "PAYOUT_UNRESOLVED",
        "refresh_attempts": 1,
        "provisional_evidence": {"_market_evidence": market_snapshot},
    }
    client = _FakeClient(row, _artifact())

    def fake_score(**kwargs):
        assert kwargs["market_evidence_present"] is True
        return {
            "terminal_label": "MODEL_QUALIFIED_HOLD",
            "model_evaluated": True,
            "calibrated_probability": 0.62,
            "probability_publishable": True,
            "can_execute": False,
        }

    monkeypatch.setattr(refresh_job, "score_mlb_1ip_empirical", fake_score)

    counters = refresh_job.run_once(
        client=client,
        now=now,
        hydrator=lambda **kwargs: {
            "starter_name": "Pitcher One",
            "starter_status": "CONFIRMED",
            "official_lineup_status": "CONFIRMED",
            "projected_top_four": None,
        },
    )

    assert counters["rerun_completed"] == 1
    assert client.updates
    persisted = client.updates[-1]
    assert persisted["status"] == "RERUN_COMPLETED"
    assert persisted["probability_publishable"] is True
    assert persisted["rerun_result"]["calibrated_probability"] == 0.62
    assert persisted["can_execute"] is False
