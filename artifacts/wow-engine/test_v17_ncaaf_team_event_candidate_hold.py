from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from v17 import ncaaf_team_event_candidate_hold as hold


class _Result:
    def __init__(self, data): self.data = data


class _Query:
    def __init__(self, table, rows):
        self.table_name = table
        self.rows = rows
    def select(self, *_args, **_kwargs): return self
    def eq(self, *_args, **_kwargs): return self
    def order(self, *_args, **_kwargs): return self
    def limit(self, *_args, **_kwargs): return self
    def execute(self): return _Result(self.rows[self.table_name])


class _DB:
    def __init__(self, *, candidate=True, source_review="REQUIRED", latest_history="2026-09-13T03:59:00+00:00"):
        candidate_rows = []
        if candidate:
            candidate_rows = [{
                "candidate_id": "ncaaf-candidate-1",
                "model_family": "NCAAF_DYNAMIC_TEAM_STATE_LOGIT_V2",
                "model_artifact_version": "NCAAF_DYNAMIC_TEAM_STATE_LOGIT_V2_TEST",
                "feature_schema_version": "NCAAF_DYNAMIC_TEAM_STATE_V2",
                "source_review_status": source_review,
                "lifecycle_state": "CANDIDATE",
                "research_screen_pass": True,
                "training_rows": 1839,
                "calibration_rows": 613,
                "test_rows": 613,
                "probability_publishable": False,
                "can_execute": False,
                "created_at": "2026-09-23T16:53:21+00:00",
            }]
        history_rows = [{"event_start_time": latest_history}] if latest_history else []
        self.rows = {
            "wow_d1_candidate_artifacts": candidate_rows,
            "wow_ncaaf_training_games": history_rows,
        }
    def table(self, name): return _Query(name, self.rows)


class _EventApi:
    def __init__(self, db): self.db = db
    def get_client(self): return self.db


def _request(sport="NCAAF"):
    return SimpleNamespace(
        sport=sport,
        league=sport,
        commence_time="2026-09-24T23:30:00Z",
        home_team="Coastal Carolina",
        away_team="Liberty",
    )


def _generic_detail():
    return {
        "code": "MODEL_UNAVAILABLE",
        "backend_route_status": "SPORT_SPECIFIC_TEAM_EVENT_ADAPTER_NOT_REGISTERED",
        "blockers": [hold.GENERIC_BLOCKER],
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def test_candidate_hold_refines_generic_unavailable_without_scoring_stale_model():
    detail = hold._candidate_hold(_request(), _EventApi(_DB()), _generic_detail())
    assert detail is not None
    assert detail["code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert detail["model_candidate_present"] is True
    assert detail["candidate_research_screen_pass"] is True
    assert detail["specialist_invoked"] is False
    assert detail["model_evaluated"] is False
    assert detail["scoring_attempted"] is False
    assert hold.SOURCE_REVIEW_BLOCKER in detail["blockers"]
    assert hold.CERTIFICATION_BLOCKER in detail["blockers"]
    assert hold.HISTORY_STALE_BLOCKER in detail["blockers"]
    assert detail["probability_publishable"] is False
    assert detail["rank_eligible"] is False
    assert detail["can_execute"] is False


def test_source_review_pass_does_not_bypass_certification_or_stale_history():
    detail = hold._candidate_hold(
        _request(),
        _EventApi(_DB(source_review="PASS")),
        _generic_detail(),
    )
    assert detail is not None
    assert hold.SOURCE_REVIEW_BLOCKER not in detail["blockers"]
    assert hold.CERTIFICATION_BLOCKER in detail["blockers"]
    assert hold.HISTORY_STALE_BLOCKER in detail["blockers"]
    assert detail["code"] == "MODEL_INPUTS_INSUFFICIENT"


def test_no_candidate_keeps_original_model_unavailable_semantics():
    assert hold._candidate_hold(
        _request(),
        _EventApi(_DB(candidate=False)),
        _generic_detail(),
    ) is None


def test_overlay_changes_only_exact_ncaaf_generic_absence(monkeypatch):
    import v17.team_event_bridge_runtime as bridges
    import v17.team_event_request_runtime as base_runtime

    def original(req, *, event_api, canonical_hydration_required=False):
        raise HTTPException(status_code=409, detail=_generic_detail())

    monkeypatch.setattr(bridges, "score_registered_team_event_request", original)
    monkeypatch.setattr(base_runtime, "score_team_event_request", original)
    monkeypatch.delattr(bridges, "_v17_ncaaf_candidate_hold_installed", raising=False)

    assert hold.install_ncaaf_team_event_candidate_hold() is True

    with pytest.raises(HTTPException) as caught:
        bridges.score_registered_team_event_request(
            _request(),
            event_api=_EventApi(_DB()),
        )
    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert hold.HISTORY_STALE_BLOCKER in caught.value.detail["blockers"]

    with pytest.raises(HTTPException) as non_ncaaf:
        bridges.score_registered_team_event_request(
            _request("NBA"),
            event_api=_EventApi(_DB()),
        )
    assert non_ncaaf.value.status_code == 409
    assert non_ncaaf.value.detail["code"] == "MODEL_UNAVAILABLE"


def test_overlay_never_interferes_with_successful_production_scorer(monkeypatch):
    import v17.team_event_bridge_runtime as bridges
    import v17.team_event_request_runtime as base_runtime

    expected = {"probability_publishable": True, "can_execute": False}
    def original(req, *, event_api, canonical_hydration_required=False):
        return expected

    monkeypatch.setattr(bridges, "score_registered_team_event_request", original)
    monkeypatch.setattr(base_runtime, "score_team_event_request", original)
    monkeypatch.delattr(bridges, "_v17_ncaaf_candidate_hold_installed", raising=False)
    hold.install_ncaaf_team_event_candidate_hold()

    assert bridges.score_registered_team_event_request(
        _request(), event_api=_EventApi(_DB())
    ) is expected
