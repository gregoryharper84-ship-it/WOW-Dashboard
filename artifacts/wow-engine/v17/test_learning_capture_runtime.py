"""Source-to-intelligence capture tests using deterministic fake DB readbacks.

No provider signatures, settled real-world games or production DB writes.
"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from v17.core_intelligence_runtime import capture_settled_prop_observations
from v17.core_intelligence_event_runtime import capture_settled_event_observations


class FakeDB:
    def __init__(self, data):
        self.data = deepcopy(data)
        self.written = []
    def table(self, name):
        return Query(self, name)


class Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.mode = "read"
        self.ids = None
        self.rows = None
    def select(self, *_):
        return self
    def order(self, *_args, **_kwargs):
        return self
    def limit(self, *_):
        return self
    def in_(self, name, values):
        self.ids = (name, set(values))
        return self
    def upsert(self, rows, **_kwargs):
        self.rows, self.mode = deepcopy(rows), "write"
        return self
    def execute(self):
        if self.mode == "write":
            self.db.written.extend(self.rows)
            return SimpleNamespace(data=deepcopy(self.rows))
        rows = deepcopy(self.db.data.get(self.table, []))
        if self.ids is not None:
            key, wanted = self.ids
            rows = [r for r in rows if r.get(key) in wanted]
        return SimpleNamespace(data=rows)


def psource(pid, *, publishable=True):
    return dict(
        prediction_id=pid, sport="NBA", market_type="PLAYER_PROP", stat_type="PTS",
        line=20.5, direction="MORE", model_family="NBA_POINTS",
        model_artifact_version="v1", calibration_version="cal-v1",
        raw_model_probability=.99,
        calibrated_probability=.63, calibrated_probability_lower_bound=.53,
        event_start_time="2026-11-21T19:00:00Z",
        created_at="2026-11-21T17:00:00Z",
        model_timestamp="2026-11-21T18:00:00Z",
        locked_at="2026-11-21T18:30:00Z",
        source_snapshot_id="source", model_artifact_checksum="artifact",
        feature_snapshot_hash="freeze", probability_publishable=publishable,
        failure_cause_tags=["MARGIN_VARIANCE"],
    )


def poutcome(pid, result="HIT"):
    return dict(
        prediction_id=pid, official_result=result, actual_stat=24,
        hit=True, push=False, void=False, settlement_source="official source",
        settlement_timestamp="2026-11-21T23:00:00Z",
        failure_category="GAME_SCRIPT",
    )


def esource(pid, *, publishable=True):
    return dict(
        event_prediction_id=pid, sport="NHL", league="NHL", market_family="OUTRIGHT_WINNER",
        home_team="Home Name", away_team="Away Name",
        controlling_specialist="NHL_WINNER", model_version="v1",
        calibration_version="cal-v1", calibrated_home_probability=.63,
        calibrated_home_lower_bound=.53, raw_home_probability=.99,
        event_start_time="2026-11-21T19:00:00Z",
        created_at="2026-11-21T17:00:00Z",
        model_timestamp="2026-11-21T18:00:00Z",
        latest_material_update_timestamp="2026-11-21T17:45:00Z",
        source_snapshot_id="source", model_artifact_id="artifact",
        model_valid_after_latest_update=True,
        calibration_status="PASS", probability_publishable=publishable,
    )


def eoutcome(pid, *, void=False):
    return dict(
        event_prediction_id=pid, official_winner="Home Name",
        home_score=3, away_score=2, void=void,
        settlement_source="official league",
        settlement_timestamp="2026-11-21T23:00:00Z",
        failure_category=None,
    )


def test_prop_capture_excludes_void_suffix_and_uncalibrated_research():
    db = FakeDB({
        "wow_outcomes": [
            poutcome("good"), poutcome("void", "VOID_PLAYER_DNP"),
            poutcome("held"),
        ],
        "wow_predictions": [
            psource("good"), psource("void"), psource("held", publishable=False),
        ],
    })
    result = capture_settled_prop_observations(db)
    assert result["status"] == "PASS"
    assert result["candidate_n"] == 3 and result["observation_n"] == 1
    assert result["skipped_n"] == 2
    assert result["candidate_n"] == result["observation_n"] + result["skipped_n"]
    assert result["skip_reasons"]["NONBINARY_SETTLEMENT_EXCLUDED_FROM_CALIBRATION"] == 1
    assert result["skip_reasons"]["MODEL_NOT_PUBLISHABLE_FOR_CALIBRATION"] == 1
    assert len(db.written) == 1
    row = db.written[0]
    assert row["source_prediction_id"] == "good"
    assert row["probability"] == .63
    assert row["probability_source"] == "calibrated_probability"
    assert row["brier_score"] == (1 - .63) ** 2
    assert row["can_execute"] is False
    assert result["source_provenance_authenticated_externally"] is False


def test_event_capture_excludes_tie_three_way_and_stale_model():
    good, tie, threeway, stale = [
        esource(name) for name in ["good", "tie", "threeway", "stale"]
    ]
    threeway["market_family"] = "SOCCER_1X2"
    stale["latest_material_update_timestamp"] = "2026-11-21T18:25:00Z"
    outcome_tie = eoutcome("tie")
    outcome_tie["home_score"] = outcome_tie["away_score"]
    db = FakeDB({
        "wow_event_outcomes": [
            eoutcome("good"), outcome_tie,
            eoutcome("threeway"), eoutcome("stale"),
        ],
        "wow_event_predictions": [good, tie, threeway, stale],
    })
    result = capture_settled_event_observations(db)
    assert result["status"] == "PASS"
    assert result["candidate_n"] == 4 and result["observation_n"] == 1
    assert result["skipped_n"] == 3
    assert result["candidate_n"] == result["observation_n"] + result["skipped_n"]
    assert result["skip_reasons"]["MULTICLASS_ROUTE_REQUIRED"] == 1
    assert result["skip_reasons"]["NONBINARY_OR_UNVERIFIED_EVENT_SCORE"] == 1
    assert result["skip_reasons"]["LATEST_MATERIAL_UPDATE_NOT_CONSUMED"] == 1
    assert len(db.written) == 1
    assert db.written[0]["source_prediction_id"] == "good"
    assert db.written[0]["probability_source"] == "calibrated_probability"
    assert db.written[0]["probability"] == .63
    assert result["can_execute"] is False


def test_event_no_record_does_not_fabricate_calibration_from_raw():
    held = esource("held", publishable=False)
    db = FakeDB({
        "wow_event_outcomes": [eoutcome("held")],
        "wow_event_predictions": [held],
    })
    result = capture_settled_event_observations(db)
    assert result["candidate_n"] == 1 and result["observation_n"] == 0
    assert result["skip_reasons"] == {"MODEL_NOT_PUBLISHABLE_FOR_CALIBRATION": 1}
    assert not db.written
