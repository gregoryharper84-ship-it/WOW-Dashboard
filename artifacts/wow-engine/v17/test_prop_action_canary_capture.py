from types import SimpleNamespace

import v17.prop_action_canary_capture as subject
from v17.detailed_evidence_install import DetailedPickRequestBatch, DetailedPickRequestRow
from v17.prop_production_registration import ReviewedCertificationRelease, _release_key


PREDICTION_ID = "11111111-1111-1111-1111-111111111111"


class _Query:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.mode = "select"
        self.payload = None
        self.filters = {}

    def select(self, *args, **kwargs):
        self.mode = "select"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, value):
        return self

    def upsert(self, payload, **kwargs):
        self.mode = "upsert"
        self.payload = dict(payload)
        return self

    def execute(self):
        if self.name == "wow_predictions" and self.mode == "select":
            row = dict(self.db.prediction)
            if self.filters.get("prediction_id") == row["prediction_id"]:
                return SimpleNamespace(data=[row])
            return SimpleNamespace(data=[])
        if self.name == "wow_prop_action_canary_receipts" and self.mode == "upsert":
            self.db.receipts.append(dict(self.payload))
            return SimpleNamespace(data=[dict(self.payload)])
        raise AssertionError((self.name, self.mode))


class _DB:
    def __init__(self, prediction):
        self.prediction = prediction
        self.receipts = []

    def table(self, name):
        return _Query(self, name)


def _batch():
    return DetailedPickRequestBatch(
        request_id="canary-request-1",
        rows=[
            DetailedPickRequestRow(
                row_key="row-1",
                event_id="MLB:123",
                event_start_time="2026-09-18T00:00:00+00:00",
                sport="MLB",
                player="Test Pitcher",
                stat_type="PITCHER_STRIKEOUTS",
                line=5.5,
                direction="MORE",
            )
        ],
    )


def _prediction():
    return {
        "prediction_id": PREDICTION_ID,
        "sport": "MLB",
        "stat_type": "PITCHER_STRIKEOUTS",
        "feature_schema_version": "PROP_FEATURES_V1",
        "model_family": "SO_MODEL",
        "model_artifact_version": "SO_ARTIFACT_V1",
        "model_artifact_checksum": "sha256:abc",
        "calibration_version": "SO_CAL_V1",
        "raw_model_probability": 0.67,
        "calibrated_probability": 0.64,
        "calibrated_probability_lower_bound": 0.58,
        "can_execute": False,
    }


def _release():
    return ReviewedCertificationRelease(
        sport="MLB",
        stat_type="PITCHER_STRIKEOUTS",
        feature_schema_version="PROP_FEATURES_V1",
        model_family="SO_MODEL",
        model_artifact_version="SO_ARTIFACT_V1",
        artifact_checksum="sha256:abc",
        calibrator_version="SO_CAL_V1",
        calibration_evidence_hash="evidence-hash-1",
        certification_id="CERT-FORWARD-001",
        certification_review_status="APPROVED",
        deterministic_replay_ready=True,
        source_provenance_ready=True,
        can_execute=False,
    )


def _result():
    return {
        "can_execute": False,
        "reconciliation_pass": True,
        "rows": [
            {
                "row_key": "row-1",
                "terminal_status": "COMPLETED",
                "result": {"prediction": {"prediction_id": PREDICTION_ID}},
            }
        ],
    }


def test_no_reviewed_release_means_no_canary(monkeypatch):
    monkeypatch.setattr(subject, "REVIEWED_CERTIFICATION_RELEASES", {})
    db = _DB(_prediction())
    audit = subject.capture_action_canary_receipts(db=db, batch=_batch(), result=_result())
    assert audit["status"] == "NO_REVIEWED_CERTIFICATION_RELEASES"
    assert db.receipts == []


def test_real_reconciled_persisted_prediction_writes_exact_release_canary(monkeypatch):
    release = _release()
    key = _release_key("MLB", "PITCHER_STRIKEOUTS", "SO_ARTIFACT_V1", "sha256:abc")
    monkeypatch.setattr(subject, "REVIEWED_CERTIFICATION_RELEASES", {key: release})
    db = _DB(_prediction())

    audit = subject.capture_action_canary_receipts(db=db, batch=_batch(), result=_result())

    assert audit["status"] == "PASS"
    assert audit["eligible_rows"] == 1
    assert audit["persisted_rows"] == 1
    assert len(db.receipts) == 1
    receipt = db.receipts[0]
    assert receipt["action_operation_id"] == "scoreWowPickRequest"
    assert receipt["prediction_id"] == PREDICTION_ID
    assert receipt["certification_id"] == "CERT-FORWARD-001"
    assert receipt["calibration_evidence_hash"] == "evidence-hash-1"
    assert receipt["calibrated_lower_bound"] == 0.58
    assert receipt["reconciliation_status"] == "PASS"
    assert receipt["immutable_receipt_hash"]
    assert receipt["can_execute"] is False


def test_unreconciled_action_response_cannot_write_canary(monkeypatch):
    release = _release()
    key = _release_key("MLB", "PITCHER_STRIKEOUTS", "SO_ARTIFACT_V1", "sha256:abc")
    monkeypatch.setattr(subject, "REVIEWED_CERTIFICATION_RELEASES", {key: release})
    db = _DB(_prediction())
    result = _result()
    result["reconciliation_pass"] = False

    audit = subject.capture_action_canary_receipts(db=db, batch=_batch(), result=result)

    assert audit["status"] == "NOT_ELIGIBLE_RECONCILIATION"
    assert db.receipts == []


def test_identity_mismatch_cannot_write_canary(monkeypatch):
    release = _release()
    key = _release_key("MLB", "PITCHER_STRIKEOUTS", "SO_ARTIFACT_V1", "sha256:abc")
    monkeypatch.setattr(subject, "REVIEWED_CERTIFICATION_RELEASES", {key: release})
    prediction = _prediction()
    prediction["calibration_version"] = "WRONG_CALIBRATOR"
    db = _DB(prediction)

    audit = subject.capture_action_canary_receipts(db=db, batch=_batch(), result=_result())

    assert audit["eligible_rows"] == 1
    assert audit["persisted_rows"] == 0
    assert "CANARY_REVIEWED_RELEASE_IDENTITY_MISMATCH" in audit["blockers"]
    assert db.receipts == []
