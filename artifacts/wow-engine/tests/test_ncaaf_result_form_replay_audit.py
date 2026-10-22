"""Adversarial isolated tests for candidate-bound NCAAF replay without scoring/promotion."""
from __future__ import annotations
import copy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ncaaf_result_form_replay_audit as a


@dataclass(frozen=True)
class FakeBinaryTrainingRow:
    event_id: str
    event_start_time: str
    feature_as_of: str
    positive_outcome: bool
    features: dict
    source_manifest_sha256: str


@dataclass(frozen=True)
class FakeMetrics:
    train_n: int = 180
    calibration_n: int = 60
    test_n: int = 60
    raw_brier: float = 0.2
    calibrated_brier: float = 0.2
    baseline_brier: float = 0.25
    raw_log_loss: float = 0.57
    calibrated_log_loss: float = 0.57
    baseline_log_loss: float = 0.69
    ece: float = 0.05


def fake_corpus():
    features = {name: 0.5 for name in a.FEATURE_NAMES}
    model = {
        "model_family": a.MODEL_FAMILY, "artifact_format": "STANDARDIZED_LOGISTIC_JSON_V1",
        "feature_names": list(a.FEATURE_NAMES), "coefficients": [0.1] * 9,
        "scaler_mean": [0.0]*9, "scaler_scale": [1.0]*9,
        "intercept": 0.1, "split_policy": "CHRONOLOGICAL_60_20_20",
        "calibrator_selection_version": "FORWARD_CALIBRATION_SELECTION_V2",
    }
    cal = {
        "method": "IDENTITY_RAW_PROBABILITY_V1", "bins": [],
        "selection_version": "FORWARD_CALIBRATION_SELECTION_V2",
        "selection": {"test_block_used_for_selection": False},
    }
    train_sha = 'abcdefabcdef12345678'
    datahash = 'e' * 64
    candidate = {
        "candidate_id": "fake-candidate-id", "sport": "NCAAF", "league": "NCAAF",
        "model_family": a.MODEL_FAMILY, "feature_schema_version": a.FEATURE_SCHEMA,
        "source_policy_id": a.SOURCE_POLICY, "lifecycle_state": "CANDIDATE",
        "research_screen_pass": True, "promoted": False, "active": False,
        "probability_publishable": False, "can_execute": False,
        "automatic_certification": False, "automatic_promotion": False,
        "artifact_payload": model, "calibrator_payload": cal,
        "artifact_checksum": a._hash(model),
        "training_dataset_hash": datahash,
        "training_code_sha": train_sha,
        "model_artifact_version": f"{a.MODEL_FAMILY}_{datahash[:16]}_{train_sha[:12]}",
        "created_at": "2026-10-22T18:00:00+00:00",
        "training_rows": 180, "calibration_rows": 60, "test_rows": 60,
        "validation_metrics": {
            **FakeMetrics().__dict__,
            "historical_reconstruction": True,
            "archived_pregame_snapshot": False,
            "market_features_used": False,
        },
    }
    rows = []
    now = datetime(2024, 8, 1, tzinfo=timezone.utc)
    for i in range(300):
        when = now + timedelta(days=i)
        manifest = {
            "policy": a.SOURCE_POLICY, "event_id": f"{i:04d}",
            "result_source": "CFBD:/games",
            "historical_reconstruction": True, "market_features_used": False,
            "home_prior_event_ids": ["h1","h2","h3"],
            "away_prior_event_ids": ["a1","a2","a3"],
        }
        rows.append({
            "official_event_id": f"{i:04d}",
            "event_start_time": when.isoformat(),
            "feature_as_of": (when - timedelta(seconds=1)).isoformat(),
            "created_at": "2026-10-22T17:59:00+00:00",
            "features": dict(features),
            "outcome_json": {"home_win": bool(i % 2)},
            "source_manifest": manifest,
            "source_manifest_sha256": a._hash(manifest),
            "historical_reconstruction": True,
            "archived_pregame_snapshot": False,
            "market_features_used": False,
            "can_execute": False,
        })
    return candidate, rows


def trainer_factory(candidate, *, throw=False):
    def train(rows, *, model_family, feature_names, min_rows):
        if throw:
            raise RuntimeError("real scorer failure")
        assert len(rows) == 300 and len(tuple(feature_names)) == 9 and min_rows == 300
        assert all(r.source_manifest_sha256 for r in rows)
        return SimpleNamespace(
            dataset_hash=candidate["training_dataset_hash"],
            artifact_payload=candidate["artifact_payload"],
            calibrator_payload=candidate["calibrator_payload"],
            metrics=FakeMetrics(),
        )
    return train


class NCAAFReplayTests(unittest.TestCase):
    def setUp(self):
        self.c, self.rows = fake_corpus()
        self.mock = ModuleType("v17.binary_candidate_lifecycle")
        self.mock.BinaryTrainingRow = FakeBinaryTrainingRow
        self.mock.train_binary_candidate = trainer_factory(self.c)
        self.patch = patch.dict(sys.modules, {"v17.binary_candidate_lifecycle": self.mock})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def evaluate(self):
        return a.audit_candidate(self.c, self.rows)

    def block(self, code):
        got = self.evaluate()
        self.assertEqual(got["code"], code, got)
        self.assertFalse(got["certification_granted"])
        self.assertFalse(got["probability_publishable"])
        self.assertFalse(got["can_execute"])

    def test_deterministic_replay_can_pass_but_source_remains_hold(self):
        out = self.evaluate()
        self.assertEqual(out["status"], "NCAAF_RESULT_FORM_DETERMINISTIC_REPLAY_PASS_SOURCE_HOLD")
        self.assertEqual(out["training_rows_replayed"], 300)
        self.assertEqual(out["source_hold_code"], a.SOURCE_HOLD)
        self.assertEqual(out["source_attestation_status"], "HOLD")
        self.assertFalse(out["certification_granted"])
        self.assertIsNone(out["model_probability"])
        self.assertIsNone(out["calibrated_lower_bound"])

    def test_wrong_model_family_fails_closed(self):
        self.c["model_family"]="NCAAF_DYNAMIC_TEAM_STATE_LOGIT_V2"
        self.block("NCAAF_REPLAY_CANDIDATE_CONTRACT_UNSUPPORTED")

    def test_unsupported_source_policy_fails_closed(self):
        self.c["source_policy_id"]="TEAM_STATE_DYNAMIC_PRIOR_ONLY_V1"
        self.block("NCAAF_REPLAY_CANDIDATE_CONTRACT_UNSUPPORTED")

    def test_promoted_candidate_rejected(self):
        self.c["promoted"] = True
        self.block("NCAAF_REPLAY_CANDIDATE_CONTRACT_UNSUPPORTED")

    def test_missing_archived_source_flag_rejected(self):
        self.rows[0]["archived_pregame_snapshot"] = True
        self.block("NCAAF_REPLAY_SOURCE_REGIME_CHANGED")

    def test_forged_historical_pregame_status_rejected(self):
        self.c["validation_metrics"]["archived_pregame_snapshot"] = True
        self.block("NCAAF_REPLAY_RESEARCH_SOURCE_FLAGS_INVALID")

    def test_forged_model_hash_rejected(self):
        self.c["artifact_checksum"]="a"*64
        self.block("NCAAF_REPLAY_ARTIFACT_CHECKSUM_MISMATCH")

    def test_wrong_calibrator_rejected(self):
        self.c["calibrator_payload"]["method"]="EMPIRICAL_WILSON_BINS_V1"
        self.block("NCAAF_REPLAY_CALIBRATOR_CONTRACT_UNSUPPORTED")

    def test_bad_source_manifest_hash_rejected(self):
        self.rows[0]["source_manifest_sha256"]="f"*64
        self.block("NCAAF_REPLAY_SOURCE_MANIFEST_HASH_MISMATCH")

    def test_wrong_evidence_policy_rejected(self):
        self.rows[0]["source_manifest"]["policy"]="UNKNOWN"
        self.block("NCAAF_REPLAY_SOURCE_MANIFEST_INVALID")

    def test_missing_team_history_rejected(self):
        self.rows[0]["source_manifest"]["home_prior_event_ids"] = ["only-one"]
        self.block("NCAAF_REPLAY_SOURCE_MANIFEST_INVALID")

    def test_market_evidence_rejected(self):
        self.rows[0]["market_features_used"] = True
        self.block("NCAAF_REPLAY_ROW_EXECUTION_OR_MARKET_INVALID")

    def test_postgame_asof_rejected(self):
        self.rows[0]["feature_as_of"] = self.rows[0]["event_start_time"]
        self.block("NCAAF_REPLAY_TEMPORAL_ORDER_INVALID")

    def test_wrong_outcome_rejected(self):
        self.rows[0]["outcome_json"]={"home_win": 1}
        self.block("NCAAF_REPLAY_OUTCOME_INVALID")

    def test_missing_row_fails_count(self):
        self.rows.pop()
        self.block("NCAAF_REPLAY_FROZEN_CORPUS_COUNT_MISMATCH")

    def test_same_timestamp_conflicting_fork_rejected(self):
        x=copy.deepcopy(self.rows[0])
        x["features"][a.FEATURE_NAMES[0]]=0.2
        self.rows.append(x)
        self.block("NCAAF_REPLAY_FROZEN_ROW_FORK")

    def test_calibration_replay_mismatch(self):
        original = copy.deepcopy(self.c["calibrator_payload"])
        self.c["calibrator_payload"]["selection"]["reason"]="tweaked"
        self.mock.train_binary_candidate = lambda *args, **kwargs: SimpleNamespace(
            dataset_hash=self.c["training_dataset_hash"],
            artifact_payload=self.c["artifact_payload"],
            calibrator_payload=original,
            metrics=FakeMetrics())
        self.block("NCAAF_REPLAY_CALIBRATOR_MISMATCH")

    def test_dataset_hash_replay_mismatch(self):
        self.mock.train_binary_candidate = lambda *args, **kwargs: SimpleNamespace(
            dataset_hash="d"*64,
            artifact_payload=self.c["artifact_payload"],
            calibrator_payload=self.c["calibrator_payload"],
            metrics=FakeMetrics())
        self.block("NCAAF_REPLAY_DATASET_HASH_MISMATCH")

    def test_model_artifact_replay_mismatch(self):
        self.mock.train_binary_candidate = lambda *args, **kwargs: SimpleNamespace(
            dataset_hash=self.c["training_dataset_hash"],
            artifact_payload={"diff":"unexpected"},
            calibrator_payload=self.c["calibrator_payload"],
            metrics=FakeMetrics())
        self.block("NCAAF_REPLAY_FITTED_ARTIFACT_MISMATCH")

    def test_metrics_replay_mismatch(self):
        self.c["validation_metrics"]["raw_brier"] = 0.01
        self.block("NCAAF_REPLAY_RESEARCH_METRICS_MISMATCH")

    def test_scorer_exception_typed_not_model_unavailable(self):
        self.mock.train_binary_candidate = trainer_factory(self.c, throw=True)
        self.block("NCAAF_REPLAY_SCORER_EXECUTION_FAILED")

    def test_future_created_rows_ignored_but_count_must_match(self):
        self.rows[0]["created_at"]="2026-10-23T00:00:00+00:00"
        self.block("NCAAF_REPLAY_FROZEN_CORPUS_COUNT_MISMATCH")

    def test_naive_timestamp_rejected(self):
        self.rows[0]["created_at"]="2026-10-22T17:00:00"
        self.block("NCAAF_REPLAY_TIMESTAMP_INVALID")


if __name__ == "__main__":
    unittest.main()