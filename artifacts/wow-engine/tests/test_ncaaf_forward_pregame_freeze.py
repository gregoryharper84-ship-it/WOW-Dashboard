"""Real 44-event freeze guard: direct positives and fail-closed tamper tests."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from ncaaf_forward_pregame_freeze import (  # noqa: E402
    build_receipt, ForwardCaptureError,
)

EARLY = datetime(2026, 10, 23, 0, 40, tzinfo=timezone.utc)


class NCAAFProspectiveFreezeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.feature_data = (ROOT / "data/research/ncaaf_result_form_forward_features_20261022.json").read_bytes()
        cls.identity_data = (ROOT / "data/research/frozen_20261022_ncaaf_nhl_identities.json").read_bytes()

    @staticmethod
    def data(payload):
        return (json.dumps(payload, indent=2) + "\n").encode()

    def make(self, **kwargs):
        args = dict(feature_bytes=self.feature_data, identity_bytes=self.identity_data,
                    captured_at=EARLY, github_sha="a" * 40,
                    github_run_id="39890000001", github_run_attempt="1")
        args.update(kwargs)
        return build_receipt(**args)

    def blocked(self, code, **kwargs):
        with self.assertRaises(ForwardCaptureError) as cm:
            self.make(**kwargs)
        self.assertEqual(cm.exception.code, code)

    def test_actual_44_target_events_can_be_prospectively_frozen(self):
        result = self.make()
        self.assertEqual(result["event_count"], 44)
        self.assertEqual(len(set(result["event_ids"])), 44)
        self.assertEqual(result["witness_kind"],
                         "GITHUB_ACTIONS_PROTECTED_MAIN_CAPTURE_BEFORE_TARGET_EVENTS")
        self.assertEqual(result["status"], "NCAAF_44_PROSPECTIVE_FORWARD_INPUTS_FROZEN_NOT_CERTIFIED")
        self.assertFalse(result["historical_training_rows_retroactively_attested"])
        self.assertEqual(result["historical_training_source_attestation"], "HOLD")
        self.assertFalse(result["provider_raw_payload_bytes_in_this_archive"])
        self.assertFalse(result["candidate_certified"])
        self.assertFalse(result["probability_publishable"])
        self.assertFalse(result["can_execute"])
        self.assertIsNone(result["model_probability"])
        self.assertIsNone(result["calibrated_lower_bound"])

    def test_capture_clock_rejects_after_kickoff(self):
        self.blocked("NCAAF_FREEZE_NOT_30_MIN_PREKICKOFF",
                     captured_at=datetime(2026, 10, 25, tzinfo=timezone.utc))

    def test_capture_clock_rejects_inside_30_min_buffer(self):
        self.blocked("NCAAF_FREEZE_NOT_30_MIN_PREKICKOFF",
                     captured_at=datetime(2026, 10, 24, 15, 31, tzinfo=timezone.utc))

    def test_only_protected_main_allowed(self):
        self.blocked("NCAAF_FREEZE_GITHUB_CONTEXT_UNTRUSTED",
                     github_ref="refs/heads/research")
        self.blocked("NCAAF_FREEZE_GITHUB_CONTEXT_UNTRUSTED",
                     github_event_name="pull_request")
        self.blocked("NCAAF_FREEZE_GITHUB_CONTEXT_UNTRUSTED",
                     github_repository="adversary/repo")

    def test_invalid_sha_rejected(self):
        self.blocked("NCAAF_FREEZE_HEAD_SHA_INVALID", github_sha="not-sha")

    def test_invalid_action_id_rejected(self):
        self.blocked("NCAAF_FREEZE_ACTION_IDENTITY_INVALID",
                     github_run_attempt="0")

    def test_fake_source_clock_after_witness_rejected(self):
        data = json.loads(self.feature_data)
        data["source_snapshot"]["retrieved_at"] = "2026-10-24T15:00:00Z"
        self.blocked("NCAAF_FREEZE_SOURCE_CAPTURE_AFTER_WITNESS",
                     feature_bytes=self.data(data))

    def test_identity_swap_rejected(self):
        data = json.loads(self.feature_data)
        data["events"][0]["espn_home_team_id"] = "0"
        self.blocked("NCAAF_FORWARD_EVENT_IDENTITY_MISMATCH",
                     feature_bytes=self.data(data))

    def test_forged_source_hash_rejected(self):
        data = json.loads(self.feature_data)
        data["source_snapshot"]["payload_sha256"] = "not-a-sha"
        self.blocked("NCAAF_FORWARD_CFBD_SOURCE_INVALID",
                     feature_bytes=self.data(data))

    def test_forged_probability_rejected(self):
        data = json.loads(self.feature_data)
        data["events"][0]["model_probability"] = 0.91
        self.blocked("NCAAF_FORWARD_UNSUPPORTED_AUTHORITY",
                     feature_bytes=self.data(data))

    def test_forged_feature_rejected(self):
        data = json.loads(self.feature_data)
        data["events"][0]["features"]["home_point_diff_prior"] += 5
        self.blocked("NCAAF_FORWARD_FEATURE_REPLAY_MISMATCH",
                     feature_bytes=self.data(data))

    def test_partial_cohort_rejected(self):
        data = json.loads(self.feature_data)
        data["events"].pop()
        self.blocked("NCAAF_FORWARD_EVENT_COUNT_INVALID",
                     feature_bytes=self.data(data))

    def test_wrong_espn_capture_run_rejected(self):
        data = json.loads(self.feature_data)
        data["espn_source"]["github_run_id"] = 1
        self.blocked("NCAAF_FREEZE_ESPN_CAPTURE_PROVENANCE_MISMATCH",
                     feature_bytes=self.data(data))

    def test_cannot_convert_historical_reconstruction_to_certified(self):
        data = json.loads(self.feature_data)
        data["model_artifact_certified"] = True
        self.blocked("NCAAF_FORWARD_UNSUPPORTED_AUTHORITY",
                     feature_bytes=self.data(data))

    def test_negative_naive_witness_clock(self):
        self.blocked("NCAAF_FREEZE_CAPTURE_CLOCK_NAIVE",
                     captured_at=datetime(2026, 10, 23, 1, 0))


if __name__ == "__main__":
    unittest.main()
