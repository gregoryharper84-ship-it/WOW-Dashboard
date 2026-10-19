"""Offline negative tests for V17 GPT Action delivery seams.

The journey evaluator currently consumes summary flags rather than validating a
fresh Action receipt or preserving page- and row-level failure codes. The canary
receipt also omits the controlling specialist and terminal authority. These
tests keep those gaps visible without changing production behavior.
"""

from types import SimpleNamespace

import pytest

from pick_request_runtime_core import _compact_pick_outcome, _completed_scored_outcome
from v17.engineering_effectiveness import evaluate_golden_user_journey
from v17.host_routing import (
    FullModelActionReceipt,
    LIVE_GPT_ACTION_RESULT_INVALID,
    validate_full_model_action_receipt,
)
import v17.prop_action_canary_capture as canary
from v17.prop_production_registration import CANARY_TABLE, _release_key


SPECIALIST = "wow.mlb-pitcher-failure-path-expert"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
PREDICTION_ID = "11111111-1111-1111-1111-111111111111"


def _scored_outcome():
    scored = {
        "controlling_specialist": SPECIALIST,
        "terminal_authority": TERMINAL_AUTHORITY,
        "probability_publishable": True,
        "prediction": {
            "prediction_id": PREDICTION_ID,
            "raw_model_probability": 0.67,
            "calibrated_probability": 0.64,
            "calibrated_probability_lower_bound": 0.58,
            "calibration_status": "PASS",
            "model_version": "SO_ARTIFACT_V1",
        },
        "probability_qualification": {
            "terminal_label": "FINAL_APPROVED",
            "confidence_tier": "HIGH",
            "rank_eligible": True,
            "model_supported": True,
            "downstream_money_evaluation_allowed": True,
            "blockers": [],
        },
    }
    return _completed_scored_outcome(
        row_key="row-1",
        scored=scored,
        snapshot_id="snapshot-1",
        fingerprint="fingerprint-1",
        acquisition={"status": "PASS"},
    )


def _journey(**overrides):
    receipt = {
        "host_identity": "WOW_BETTING_ENGINE",
        "live_editor_verified": True,
        "action_invoked": True,
        "inventory_accounted": True,
        "source_ingestion_complete": True,
        "all_rows_reconciled": True,
        "governed_scoring_accounted": True,
        "response_handoff_complete": True,
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
        "valid_governed_row_count": 1,
        "empty_slate_proven": False,
    }
    receipt.update(overrides)
    return receipt


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.selected = "*"
        self.filters = {}
        self.mode = "select"
        self.payload = None

    def select(self, columns):
        self.selected = columns
        self.mode = "select"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, _limit):
        return self

    def upsert(self, payload, **_kwargs):
        self.mode = "upsert"
        self.payload = dict(payload)
        return self

    def execute(self):
        if self.table == "wow_predictions":
            row = self.db.prediction
            if self.filters.get("prediction_id") != row["prediction_id"]:
                return SimpleNamespace(data=[])
            fields = row.keys() if self.selected == "*" else self.selected.split(",")
            return SimpleNamespace(data=[{key: row[key] for key in fields if key in row}])
        if self.table == CANARY_TABLE and self.mode == "upsert":
            self.db.receipts.append(dict(self.payload))
            return SimpleNamespace(data=[dict(self.payload)])
        if self.table == CANARY_TABLE:
            rows = [
                dict(row)
                for row in self.db.receipts
                if row["immutable_receipt_hash"]
                == self.filters.get("immutable_receipt_hash")
            ]
            return SimpleNamespace(data=rows)
        raise AssertionError((self.table, self.mode))


class _DB:
    def __init__(self, prediction):
        self.prediction = prediction
        self.receipts = []

    def table(self, name):
        return _Query(self, name)


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
        "controlling_specialist": SPECIALIST,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def _release():
    return SimpleNamespace(
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


def test_canonical_compact_backend_envelope_keeps_governed_row_identity():
    outcome = _scored_outcome()
    assert outcome["result"]["controlling_specialist"] == SPECIALIST
    assert outcome["result"]["prediction"]["calibrated_probability"] == 0.64
    assert outcome["result"]["prediction"]["calibrated_probability_lower_bound"] == 0.58
    assert outcome["result"]["terminal_authority"] == TERMINAL_AUTHORITY
    assert outcome["can_execute"] is False

    compact = _compact_pick_outcome(outcome)
    assert compact.get("controlling_specialist") == SPECIALIST
    assert compact.get("calibrated_probability") == 0.64
    assert compact.get("calibrated_probability_lower_bound") == 0.58
    assert compact.get("terminal_authority") == TERMINAL_AUTHORITY
    assert compact["can_execute"] is False


def test_persisted_action_canary_receipt_keeps_specialist_and_terminal_authority(
    monkeypatch,
):
    release = _release()
    key = _release_key("MLB", "PITCHER_STRIKEOUTS", "SO_ARTIFACT_V1", "sha256:abc")
    monkeypatch.setattr(canary, "REVIEWED_CERTIFICATION_RELEASES", {key: release})
    db = _DB(_prediction())
    result = {
        "can_execute": False,
        "reconciliation_pass": True,
        "rows": [_scored_outcome()],
    }
    batch = SimpleNamespace(
        model_dump=lambda **_kwargs: {"request_id": "request-1", "rows": []}
    )

    audit = canary.capture_action_canary_receipts(db=db, batch=batch, result=result)

    assert audit["status"] == "PASS"
    assert audit["persisted_rows"] == 1
    receipt = db.receipts[0]
    assert receipt["calibrated_probability"] == 0.64
    assert receipt["calibrated_lower_bound"] == 0.58
    assert receipt["can_execute"] is False
    missing = [
        field
        for field, expected in (
            ("controlling_specialist", SPECIALIST),
            ("terminal_authority", TERMINAL_AUTHORITY),
        )
        if receipt.get(field) != expected
    ]
    assert not missing, f"persisted Action receipt dropped governed fields: {missing}"


def test_backend_action_success_does_not_attest_the_live_gpt_editor():
    backend = validate_full_model_action_receipt(
        FullModelActionReceipt(
            candidate_family="PLAYER_PROP",
            action_invoked=True,
            operation_id="scoreWowPickRequest",
            backend_terminal_status="MODEL_QUALIFIED",
            backend_model_capability="AVAILABLE",
            backend_specialist_scoring_attempted=True,
            http_result=200,
        )
    )
    assert backend["full_model_completed"] is True
    assert backend["action_invocation_attempted"] is True
    result = evaluate_golden_user_journey(
        _journey(live_editor_verified=False, backend_http_status=200)
    )
    assert result["status"] != "PASS"
    assert "FAIL_ACTION_INVOCATION:LIVE_EDITOR_NOT_VERIFIED" in result["blockers"]


def test_health_or_ci_success_without_action_invocation_is_not_a_journey_pass():
    result = evaluate_golden_user_journey(
        {
            "ci_success": True,
            "backend_health_success": True,
            "backend_http_status": 200,
            "action_invoked": False,
            "can_execute": False,
        }
    )
    assert result["status"] != "PASS"
    assert "FAIL_ACTION_INVOCATION:ACTION_NOT_INVOKED" in result["blockers"]


def test_wrong_operation_cannot_promote_a_complete_summary_to_pass():
    backend = validate_full_model_action_receipt(
        FullModelActionReceipt(
            candidate_family="PLAYER_PROP",
            action_invoked=True,
            operation_id="getWowV17BackendHealth",
            backend_terminal_status="MODEL_QUALIFIED",
            http_result=200,
        )
    )
    assert backend["status"] == LIVE_GPT_ACTION_RESULT_INVALID
    result = evaluate_golden_user_journey(
        _journey(
            action_operation_id=backend["operation_id"],
            action_receipt_status=backend["status"],
        )
    )
    assert result["status"] != "PASS"


@pytest.mark.parametrize(
    "action_receipt",
    [
        None,
        {"invocation_id": "old", "stale": True},
        {"invocation_id": "duplicate", "replayed": True},
    ],
    ids=("missing", "stale", "replayed"),
)
def test_missing_stale_or_replayed_receipt_cannot_promote_journey(action_receipt):
    result = evaluate_golden_user_journey(
        _journey(action_receipt=action_receipt)
    )
    assert result["status"] != "PASS"


def test_skipped_verification_cannot_promote_journey_to_pass():
    result = evaluate_golden_user_journey(_journey(verification_skipped=True))
    assert result["status"] != "PASS"


def test_zero_unaccounted_rows_are_typed_as_prop_acquisition_failure():
    result = evaluate_golden_user_journey(
        _journey(
            inventory_accounted=False,
            acquired_row_count=0,
            valid_governed_row_count=0,
            empty_slate_proven=False,
        )
    )
    assert result["status"] == "FAIL_PROP_ACQUISITION"
    assert "FAIL_PROP_ACQUISITION:INVENTORY_NOT_ACCOUNTED" in result["blockers"]


def test_empty_slate_needs_source_evidence_not_only_a_boolean_claim():
    result = evaluate_golden_user_journey(
        _journey(
            acquired_row_count=0,
            valid_governed_row_count=0,
            empty_slate_proven=True,
            source_inventory_evidence=None,
        )
    )
    assert result["status"] != "PASS"


def test_unreadable_source_page_keeps_its_typed_ingestion_failure():
    failure = "SOURCE_PAGE_UNREADABLE:4"
    result = evaluate_golden_user_journey(
        _journey(
            source_ingestion_complete=False,
            source_ingestion_failure_code=failure,
            valid_governed_row_count=0,
        )
    )
    assert result["status"] == "FAIL_SOURCE_INGESTION"
    assert failure in result["blockers"]
    assert all("MODEL_UNAVAILABLE" not in blocker for blocker in result["blockers"])


@pytest.mark.parametrize(
    ("overrides", "failure_code"),
    [
        (
            {
                "governed_scoring_accounted": False,
                "scorer_failure_code": "MODEL_SCORER_FAILED",
            },
            "MODEL_SCORER_FAILED",
        ),
        (
            {"persisted_receipt_failure_code": "PERSISTENCE_FAILURE"},
            "PERSISTENCE_FAILURE",
        ),
        (
            {
                "response_handoff_complete": False,
                "response_handoff_failure_code": "CANONICAL_RESULT_NOT_DISPLAYED",
            },
            "CANONICAL_RESULT_NOT_DISPLAYED",
        ),
    ],
    ids=("scorer", "persisted-receipt", "response-handoff"),
)
def test_scorer_receipt_and_handoff_failures_remain_typed(overrides, failure_code):
    result = evaluate_golden_user_journey(_journey(**overrides))
    assert result["status"] != "PASS"
    assert failure_code in result["blockers"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"probability_source": "SPORTSBOOK_IMPLIED", "llm_substitution": True},
        {
            "rows_in": 2,
            "rows_completed": 1,
            "rows_held": 0,
            "rows_rejected": 0,
        },
    ],
    ids=("probability-substitution", "silent-row-loss"),
)
def test_untrusted_probability_or_unreconciled_rows_cannot_pass(overrides):
    result = evaluate_golden_user_journey(_journey(**overrides))
    assert result["status"] != "PASS"
