"""Regression tests for ledger.insert_prediction's exact-identity idempotency
and controlling_specialist propagation.
"""
from types import SimpleNamespace

import pytest

import ledger


def _publishable_kwargs(**overrides):
    kwargs = dict(
        event_id="MLB:NYY:BOS:20260919",
        event_start_time="2026-09-19T23:00:00+00:00",
        sport="MLB",
        market_type=ledger._PROP_DISCRETE_MARKET_TYPE,
        stat_type="PITCHER_STRIKEOUTS",
        line=5.5,
        direction="MORE",
        source_snapshot_id="11111111-1111-1111-1111-111111111111",
        player="Gerrit Cole",
        model_timestamp="2026-09-19T20:00:00+00:00",
        raw_model_probability=0.62,
        model_provider_identity=ledger._PROP_PROVIDER_IDENTITY,
        model_family="DISCRETE_PMF_V1",
        model_artifact_version="v1",
        model_artifact_checksum="checksum",
        model_bundle_fingerprint="fingerprint",
        model_artifact_lifecycle_state="PROSPECTIVE_CERTIFIED",
        feature_schema_version="v1",
        feature_transform_version="v1",
        feature_snapshot_hash="hash",
        training_dataset_hash="hash",
        training_code_sha="sha",
        specialist_version="v1",
        certification_id="cert-1",
        distribution_type="DISCRETE_PMF",
        effective_sample_size=250.0,
        probability_more=0.62,
        probability_less=0.37,
        push_probability=0.01,
        calibrated_probability=0.60,
        calibrated_probability_lower_bound=0.52,
        calibrated_probability_upper_bound=0.68,
        calibration_status=ledger.CalibrationStatus.ISOTONIC_V1,
        controlling_specialist="wow.mlb-pitcher-strikeouts-specialist",
    )
    kwargs.update(overrides)
    return kwargs


class FakeFilterQuery:
    def __init__(self, rows):
        self._rows = rows
        self._filters: dict[str, object] = {}
        self._is_null: set[str] = set()

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def is_(self, column, _value):
        self._is_null.add(column)
        return self

    def execute(self):
        def matches(row):
            for column, value in self._filters.items():
                if row.get(column) != value:
                    return False
            for column in self._is_null:
                if row.get(column) is not None:
                    return False
            return True

        return SimpleNamespace(data=[row for row in self._rows if matches(row)])


class FakeInsert:
    def __init__(self, table, rows):
        self.table = table
        self.rows = rows

    def execute(self):
        self.table.rows.extend(self.rows)
        return SimpleNamespace(data=self.rows)


class FakeTable:
    def __init__(self):
        self.rows: list[dict] = []

    def select(self, _columns):
        return FakeFilterQuery(self.rows)

    def insert(self, payload):
        return FakeInsert(self, [dict(payload)])


class FakeClient:
    def __init__(self):
        self.tables = {"wow_predictions": FakeTable()}

    def table(self, name):
        return self.tables[name]


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(ledger, "get_client", lambda: client)
    return client


def test_first_insert_persists_a_new_row(fake_client):
    row = ledger.PredictionRow(**_publishable_kwargs())
    result = ledger.insert_prediction(row)
    assert result["controlling_specialist"] == "wow.mlb-pitcher-strikeouts-specialist"
    assert result["probability_publishable"] is True
    assert len(fake_client.tables["wow_predictions"].rows) == 1


def test_exact_duplicate_resubmission_does_not_create_a_second_row(fake_client):
    ledger.insert_prediction(ledger.PredictionRow(**_publishable_kwargs()))
    ledger.insert_prediction(ledger.PredictionRow(**_publishable_kwargs()))
    assert len(fake_client.tables["wow_predictions"].rows) == 1


def test_duplicate_check_returns_the_existing_frozen_row(fake_client):
    first = ledger.insert_prediction(ledger.PredictionRow(**_publishable_kwargs()))
    second = ledger.insert_prediction(ledger.PredictionRow(**_publishable_kwargs()))
    assert second["prediction_id"] == first["prediction_id"]


def test_different_player_same_event_and_line_is_not_treated_as_duplicate(fake_client):
    ledger.insert_prediction(ledger.PredictionRow(**_publishable_kwargs(player="Gerrit Cole")))
    ledger.insert_prediction(ledger.PredictionRow(**_publishable_kwargs(player="Chris Sale")))
    assert len(fake_client.tables["wow_predictions"].rows) == 2


def test_different_source_snapshot_is_not_treated_as_duplicate(fake_client):
    ledger.insert_prediction(
        ledger.PredictionRow(**_publishable_kwargs(source_snapshot_id="11111111-1111-1111-1111-111111111111"))
    )
    ledger.insert_prediction(
        ledger.PredictionRow(**_publishable_kwargs(source_snapshot_id="22222222-2222-2222-2222-222222222222"))
    )
    assert len(fake_client.tables["wow_predictions"].rows) == 2


def test_dedupe_check_failure_does_not_block_persistence(monkeypatch, fake_client):
    def boom(*_args, **_kwargs):
        raise RuntimeError("query backend unavailable")

    monkeypatch.setattr(ledger, "_find_exact_duplicate", boom)
    result = ledger.insert_prediction(ledger.PredictionRow(**_publishable_kwargs()))
    assert result["controlling_specialist"] == "wow.mlb-pitcher-strikeouts-specialist"
    assert len(fake_client.tables["wow_predictions"].rows) == 1
