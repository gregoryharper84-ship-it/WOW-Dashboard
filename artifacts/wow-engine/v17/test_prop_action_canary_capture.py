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
            if getattr(self.db, "prediction_error", False):
                raise ConnectionError("simulated prediction-store read outage")
            row = dict(self.db.prediction)
            if self.filters.get("prediction_id") == row["prediction_id"]:
                return SimpleNamespace(data=[row])
            return SimpleNamespace(data=[])
        if self.name == "wow_prop_action_canary_receipts" and self.mode == "upsert":
            if not getattr(self.db, "drop_writes", False):
                self.db.receipts.append(dict(self.payload))
            return SimpleNamespace(data=[dict(self.payload)])
        if self.name == "wow_prop_action_canary_receipts" and self.mode == "select":
            if getattr(self.db, "readback_error", False):
                raise ConnectionError("simulated receipt-store read outage")
            rows = [dict(row) for row in self.db.receipts
                    if row["immutable_receipt_hash"] == self.filters.get("immutable_receipt_hash")]
            if rows and getattr(self.db, "corrupt_readback", False):
                rows[0]["certification_id"] = "WRONG_CERTIFICATION"
            if rows and getattr(self.db, "corrupt_probability", False):
                rows[0]["calibrated_lower_bound"] = 0.99
            return SimpleNamespace(data=rows)
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


def test_real_reconciled_persisted_prediction_without_row_can_execute_writes_exact_release_canary(monkeypatch):
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


def test_canary_upsert_without_durable_readback_must_not_count_success(monkeypatch):
    release = _release()
    key = _release_key("MLB", "PITCHER_STRIKEOUTS", "SO_ARTIFACT_V1", "sha256:abc")
    monkeypatch.setattr(subject, "REVIEWED_CERTIFICATION_RELEASES", {key: release})
    db = _DB(_prediction())
    db.drop_writes = True

    audit = subject.capture_action_canary_receipts(db=db, batch=_batch(), result=_result())
    assert audit["persisted_rows"] == 0
    assert audit["status"] == "COMPLETED_WITH_BLOCKERS"
    assert "CANARY_RECEIPT_READBACK_MISSING" in audit["blockers"]
    assert db.receipts == []


def test_canary_readback_wrong_certification_must_not_count_success(monkeypatch):
    release = _release()
    key = _release_key("MLB", "PITCHER_STRIKEOUTS", "SO_ARTIFACT_V1", "sha256:abc")
    monkeypatch.setattr(subject, "REVIEWED_CERTIFICATION_RELEASES", {key: release})
    db = _DB(_prediction())
    db.corrupt_readback = True

    audit = subject.capture_action_canary_receipts(db=db, batch=_batch(), result=_result())
    assert audit["persisted_rows"] == 0
    assert audit["status"] == "COMPLETED_WITH_BLOCKERS"
    assert "CANARY_RECEIPT_READBACK_MISMATCH" in audit["blockers"]


def test_canary_readback_transport_exception_is_explicit_blocker(monkeypatch):
    release = _release()
    key = _release_key("MLB", "PITCHER_STRIKEOUTS", "SO_ARTIFACT_V1", "sha256:abc")
    monkeypatch.setattr(subject, "REVIEWED_CERTIFICATION_RELEASES", {key: release})
    db = _DB(_prediction())
    db.readback_error = True

    audit = subject.capture_action_canary_receipts(db=db, batch=_batch(), result=_result())
    assert audit["eligible_rows"] == 1
    assert audit["persisted_rows"] == 0
    assert audit["status"] == "COMPLETED_WITH_BLOCKERS"
    assert "CANARY_RECEIPT_READBACK_UNAVAILABLE:ConnectionError" in audit["blockers"]
    assert db.receipts and db.receipts[0]["certification_id"] == "CERT-FORWARD-001"
    assert audit["can_execute"] is False


def test_canary_readback_corrupted_probability_package_is_rejected(monkeypatch):
    release = _release()
    key = _release_key("MLB", "PITCHER_STRIKEOUTS", "SO_ARTIFACT_V1", "sha256:abc")
    monkeypatch.setattr(subject, "REVIEWED_CERTIFICATION_RELEASES", {key: release})
    db = _DB(_prediction())
    db.corrupt_probability = True

    audit = subject.capture_action_canary_receipts(db=db, batch=_batch(), result=_result())
    assert audit["eligible_rows"] == 1
    assert audit["persisted_rows"] == 0
    assert audit["status"] == "COMPLETED_WITH_BLOCKERS"
    assert "CANARY_RECEIPT_READBACK_MISMATCH" in audit["blockers"]
    assert audit["can_execute"] is False


def test_boolean_values_never_become_probability_evidence():
    assert subject._finite_probability(True) is None
    assert subject._finite_probability(False) is None
    assert subject._finite_probability(0.0) == 0.0
    assert subject._finite_probability(1.0) == 1.0


def test_boolean_probability_fields_fail_closed_before_receipt_write(monkeypatch):
    release = _release()
    key = _release_key("MLB", "PITCHER_STRIKEOUTS", "SO_ARTIFACT_V1", "sha256:abc")
    monkeypatch.setattr(subject, "REVIEWED_CERTIFICATION_RELEASES", {key: release})
    for field in ("raw_model_probability", "calibrated_probability", "calibrated_probability_lower_bound"):
        for boolean in (True, False):
            prediction = _prediction()
            prediction[field] = boolean
            db = _DB(prediction)
            audit = subject.capture_action_canary_receipts(db=db, batch=_batch(), result=_result())
            assert audit["eligible_rows"] == 1, (field, boolean, audit)
            assert audit["persisted_rows"] == 0, (field, boolean, audit)
            assert "CANARY_GOVERNED_PROBABILITY_PACKAGE_INVALID" in audit["blockers"]
            assert db.receipts == []
            assert audit["can_execute"] is False


def test_empty_reconciled_action_does_not_false_pass_canary(monkeypatch):
    release = _release()
    key = _release_key("MLB", "PITCHER_STRIKEOUTS", "SO_ARTIFACT_V1", "sha256:abc")
    monkeypatch.setattr(subject, "REVIEWED_CERTIFICATION_RELEASES", {key: release})
    result = _result()
    result["rows"] = []
    db = _DB(_prediction())
    audit = subject.capture_action_canary_receipts(db=db, batch=_batch(), result=result)
    assert audit["status"] == "NO_ELIGIBLE_CANARY_ROWS"
    assert audit["eligible_rows"] == 0
    assert audit["persisted_rows"] == 0
    assert not db.receipts
    assert audit["can_execute"] is False


def test_completed_prediction_without_matching_certified_release_not_a_canary_pass(monkeypatch):
    release = _release()
    # A nonmatching exact artifact must not count as eligible or certified.
    monkeypatch.setattr(
        subject, "REVIEWED_CERTIFICATION_RELEASES",
        {_release_key("NFL", "RECEIVING_YARDS", "OTHER", "sha256:other"): release},
    )
    db = _DB(_prediction())
    audit = subject.capture_action_canary_receipts(db=db, batch=_batch(), result=_result())
    assert audit["status"] == "NO_ELIGIBLE_CANARY_ROWS"
    assert audit["eligible_rows"] == 0
    assert audit["persisted_rows"] == 0
    assert not db.receipts


def test_prediction_store_outage_is_typed_not_reported_as_missing_prediction(monkeypatch):
    """Follow-up to #1713: a wow_predictions read outage must not masquerade as NOT_FOUND."""
    release = _release()
    key = _release_key("MLB", "PITCHER_STRIKEOUTS", "SO_ARTIFACT_V1", "sha256:abc")
    monkeypatch.setattr(subject, "REVIEWED_CERTIFICATION_RELEASES", {key: release})
    db = _DB(_prediction())
    db.prediction_error = True

    audit = subject.capture_action_canary_receipts(db=db, batch=_batch(), result=_result())
    assert "CANARY_PREDICTION_STORE_UNAVAILABLE:ConnectionError" in audit["blockers"]
    assert "CANARY_PERSISTED_PREDICTION_NOT_FOUND" not in audit["blockers"]
    assert audit["status"] == "COMPLETED_WITH_BLOCKERS"
    assert audit["persisted_rows"] == 0
    assert db.receipts == []
    assert audit["can_execute"] is False


def test_genuinely_missing_prediction_stays_not_found(monkeypatch):
    release = _release()
    key = _release_key("MLB", "PITCHER_STRIKEOUTS", "SO_ARTIFACT_V1", "sha256:abc")
    monkeypatch.setattr(subject, "REVIEWED_CERTIFICATION_RELEASES", {key: release})
    prediction = _prediction()
    prediction["prediction_id"] = "22222222-2222-2222-2222-222222222222"
    db = _DB(prediction)

    audit = subject.capture_action_canary_receipts(db=db, batch=_batch(), result=_result())
    assert audit["blockers"] == ["CANARY_PERSISTED_PREDICTION_NOT_FOUND"]
    assert audit["persisted_rows"] == 0 and db.receipts == []


def test_wrapper_db_factory_failure_preserves_canonical_scoring(monkeypatch):
    from fastapi import FastAPI

    app = FastAPI()
    calls = []

    @app.post("/score-pick-request")
    def original(batch: DetailedPickRequestBatch, x_wow_model_identity=None):
        calls.append(batch.request_id)
        return _result()

    def broken_db_factory():
        raise ConnectionError("sensitive simulated connection detail")

    assert subject.install_prop_action_canary_capture(app, db_client_fn=broken_db_factory)
    endpoint = next(route.endpoint for route in app.routes
                    if getattr(route, "path", None) == "/score-pick-request")
    output = endpoint(_batch())
    assert calls == ["canary-request-1"]
    assert output["reconciliation_pass"] is True
    assert output["rows"] == _result()["rows"]
    audit = output["production_action_canary_capture"]
    assert audit["status"] == "BLOCKED_CAPTURE_UNAVAILABLE"
    assert audit["persisted_rows"] == 0
    assert audit["blockers"] == ["CANARY_CAPTURE_UNAVAILABLE:ConnectionError"]
    assert audit["can_execute"] is False
    assert output["can_execute"] is False
    assert "sensitive simulated" not in repr(audit)


def test_wrapper_capture_failure_does_not_reinvoke_scorer(monkeypatch):
    from fastapi import FastAPI

    app = FastAPI()
    calls = []

    @app.post("/score-pick-request")
    def original(batch: DetailedPickRequestBatch, x_wow_model_identity=None):
        calls.append("score")
        return _result()

    def failed_capture(**kwargs):
        raise TimeoutError("private store timeout")

    monkeypatch.setattr(subject, "capture_action_canary_receipts", failed_capture)
    assert subject.install_prop_action_canary_capture(app, db_client_fn=lambda: object())
    endpoint = next(route.endpoint for route in app.routes
                    if getattr(route, "path", None) == "/score-pick-request")
    output = endpoint(_batch())
    assert calls == ["score"]
    assert output["production_action_canary_capture"]["blockers"] == [
        "CANARY_CAPTURE_UNAVAILABLE:TimeoutError"
    ]
    assert output["production_action_canary_capture"]["persisted_rows"] == 0
    assert output["can_execute"] is False


def test_wrapper_does_not_mask_scorer_failure():
    from fastapi import FastAPI
    import pytest

    app = FastAPI()

    @app.post("/score-pick-request")
    def original(batch: DetailedPickRequestBatch, x_wow_model_identity=None):
        raise ValueError("scorer failed")

    assert subject.install_prop_action_canary_capture(app, db_client_fn=lambda: object())
    endpoint = next(route.endpoint for route in app.routes
                    if getattr(route, "path", None) == "/score-pick-request")
    with pytest.raises(ValueError, match="scorer failed"):
        endpoint(_batch())


def test_http_action_canary_factory_failure_preserves_successful_score(monkeypatch):
    """HTTP-level reproduction: capture setup must not turn a scored 200 into 500."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    scorer_calls = []

    @app.post("/score-pick-request")
    def real_scoring_boundary(batch: DetailedPickRequestBatch, x_wow_model_identity=None):
        scorer_calls.append(batch.request_id)
        return _result()

    def failing_factory():
        raise ConnectionError("private provider connection details")

    monkeypatch.setattr(subject, "REVIEWED_CERTIFICATION_RELEASES", {})
    assert subject.install_prop_action_canary_capture(app, db_client_fn=failing_factory)

    response = TestClient(app).post(
        "/score-pick-request",
        json=_batch().model_dump(mode="json", exclude_none=True),
    )
    assert response.status_code == 200
    body = response.json()
    assert scorer_calls == ["canary-request-1"]
    assert body["rows"] == _result()["rows"]
    assert body["reconciliation_pass"] is True
    assert body["can_execute"] is False
    assert body["production_action_canary_capture"]["status"] == "BLOCKED_CAPTURE_UNAVAILABLE"
    assert body["production_action_canary_capture"]["persisted_rows"] == 0
    assert body["production_action_canary_capture"]["blockers"] == [
        "CANARY_CAPTURE_UNAVAILABLE:ConnectionError"
    ]
    assert "private provider" not in response.text
