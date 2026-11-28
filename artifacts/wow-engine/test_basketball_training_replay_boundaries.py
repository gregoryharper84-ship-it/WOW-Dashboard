from types import SimpleNamespace

import pytest

import basketball_specialist_pipeline as pipeline
from basketball_specialist_pipeline import load_games


class _Query:
    def __init__(self, pages):
        self.pages = pages
        self.orders = []
        self._range = (0, 999)

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def order(self, column, *_args, **_kwargs):
        self.orders.append(column)
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def execute(self):
        page = self._range[0] // 1000
        data = self.pages[page] if page < len(self.pages) else []
        return SimpleNamespace(data=data)


class _Client:
    def __init__(self, pages):
        self.query = _Query(pages)

    def table(self, _name):
        return self.query


def _row(game_id: str, game_date: str = "2024-01-01"):
    return {
        "game_id": game_id,
        "season": 2024,
        "game_date": game_date,
        "home_team_id": "H",
        "away_team_id": "A",
        "home_score": 100,
        "away_score": 90,
    }


def test_load_games_uses_total_order_for_range_pagination():
    client = _Client([[_row("1"), _row("2")]])
    games = load_games(client, "NBA")
    assert [g.game_id for g in games] == ["1", "2"]
    assert client.query.orders[:2] == ["game_date", "game_id"]


def test_load_games_fails_closed_if_paginated_source_repeats_game_id():
    first_page = [_row(str(i), "2024-01-01") for i in range(1000)]
    second_page = [_row("999", "2024-01-02")]
    client = _Client([first_page, second_page])
    with pytest.raises(RuntimeError, match="TRAINING_PAGINATION_DUPLICATE_GAME_IDS"):
        load_games(client, "NBA")


def test_fit_and_persist_verifies_the_certification_row_after_the_upsert(monkeypatch):
    wrote = []
    verified = []

    class _WriteClient:
        def table(self, name):
            self.table_name = name
            return self

        def insert(self, payload):
            wrote.append(("insert", self.table_name, payload))
            return self

        def upsert(self, payload, on_conflict=None):
            wrote.append(("upsert", self.table_name, payload, on_conflict))
            return self

        def execute(self):
            return SimpleNamespace(data=[])

    artifact = SimpleNamespace(
        artifact_checksum="a" * 64,
        training_row_count=240,
        train_start_date="2024-01-01",
        train_end_date="2024-06-01",
        holdout_row_count=60,
        holdout_brier=0.21,
        holdout_log_loss=0.62,
        holdout_accuracy=0.61,
    )
    decision = SimpleNamespace(capability_status="CERTIFIED", reason_code="OK")
    calibration = SimpleNamespace(
        coefficients=SimpleNamespace(a=0.1, b=0.9),
        metrics=SimpleNamespace(brier=0.22, log_loss=0.63, ece=0.01, calibration_bias=0.0),
        result=SimpleNamespace(calibration_status="PLATT_TIME_SPLIT_V1"),
    )

    monkeypatch.setattr(pipeline, "asdict", lambda obj: {"sport": "NBA", "notes": []})
    monkeypatch.setattr(pipeline, "load_games", lambda client, sport: [object()] * 300)
    monkeypatch.setattr(pipeline, "build_pregame_features", lambda games, sport: [object()] * 290)
    monkeypatch.setattr(
        pipeline,
        "train_specialist",
        lambda features, sport, model_artifact_version: artifact,
    )
    monkeypatch.setattr(
        pipeline,
        "disjoint_calibration_and_holdout",
        lambda features, fitted: ([object()] * 230, [object()] * 60),
    )
    monkeypatch.setattr(
        pipeline,
        "_walk_forward_raw",
        lambda rows: ([0.5] * 210, [1] * 210, [0] * 210, ["2024-01-01T00:00:00+00:00"] * 210),
    )
    monkeypatch.setattr(pipeline, "phase_b_platt", lambda *args: calibration)
    monkeypatch.setattr(pipeline, "certification_decision", lambda *args, **kwargs: decision)
    monkeypatch.setattr(
        pipeline,
        "evaluate_untouched_calibrated_holdout",
        lambda *args: {"holdout_rows": 60},
    )
    monkeypatch.setattr(
        pipeline,
        "verify_certification_write",
        lambda client, **kwargs: verified.append(kwargs) or {},
    )

    result = pipeline.fit_and_persist("NBA", client=_WriteClient(), provenance_complete=True)

    assert [entry[0] for entry in wrote] == ["insert", "insert", "upsert"]
    assert wrote[0][1] == "wow_basketball_team_event_model_artifacts"
    assert wrote[1][1] == "wow_basketball_team_event_calibrators"
    assert wrote[2][1] == "wow_basketball_team_event_certification"
    assert wrote[2][3] == "sport"
    assert wrote[2][2]["capability_status"] == "SHADOW"
    assert wrote[2][2]["reason_code"] == "CERTIFICATION_REPLAY_REQUIRED"
    assert wrote[0][2]["promoted"] is False
    assert wrote[0][2]["active"] is False
    assert wrote[1][2]["promoted"] is False
    assert wrote[1][2]["active"] is False

    assert verified, "the certification upsert must be verified by a read-back"
    assert verified[0]["sport"] == "NBA"
    assert verified[0]["model_artifact_version"] == wrote[2][2]["model_artifact_version"]
    assert verified[0]["calibrator_version"] == wrote[1][2]["calibrator_version"]
    assert result["certification_write_verified"] is True
    assert result["persisted_status"] == "SHADOW"
    assert result["promotion_attempted"] is False
    assert result["can_execute"] is False
