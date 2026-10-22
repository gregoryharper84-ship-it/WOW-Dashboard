"""Negative tests for private, inert NCAAF result/form fitted candidate binding."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from ncaaf_result_form_candidate_binding_audit import (  # noqa
    CandidateBindingBlocked, FEATURE_NAMES, _hash,
    audit_metadata, audit_private_artifact,
)


class NCAAFResultFormBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_binding = json.loads((ROOT / "data/research/ncaaf_result_form_candidate_binding_20261022.json").read_text())
        cls.features = json.loads((ROOT / "data/research/ncaaf_result_form_forward_features_20261022.json").read_text())

    def fixture(self):
        binding = copy.deepcopy(self.source_binding)
        artifact = {
            "artifact_format": "STANDARDIZED_LOGISTIC_JSON_V1",
            "model_family": "NCAAF_RESULT_FORM_LOGIT_V1",
            "feature_names": list(FEATURE_NAMES),
            "intercept": 0.1, "coefficients": [0.1] * 9,
            "scaler_mean": [0.0] * 9, "scaler_scale": [1.0] * 9,
            "split_policy": "CHRONOLOGICAL_60_20_20",
            "calibrator_selection_version": "FORWARD_CALIBRATION_SELECTION_V2",
        }
        candidate = {
            "candidate_id": binding["candidate_id"],
            "model_artifact_version": binding["model_artifact_version"],
            "artifact_payload": artifact,
            "artifact_checksum": _hash(artifact),
            "calibrator_payload": {
                "method": "IDENTITY_RAW_PROBABILITY_V1",
                "bins": [],
                "selection_version": "FORWARD_CALIBRATION_SELECTION_V2",
                "selection": {"test_block_used_for_selection": False},
            },
            "active": False, "promoted": False,
            "probability_publishable": False, "can_execute": False,
        }
        binding["model_artifact_checksum"] = candidate["artifact_checksum"]
        binding["model_artifact_checksum_recomputed"] = candidate["artifact_checksum"]
        return binding, candidate

    def rejects(self, binding, candidate, code):
        with self.assertRaises(CandidateBindingBlocked) as context:
            audit_private_artifact(binding, self.features, candidate)
        self.assertEqual(context.exception.code, code)

    def test_frozen_44_metadata_passes(self):
        output = audit_metadata(self.source_binding, self.features)
        self.assertEqual(output["events_verified"], 44)
        self.assertFalse(output["probability_publishable"])

    def test_synthetic_candidate_shadow_is_never_publishable(self):
        b,c = self.fixture()
        out = audit_private_artifact(b, self.features, c)
        self.assertEqual(out["finite_probability_count"], 44)
        self.assertIsNone(out["model_probability"])
        self.assertIsNone(out["calibrated_lower_bound"])
        self.assertFalse(out["probability_publishable"])

    def test_wrong_family_rejected(self):
        b,c=self.fixture()
        b["model_family"]="NCAAF_DYNAMIC_TEAM_STATE_LOGIT_V2"
        self.rejects(b,c,"NCAAF_CANDIDATE_EXACT_CONTRACT_MISMATCH")

    def test_wrong_feature_order_rejected(self):
        b,c=self.fixture()
        b["feature_names"]=list(reversed(b["feature_names"]))
        self.rejects(b,c,"NCAAF_CANDIDATE_EXACT_CONTRACT_MISMATCH")

    def test_forged_activation_rejected(self):
        b,c=self.fixture()
        c["active"]=True
        self.rejects(b,c,"NCAAF_CANDIDATE_FORBIDDEN_PUBLICATION_CLAIM")

    def test_fake_certification_rejected(self):
        b,c=self.fixture()
        b["candidate_bound_certification"]="PASS"
        self.rejects(b,c,"NCAAF_CANDIDATE_FORBIDDEN_PUBLICATION_CLAIM")

    def test_checksum_tampering_rejected(self):
        b,c=self.fixture()
        c["artifact_payload"]["coefficients"][3]=1.5
        self.rejects(b,c,"NCAAF_CANDIDATE_MODEL_HASH_MISMATCH")

    def test_calibrator_switch_rejected(self):
        b,c=self.fixture()
        c["calibrator_payload"]["method"]="EMPIRICAL_WILSON_BINS_V1"
        self.rejects(b,c,"NCAAF_CANDIDATE_CALIBRATOR_UNSUPPORTED")

    def test_holdout_calibrator_selection_rejected(self):
        b,c=self.fixture()
        c["calibrator_payload"]["selection"]["test_block_used_for_selection"]=True
        self.rejects(b,c,"NCAAF_CANDIDATE_CALIBRATOR_UNSUPPORTED")

    def test_missing_coefficient_rejected(self):
        b,c=self.fixture()
        c["artifact_payload"]["coefficients"].pop()
        c["artifact_checksum"]=_hash(c["artifact_payload"])
        b["model_artifact_checksum"]=c["artifact_checksum"]
        b["model_artifact_checksum_recomputed"]=c["artifact_checksum"]
        self.rejects(b,c,"NCAAF_CANDIDATE_VECTOR_DIMENSION_INVALID")

    def test_invalid_scale_rejected(self):
        b,c=self.fixture()
        c["artifact_payload"]["scaler_scale"][0]=0
        c["artifact_checksum"]=_hash(c["artifact_payload"])
        b["model_artifact_checksum"]=c["artifact_checksum"]
        b["model_artifact_checksum_recomputed"]=c["artifact_checksum"]
        self.rejects(b,c,"NCAAF_CANDIDATE_SCALE_INVALID")

    def test_privileged_row_rejected(self):
        x=copy.deepcopy(self.features)
        x["events"][0]["model_probability"]=0.75
        with self.assertRaises(CandidateBindingBlocked) as context:
            audit_metadata(self.source_binding,x)
        self.assertEqual(context.exception.code,"NCAAF_CANDIDATE_UNSUPPORTED_ROW_AUTHORITY")

    def test_missing_event_rejected(self):
        x=copy.deepcopy(self.features)
        x["events"].pop()
        with self.assertRaises(CandidateBindingBlocked) as context:
            audit_metadata(self.source_binding,x)
        self.assertEqual(context.exception.code,"NCAAF_CANDIDATE_FORWARD_COHORT_INCOMPLETE")


if __name__ == "__main__":
    unittest.main()
