from types import SimpleNamespace

import ledger
from ledger import PredictionRow
from simulation import MIN_SIMULATION_DRAWS


class Query:
    def __init__(self, client, table):
        self.client = client
        self.table = table
        self.mode = "select"
        self.payload = None
        self.filters = {}
        self.limit_n = None

    def select(self, *_args, **_kwargs):
        self.mode = "select"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, value):
        self.limit_n = int(value)
        return self

    def insert(self, payload):
        self.mode = "insert"
        self.payload = dict(payload)
        return self

    def execute(self):
        rows = self.client.tables.setdefault(self.table, [])
        if self.mode == "select":
            data = [
                dict(row)
                for row in rows
                if all(row.get(k) == v for k, v in self.filters.items())
            ]
            if self.limit_n is not None:
                data = data[: self.limit_n]
            return SimpleNamespace(data=data)
        if any(row.get("prediction_id") == self.payload.get("prediction_id") for row in rows):
            raise RuntimeError("duplicate primary key")
        rows.append(dict(self.payload))
        return SimpleNamespace(data=[dict(self.payload)])


class Client:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return Query(self, name)


def valid_row(*, model_timestamp="2026-09-20T20:00:00+00:00", seed=7):
    return PredictionRow(
        event_id="NFL:DET:BAL:20260921",
        event_start_time="2026-09-21T23:00:00+00:00",
        sport="NFL",
        market_type="LEGACY_TEST",
        stat_type="RECEIVING_YARDS",
        line=42.5,
        direction="MORE",
        source_snapshot_id="11111111-1111-1111-1111-111111111111",
        model_timestamp=model_timestamp,
        player="Test Player",
        regime_model_version="LEGACY_TEST_V1",
        regime_probability_sum=1.0,
        simulation_seed=seed,
        simulation_draws=MIN_SIMULATION_DRAWS,
        raw_model_probability=0.64,
        independent_model_probability=0.64,
        calibration_status="PRECALIBRATION_SHRINKAGE",
        calibration_method="PRECALIBRATION_SHRINKAGE",
        calibrated_probability=0.61,
        calibrated_probability_lower_bound=0.55,
        calibrated_probability_upper_bound=0.67,
        money_lane_status="PAYOUT_UNRESOLVED",
    )


def test_retry_reuses_one_immutable_prediction_row(monkeypatch):
    client = Client()
    monkeypatch.setattr(ledger, "get_client", lambda: client)

    first = ledger.insert_prediction(valid_row())
    retry = ledger.insert_prediction(
        valid_row(model_timestamp="2026-09-20T20:00:05+00:00")
    )

    assert first["prediction_id"] == retry["prediction_id"]
    assert len(client.tables["wow_predictions"]) == 1


def test_material_model_change_gets_new_prediction_identity(monkeypatch):
    client = Client()
    monkeypatch.setattr(ledger, "get_client", lambda: client)

    first = ledger.insert_prediction(valid_row(seed=7))
    changed = ledger.insert_prediction(valid_row(seed=8))

    assert first["prediction_id"] != changed["prediction_id"]
    assert len(client.tables["wow_predictions"]) == 2


def test_prediction_identity_never_depends_on_money_lane_or_retry_clock():
    left = valid_row(model_timestamp="2026-09-20T20:00:00+00:00")
    right = valid_row(model_timestamp="2026-09-20T20:01:00+00:00")
    left.money_lane_status = "PAYOUT_UNRESOLVED"
    right.money_lane_status = "RESOLVED"

    assert ledger.prediction_id_for(left) == ledger.prediction_id_for(right)
