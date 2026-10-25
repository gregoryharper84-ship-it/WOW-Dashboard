"""Offline adversarial checks for the 44 real, previously sourced NCAAF inputs."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from ncaaf_frozen_result_form_audit import audit, FrozenInputError, FEATURE_NAMES


class FrozenNCAAFTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((ROOT / "data/research/ncaaf_result_form_forward_features_20261022.json").read_text())
        cls.espn = json.loads((ROOT / "data/research/frozen_20261022_ncaaf_nhl_identities.json").read_text())

    def altered(self):
        return copy.deepcopy(self.data)

    def blocked(self, data, code):
        with self.assertRaises(FrozenInputError) as ctx:
            audit(data, self.espn)
        self.assertEqual(ctx.exception.code, code)

    def test_all_real_events_pass_and_remain_nonpublishable(self):
        result = audit(self.data, self.espn)
        self.assertEqual(result["status"], "NCAAF_RESULT_FORM_FORWARD_FEATURE_REPLAY_PASS")
        self.assertEqual(result["events_verified"], 44)
        self.assertIsNone(result["model_probability"])
        self.assertFalse(result["probability_publishable"])
        self.assertFalse(result["can_execute"])

    def test_feature_contract_matches_existing_candidate_not_rich_model(self):
        from v17.ncaaf_result_form_candidate import FEATURE_NAMES as model_features
        self.assertEqual(FEATURE_NAMES, model_features)
        self.assertEqual(self.data["feature_schema_version"], "NCAAF_RESULT_FORM_PRIOR_V1")
        self.assertNotEqual(self.data["feature_schema_version"], "NCAAF_FEATURES_V1")

    def test_duplicate_espn_id_rejected(self):
        a = self.altered()
        a["events"][1]["espn_event_id"] = a["events"][0]["official_event_id"]
        self.blocked(a, "NCAAF_FORWARD_EVENT_IDENTITY_MISMATCH")

    def test_swapped_team_identity_rejected(self):
        a = self.altered()
        a["events"][0]["espn_home_team_id"] = a["events"][0]["espn_away_team_id"]
        self.blocked(a, "NCAAF_FORWARD_EVENT_IDENTITY_MISMATCH")

    def test_after_kickoff_features_rejected(self):
        a = self.altered()
        a["events"][0]["feature_as_of"] = a["events"][0]["event_start_time"]
        self.blocked(a, "NCAAF_FORWARD_TEMPORAL_CONTRADICTION")

    def test_after_kickoff_source_rejected(self):
        a = self.altered()
        a["source_snapshot"]["retrieved_at"] = "2026-10-26T00:00:00Z"
        self.blocked(a, "NCAAF_FORWARD_TEMPORAL_CONTRADICTION")

    def test_float_change_cannot_fake_feature(self):
        a = self.altered()
        a["events"][0]["features"]["home_win_rate_prior"] += 0.12
        self.blocked(a, "NCAAF_FORWARD_FEATURE_REPLAY_MISMATCH")

    def test_numeric_bool_rejected(self):
        a = self.altered()
        a["events"][0]["features"]["neutral_site"] = True
        self.blocked(a, "NCAAF_FORWARD_NUMERIC_VECTOR_INVALID")

    def test_incomplete_history_rejected(self):
        a = self.altered()
        a["events"][0]["history"]["away"]["games"] = 2
        self.blocked(a, "NCAAF_FORWARD_PRIOR_HISTORY_INVALID")

    def test_duplicate_prior_rejected(self):
        a = self.altered()
        h = a["events"][0]["history"]["home"]["prior_event_ids"]
        h[-1] = h[0]
        self.blocked(a, "NCAAF_FORWARD_PRIOR_HISTORY_INVALID")

    def test_fake_probability_claim_rejected(self):
        a = self.altered()
        a["events"][0]["model_probability"] = 0.75
        self.blocked(a, "NCAAF_FORWARD_UNSUPPORTED_AUTHORITY")

    def test_wrong_model_family_rejected(self):
        a = self.altered()
        a["candidate_model_family"] = "NCAAF_LOGISTIC_V1"
        self.blocked(a, "NCAAF_FORWARD_MODEL_CONTRACT_MISMATCH")


if __name__ == "__main__":
    unittest.main()
