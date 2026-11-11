"""Governed MLB calibrator evidence producer.

These tests fit the real ratified Phase B calibrator through the governed receipt
join and assert the persisted evidence row carries its own proofs: provenance and
as-of custody re-read from receipts rather than supplied, an identity-bound
calibrator checksum, the fitted coefficients with their digest, the exact receipt
pairs used, and honest holdout metrics scored against the uncalibrated incumbent.
No governed row is certified, no verification flag is written and no probability
is published.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from v17 import mlb_calibrator_evidence_producer as producer
from v17.mlb_pregame_evidence_ingest import canonical_sha256

ARTIFACT_SHA = "a" * 64
LAST_SETTLEMENT = datetime(2026, 11, 1, 23, 0, tzinfo=timezone.utc)
NOW = "2026-11-09T00:00:00+00:00"
DECISION_AT = datetime(2026, 11, 9, tzinfo=timezone.utc)


class _Table:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self._rows = db.tables.setdefault(name, [])
        self._matched = list(self._rows)
        self._mode = "select"
        self._payload = None

    def select(self, *_args, **_kwargs):
        self._mode = "select"
        return self

    def eq(self, column, value):
        wanted = str(value).strip().lower()
        self._matched = [
            row
            for row in self._matched
            if str(row.get(column)).strip().lower() == wanted
        ]
        return self

    def limit(self, count):
        self._matched = self._matched[:count]
        return self

    def insert(self, payload):
        self._mode = "insert"
        self._payload = dict(payload)
        return self

    def execute(self):
        if self._mode == "insert":
            row = {**self._payload, "id": len(self._rows) + 1}
            self._rows.append(row)
            self.db.writes.append((self.name, "insert"))
            return SimpleNamespace(data=[dict(row)])
        return SimpleNamespace(data=[dict(row) for row in self._matched])


class _Db:
    def __init__(self):
        self.tables = {producer.TABLE: []}
        self.writes = []

    def table(self, name):
        return _Table(self, name)


def _artifact(**overrides):
    row = {
        "artifact_id": "artifact-test",
        "sport": "MLB",
        "model_family": "MLB_EVENT_WIN_PROBABILITY",
        "model_artifact_version": "MLB_EVENT_V1",
        "feature_schema_version": "MLB_EVENT_FEATURES_V1",
        "artifact_checksum": ARTIFACT_SHA,
        "lifecycle_state": "CHAMPION",
        "active": True,
        "promoted": True,
    }
    row.update(overrides)
    return row


def _fixture(*, days=30, per_day=40, seed=11):
    """Observations as identities only, plus the governed receipts behind them.

    Each prediction receipt is frozen an hour before first pitch and persisted at
    the same instant, each settlement receipt lands that evening, and both were
    recorded well before the decision cutoff.
    """
    rng = random.Random(seed)
    observations: list[dict[str, str]] = []
    predictions: dict[str, dict] = {}
    settlements: dict[str, dict] = {}
    for day in range(days):
        settled = LAST_SETTLEMENT - timedelta(days=days - 1 - day)
        start = settled.replace(hour=19, minute=0, second=0)
        for index in range(per_day):
            event_id = f"MLB:{day}-{index}"
            prediction_id = f"pred-{day}-{index}"
            settlement_id = f"grade-{day}-{index}"
            raw = rng.uniform(0.25, 0.75)
            frozen = (start - timedelta(hours=1)).isoformat()
            observations.append(
                {
                    "event_id": event_id,
                    "prediction_receipt_id": prediction_id,
                    "settlement_receipt_id": settlement_id,
                }
            )
            predictions[prediction_id] = {
                "official_event_id": event_id,
                "raw_home_probability": raw,
                "model_timestamp": frozen,
                "created_at": frozen,
                "prelock_recorded_at": frozen,
                "event_start_time": start.isoformat(),
                "model_artifact_checksum": ARTIFACT_SHA,
            }
            settlements[settlement_id] = {
                "official_event_id": event_id,
                "outcome": 1 if rng.random() < raw else 0,
                "outcome_timestamp": settled.isoformat(),
                "grade_timestamp": settled.isoformat(),
            }
    return observations, predictions, settlements


def _resolver(predictions, settlements):
    """Stand-in for the governed join: values come from receipts, never rows."""

    def resolve(observation):
        event_id = observation.get("event_id")
        prediction = predictions.get(observation.get("prediction_receipt_id"))
        settlement = settlements.get(observation.get("settlement_receipt_id"))
        if prediction is None:
            return None, (f"PREDICTION_RECEIPT_NOT_FOUND:{event_id}",)
        if settlement is None:
            return None, (f"SETTLEMENT_RECEIPT_NOT_FOUND:{event_id}",)
        if prediction.get("official_event_id") != event_id:
            return None, (f"PREDICTION_EVENT_MISMATCH:{event_id}",)
        if settlement.get("official_event_id") != event_id:
            return None, (f"SETTLEMENT_EVENT_MISMATCH:{event_id}",)
        return (
            {
                "event_id": event_id,
                "prediction_receipt_id": observation.get("prediction_receipt_id"),
                "settlement_receipt_id": observation.get("settlement_receipt_id"),
                "raw_probability": prediction["raw_home_probability"],
                "prediction_at": prediction["model_timestamp"],
                "prediction_recorded_at": prediction.get("created_at"),
                "event_start_time": prediction["event_start_time"],
                "prelock_recorded_at": prediction.get("prelock_recorded_at"),
                "settled_at": settlement["outcome_timestamp"],
                "settlement_recorded_at": settlement.get("grade_timestamp"),
                "outcome": settlement["outcome"],
                "model_artifact_checksum": prediction.get(
                    "model_artifact_checksum", ARTIFACT_SHA
                ),
            },
            (),
        )

    return resolve


def _produce(db, fixture, **overrides):
    observations, predictions, settlements = fixture
    kwargs = {
        "artifact": _artifact(),
        "settled_rows": observations,
        "calibrator_id": "cal-mlb-1",
        "now": NOW,
        "resolve": _resolver(predictions, settlements),
    }
    kwargs.update(overrides)
    return producer.produce_calibrator_evidence(db, **kwargs)


def test_producer_fits_and_persists_one_exact_version_row():
    db = _Db()

    receipt = _produce(db, _fixture())

    assert receipt["status"] == producer.PERSISTED
    stored = db.tables[producer.TABLE]
    assert len(stored) == 1
    row = stored[0]
    assert row["calibrator_id"] == "cal-mlb-1"
    assert row["sport"] == "MLB"
    assert row["model_family"] == "MLB_EVENT_WIN_PROBABILITY"
    assert row["model_artifact_version"] == "MLB_EVENT_V1"
    assert row["feature_schema_version"] == "MLB_EVENT_FEATURES_V1"
    assert row["model_artifact_checksum"] == ARTIFACT_SHA
    assert row["calibration_method"] == "PLATT_TIME_SPLIT_V1"
    # A holdout diagnostic, never a claim to the certified bounds method.
    assert row["bounds_method_version"] == producer.HOLDOUT_BOUNDS_METHOD
    assert row["bounds_method_version"] != "PREDICTIVE_BOUNDS_V1"
    assert producer.BOUNDS_BOOTSTRAP_REALIZATIONS == 2000
    assert row["calibration_training_n"] == 880
    assert row["holdout_n"] == 320
    assert row["holdout_n"] >= producer.HOLDOUT_MIN_N
    assert row["holdout_overlap_rows"] == 0
    assert 0.0 <= row["brier"] <= 1.0
    assert row["log_loss"] >= 0.0
    assert -1.0 <= row["calibration_bias"] <= 1.0
    for field in (
        "calibrator_checksum",
        "calibration_coefficients_sha256",
        "calibration_receipt_manifest_sha256",
        "calibration_cohort_sha256",
        "holdout_evidence_sha256",
    ):
        assert len(row[field]) == 64
    coefficients = row["calibration_coefficients"]
    assert set(coefficients) == {"a", "b"}
    assert row["calibration_coefficients_sha256"] == canonical_sha256(coefficients)
    # The exact receipt pairs are retrievable, not only digested.
    manifest = row["calibration_receipt_manifest"]
    assert manifest["manifest_version"] == producer.RECEIPT_MANIFEST_VERSION
    assert len(manifest["fit"]) == row["calibration_training_n"]
    assert len(manifest["holdout"]) == row["holdout_n"]
    assert set(manifest["fit"][0]) == {
        "event_id",
        "prediction_receipt_id",
        "settlement_receipt_id",
    }
    assert all(
        pair["prediction_receipt_id"] and pair["settlement_receipt_id"]
        for pair in manifest["fit"] + manifest["holdout"]
    )
    assert row["calibration_receipt_manifest_sha256"] == canonical_sha256(manifest)
    assert row["calibrator_checksum"] not in {
        row["calibration_cohort_sha256"],
        row["holdout_evidence_sha256"],
        row["calibration_coefficients_sha256"],
        row["calibration_receipt_manifest_sha256"],
    }
    assert 0.0 <= row["holdout_incumbent_brier"] <= 1.0
    assert row["holdout_brier_delta"] == row["brier"] - row["holdout_incumbent_brier"]
    assert row["holdout_log_loss_delta"] == (
        row["log_loss"] - row["holdout_incumbent_log_loss"]
    )
    assert isinstance(row["holdout_calibrated_worse_n"], int)
    assert 0 <= row["holdout_calibrated_worse_n"] <= row["holdout_n"]
    assert row["holdout_bounds_lower_mean"] <= row["holdout_bounds_upper_mean"]
    assert -1.0 <= row["holdout_lower_bound_reliability_margin"] <= 1.0
    assert "chronological_replay_verified" not in row
    assert "calibrated_lower_bound_verified" not in row
    assert receipt["probability_written"] is False
    assert receipt["verification_flag_written"] is False
    assert receipt["can_execute"] is False


def test_the_holdout_is_strictly_later_than_the_fit_cohort():
    observations, predictions, settlements = _fixture()
    normalized, blockers = producer.normalize_settled_rows(
        observations,
        decision_at=DECISION_AT,
        artifact_checksum=ARTIFACT_SHA,
        resolve=_resolver(predictions, settlements),
    )
    assert blockers == ()
    fit, holdout = producer.partition_settled_rows(normalized)

    assert fit and holdout
    assert max(row["settled_at"] for row in fit) < min(
        row["settled_at"] for row in holdout
    )
    assert not {row["event_id"] for row in fit} & {row["event_id"] for row in holdout}
    assert all(row["prediction_receipt_id"] for row in fit + holdout)


def test_a_different_receipt_set_with_the_same_numbers_changes_the_evidence():
    baseline, alt = _Db(), _Db()
    observations, predictions, settlements = _fixture()
    relabelled = observations
    for observation in relabelled:
        original = predictions[observation["prediction_receipt_id"]]
        new_id = "alt-" + observation["prediction_receipt_id"]
        predictions[new_id] = dict(original)
        observation["prediction_receipt_id"] = new_id

    first = _produce(baseline, _fixture())
    second = _produce(alt, (relabelled, predictions, settlements))

    assert first["status"] == producer.PERSISTED
    assert second["status"] == producer.PERSISTED
    assert (
        baseline.tables[producer.TABLE][0]["calibrator_checksum"]
        != alt.tables[producer.TABLE][0]["calibrator_checksum"]
    )
    assert (
        baseline.tables[producer.TABLE][0]["calibration_cohort_sha256"]
        != alt.tables[producer.TABLE][0]["calibration_cohort_sha256"]
    )
    assert (
        baseline.tables[producer.TABLE][0]["calibration_receipt_manifest_sha256"]
        != alt.tables[producer.TABLE][0]["calibration_receipt_manifest_sha256"]
    )


def test_a_prediction_record_persisted_at_first_pitch_is_refused():
    db = _Db()
    observations, predictions, settlements = _fixture()
    first = predictions[observations[0]["prediction_receipt_id"]]
    first["created_at"] = first["event_start_time"]

    receipt = _produce(db, (observations, predictions, settlements))

    assert receipt["status"] == producer.REFUSED
    assert any("PREDICTION_RECORD_POSTDATES_START" in item for item in receipt["blockers"])
    assert db.tables[producer.TABLE] == []


# A prediction receipt persisted after the decision cutoff is already refused by
# the earlier first-pitch check (the cutoff is always after settlement, hence
# after the start), so only the settlement-record cutoff needs its own case.
def test_a_settlement_record_persisted_after_the_decision_is_refused():
    db = _Db()
    observations, predictions, settlements = _fixture()
    settlements[observations[0]["settlement_receipt_id"]]["grade_timestamp"] = (
        "2026-11-10T00:00:00+00:00"
    )

    receipt = _produce(db, (observations, predictions, settlements))

    assert receipt["status"] == producer.REFUSED
    assert any(
        "SETTLEMENT_RECORD_POSTDATES_DECISION" in item for item in receipt["blockers"]
    ), receipt["blockers"]


def test_a_forecast_frozen_after_it_was_persisted_is_refused():
    db = _Db()
    observations, predictions, settlements = _fixture()
    prediction = predictions[observations[0]["prediction_receipt_id"]]
    prediction["model_timestamp"] = "2026-10-01T12:00:00+00:00"
    prediction["created_at"] = "2026-10-01T11:00:00+00:00"

    receipt = _produce(db, (observations, predictions, settlements))

    assert receipt["status"] == producer.REFUSED
    assert any(
        "PREDICTION_RECORD_PRECEDES_FROZEN" in item for item in receipt["blockers"]
    ), receipt["blockers"]


def test_an_observation_without_resolvable_receipts_is_refused():
    row, blockers = producer.build_calibrator_row(
        artifact=_artifact(),
        settled_rows=[
            {
                "event_id": "MLB:0-0",
                "prediction_receipt_id": "pred-0-0",
                "settlement_receipt_id": "grade-0-0",
            }
        ],
        calibrator_id="cal-mlb-1",
        now=NOW,
        resolve=None,
    )
    assert row is None
    assert blockers == ("MLB_CALIBRATOR_EVIDENCE_PROVENANCE_UNVERIFIABLE",)

    db = _Db()
    observations, _, _ = _fixture(days=1, per_day=1)
    receipt = producer.produce_calibrator_evidence(
        db,
        artifact=_artifact(),
        settled_rows=observations,
        calibrator_id="cal-mlb-1",
        now=NOW,
    )
    assert receipt["status"] == producer.REFUSED
    assert any(
        "PRELOCK_RECEIPT_MISSING" in item for item in receipt["blockers"]
    ), receipt["blockers"]
    assert db.tables[producer.TABLE] == []


def test_a_receipt_whose_forecast_was_written_at_first_pitch_is_refused():
    db = _Db()
    observations, predictions, settlements = _fixture()
    first = predictions[observations[0]["prediction_receipt_id"]]
    frozen = first["event_start_time"]
    first["model_timestamp"] = frozen
    first["created_at"] = frozen

    receipt = _produce(db, (observations, predictions, settlements))

    assert receipt["status"] == producer.REFUSED
    joined = " ".join(receipt["blockers"])
    assert "FORECAST_NOT_PREGAME" in joined or "POSTDATES_START" in joined
    assert db.tables[producer.TABLE] == []


def test_a_supplied_value_that_contradicts_its_receipt_is_refused():
    db = _Db()
    observations, predictions, settlements = _fixture()
    observations[0] = {**observations[0], "raw_probability": 0.99}

    receipt = _produce(db, (observations, predictions, settlements))

    assert receipt["status"] == producer.REFUSED
    assert any(
        "RECEIPT_DISAGREES:raw_probability" in item for item in receipt["blockers"]
    ), receipt["blockers"]


def test_a_receipt_from_another_artifact_is_refused():
    db = _Db()
    observations, predictions, settlements = _fixture()
    predictions[observations[0]["prediction_receipt_id"]][
        "model_artifact_checksum"
    ] = "b" * 64

    receipt = _produce(db, (observations, predictions, settlements))

    assert receipt["status"] == producer.REFUSED
    assert any("ARTIFACT_PROVENANCE_MISMATCH" in item for item in receipt["blockers"])
    assert db.tables[producer.TABLE] == []


def test_a_settlement_before_first_pitch_is_refused():
    db = _Db()
    observations, predictions, settlements = _fixture()
    prediction = predictions[observations[0]["prediction_receipt_id"]]
    start = datetime.fromisoformat(prediction["event_start_time"])
    settlements[observations[0]["settlement_receipt_id"]][
        "outcome_timestamp"
    ] = (start - timedelta(hours=2)).isoformat()

    receipt = _produce(db, (observations, predictions, settlements))

    assert receipt["status"] == producer.REFUSED
    assert any("SETTLED_BEFORE_START" in item for item in receipt["blockers"])


def test_an_empty_cohort_is_a_typed_refusal_not_a_crash():
    db = _Db()

    receipt = _produce(db, ([], {}, {}))

    assert receipt["status"] == producer.REFUSED
    assert receipt["blockers"] == ["MLB_CALIBRATOR_EVIDENCE_COHORT_EMPTY"]
    assert db.tables[producer.TABLE] == []
    assert producer.partition_settled_rows([]) == ([], [])


def test_a_holdout_below_the_fixed_floor_is_refused():
    db = _Db()
    fixture = _fixture(days=22, per_day=33)  # holdout = 6 days x 33 = 198

    receipt = _produce(db, fixture)

    assert receipt["status"] == producer.REFUSED
    assert any("HOLDOUT_INSUFFICIENT" in item for item in receipt["blockers"])
    assert db.tables[producer.TABLE] == []


def test_replay_is_idempotent():
    db = _Db()

    assert _produce(db, _fixture())["status"] == producer.PERSISTED
    second = _produce(db, _fixture())

    assert second["status"] == producer.ALREADY_CURRENT
    assert len(db.tables[producer.TABLE]) == 1


def test_replay_under_the_same_id_with_different_coefficients_is_a_conflict():
    db = _Db()

    first = _produce(db, _fixture(seed=11))
    second = _produce(db, _fixture(seed=12))

    assert first["status"] == producer.PERSISTED
    assert second["status"] == producer.REFUSED
    assert any("IDENTITY_CONFLICT" in item for item in second["blockers"])
    stored = db.tables[producer.TABLE]
    assert len(stored) == 1
    assert stored[0]["calibrator_checksum"] == first["calibrator_checksum"]


def test_the_checksum_binds_calibrator_id_and_artifact_identity():
    by_id = _Db()
    other_id = _Db()

    _produce(by_id, _fixture(), calibrator_id="cal-mlb-1")
    _produce(other_id, _fixture(), calibrator_id="cal-mlb-2")

    assert (
        by_id.tables[producer.TABLE][0]["calibrator_checksum"]
        != other_id.tables[producer.TABLE][0]["calibrator_checksum"]
    )


def test_two_different_cohorts_produce_different_calibrator_checksums():
    first, second = _Db(), _Db()

    _produce(first, _fixture(seed=11))
    _produce(second, _fixture(seed=12))

    assert (
        first.tables[producer.TABLE][0]["calibrator_checksum"]
        != second.tables[producer.TABLE][0]["calibrator_checksum"]
    )


def test_a_duplicate_settled_identity_is_a_conflict():
    db = _Db()
    observations, predictions, settlements = _fixture()

    receipt = _produce(
        db, ([*observations, dict(observations[0])], predictions, settlements)
    )

    assert receipt["status"] == producer.REFUSED
    assert any("DUPLICATE_SETTLED_ROW" in item for item in receipt["blockers"])


def test_an_unsettled_observation_is_refused():
    db = _Db()
    observations, predictions, settlements = _fixture()
    settlements[observations[-1]["settlement_receipt_id"]][
        "outcome_timestamp"
    ] = "2026-11-10T00:00:00+00:00"

    receipt = _produce(db, (observations, predictions, settlements))

    assert receipt["status"] == producer.REFUSED
    assert any("NOT_SETTLED_AT_DECISION" in item for item in receipt["blockers"])


def test_an_invalid_receipted_probability_or_outcome_is_refused():
    db = _Db()
    observations, predictions, settlements = _fixture()
    predictions[observations[0]["prediction_receipt_id"]]["raw_home_probability"] = 0.0
    settlements[observations[1]["settlement_receipt_id"]]["outcome"] = 2

    receipt = _produce(db, (observations, predictions, settlements))

    assert receipt["status"] == producer.REFUSED
    joined = " ".join(receipt["blockers"])
    assert "RAW_PROBABILITY_INVALID" in joined
    assert "OUTCOME_INVALID" in joined


def test_an_insufficient_fit_cohort_is_refused():
    db = _Db()

    receipt = _produce(db, _fixture(days=5, per_day=30))

    assert receipt["status"] == producer.REFUSED
    assert any("FIT_COHORT_INSUFFICIENT" in item for item in receipt["blockers"])
    assert db.writes == []


def test_a_fit_failure_is_typed_and_never_papered_over():
    db = _Db()

    # 6 days -> 4 fit days (4 folds, under the ratified minimum of 6) and a
    # 200-row holdout, so the fitter itself is what refuses.
    receipt = _produce(db, _fixture(days=6, per_day=100))

    assert receipt["status"] == producer.REFUSED
    assert any("FIT_FAILED" in item for item in receipt["blockers"])
    assert db.tables[producer.TABLE] == []


def test_a_holdout_the_calibration_worsens_is_refused():
    # The rule itself, at its boundary: a material regression and an optimistic
    # declared floor are refusals, not notes attached to a passing row.
    assert producer._comparison_blockers(0.30, 0.25, 0.02) != ()
    assert producer._comparison_blockers(0.252, 0.25, 0.02) == ()
    assert producer._comparison_blockers(0.24, 0.25, 0.02) == ()
    unreliable = producer._comparison_blockers(
        0.24, 0.25, -producer.BOUNDS_RELIABILITY_TOLERANCE - 0.01
    )
    assert any("LOWER_BOUND_UNRELIABLE" in item for item in unreliable)


def test_an_uncertified_artifact_is_not_evidence_authority():
    db = _Db()

    receipt = _produce(db, _fixture(), artifact=_artifact(lifecycle_state="CANDIDATE"))

    assert receipt["status"] == producer.REFUSED
    assert any("ARTIFACT_NOT_CERTIFIED" in item for item in receipt["blockers"])
    assert db.tables[producer.TABLE] == []


def test_a_missing_database_is_a_typed_refusal():
    observations, predictions, settlements = _fixture()

    receipt = producer.produce_calibrator_evidence(
        None,
        artifact=_artifact(),
        settled_rows=observations,
        calibrator_id="cal-mlb-1",
        now=NOW,
        resolve=_resolver(predictions, settlements),
    )

    assert receipt["status"] == producer.REFUSED
    assert receipt["blockers"] == ["MLB_CALIBRATOR_EVIDENCE_DB_UNAVAILABLE"]
    assert receipt["can_execute"] is False


def test_missing_prelock_custody_is_refused():
    observations, predictions, settlements = _fixture()
    predictions[observations[0]["prediction_receipt_id"]].pop("prelock_recorded_at")
    receipt = _produce(_Db(), (observations, predictions, settlements))
    assert any("PRELOCK_RECORD_TIMESTAMP_MISSING" in b for b in receipt["blockers"])


def test_missing_artifact_id_is_refused():
    receipt = _produce(_Db(), _fixture(), artifact=_artifact(artifact_id=""))
    assert "MLB_CALIBRATOR_EVIDENCE_ARTIFACT_INCOMPLETE:artifact_id" in receipt["blockers"]
