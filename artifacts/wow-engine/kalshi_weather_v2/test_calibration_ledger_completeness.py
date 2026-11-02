"""Fail-closed calibration: unreadable and server-truncated data never fit."""
from types import SimpleNamespace

from kalshi_weather_v2.shadow_cohort import build_calibration_report


class Query:
    def __init__(self, rows, *, exact_count=None, broken=False):
        self.rows = rows
        self.exact_count = exact_count
        self.broken = broken

    def select(self, *_args, **_kwargs):
        return self

    def limit(self, _n):
        return self

    def execute(self):
        if self.broken:
            raise OSError("database unavailable")
        return SimpleNamespace(data=self.rows, count=self.exact_count)


class Client:
    def __init__(self, *, predictions=(), outcomes=(), prediction_count=0,
                 outcome_count=0, broken_table=None):
        self.predictions = list(predictions)
        self.outcomes = list(outcomes)
        self.prediction_count = prediction_count
        self.outcome_count = outcome_count
        self.broken_table = broken_table

    def table(self, name):
        is_prediction = name.endswith("predictions")
        return Query(
            self.predictions if is_prediction else self.outcomes,
            exact_count=self.prediction_count if is_prediction else self.outcome_count,
            broken=name if name == self.broken_table else False,
        )


def test_empty_ledgers_produce_complete_empty_report():
    report = build_calibration_report(client=Client())
    assert report["status"] == "EMPIRICAL_CALIBRATION_REPORT"
    assert report["dataset_integrity"]["source_ledgers_complete"] is True
    assert report["dataset_integrity"]["total_predictions"] == 0


def test_prediction_read_exception_cannot_produce_fake_complete_report():
    report = build_calibration_report(
        client=Client(broken_table="wow_kalshi_weather_predictions")
    )
    assert report["status"] == "EMPIRICAL_CALIBRATION_REPORT_INCOMPLETE"
    assert report["dataset_integrity"]["source_ledger_error_code"] == "CALIBRATION_LEDGER_READ_FAILED"
    assert report["groups"] == []
    assert report["capability_promotion_allowed"] is False


def test_outcome_read_exception_cannot_relabel_settled_as_unsettled():
    report = build_calibration_report(
        client=Client(broken_table="wow_kalshi_weather_outcomes")
    )
    assert report["status"] == "EMPIRICAL_CALIBRATION_REPORT_INCOMPLETE"
    assert report["groups"] == []


def test_postgrest_server_cap_detected_by_exact_count():
    report = build_calibration_report(
        client=Client(predictions=[{"prediction_id": "x"}], prediction_count=1001)
    )
    assert report["status"] == "EMPIRICAL_CALIBRATION_REPORT_INCOMPLETE"
    assert report["dataset_integrity"]["source_ledger_error_code"] == "CALIBRATION_LEDGER_TRUNCATED"


def test_missing_exact_count_is_not_proof_of_completeness():
    report = build_calibration_report(
        client=Client(prediction_count=None)
    )
    assert report["status"] == "EMPIRICAL_CALIBRATION_REPORT_INCOMPLETE"
    assert report["dataset_integrity"]["source_ledger_error_code"] == "CALIBRATION_LEDGER_COUNT_UNAVAILABLE"
