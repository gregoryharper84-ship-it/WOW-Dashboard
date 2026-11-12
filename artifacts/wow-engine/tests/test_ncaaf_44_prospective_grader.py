"""Adversarial verification of immutable 44-event prospective NCAAF grading.

All test outcomes are synthetic and NOT actual 2026 Oct24 results.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from ncaaf_44_prospective_grader import (  # noqa: E402
    freeze_contract, inspect_espn, grade, GradingBlocked,
    _git_blob, EXPECTED_SHADOW_BLOB,
)

CLOCK = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
SHA = "a" * 40


def serialize(obj):
    return json.dumps(obj, separators=(",", ":")).encode()


def fake_event(f, *, home_score="28", away_score="21", completed=True,
               status="STATUS_FINAL", start=None):
    return {
        "header": {
            "id": str(f["official_event_id"]),
            "competitions": [{
                "id": str(f["official_event_id"]),
                "date": start or f["event_start_time"],
                "status": {
                    "type": {
                        "name": status, "completed": completed,
                        "state": "post" if completed else "pre",
                    },
                },
                "competitors": [
                    {
                        "homeAway": "home",
                        "id": str(f["espn_home_team_id"]),
                        "team": {"id": str(f["espn_home_team_id"])},
                        "score": home_score,
                        "winner": bool(int(home_score) > int(away_score)),
                    },
                    {
                        "homeAway": "away",
                        "id": str(f["espn_away_team_id"]),
                        "team": {"id": str(f["espn_away_team_id"])},
                        "score": away_score,
                        "winner": bool(int(away_score) > int(home_score)),
                    },
                ],
            }],
        },
    }


class NCAAF44ProspectiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shadow = (ROOT / "data/research/ncaaf_result_form_44_pregame_shadow_20261022.json").read_bytes()
        cls.features = (ROOT / "data/research/ncaaf_result_form_forward_features_20261022.json").read_bytes()
        cls.ordered, _, _ = freeze_contract(cls.shadow, cls.features)

    def test_exact_real_pregame_blob_and_44_cohort(self):
        self.assertEqual(_git_blob(self.shadow), EXPECTED_SHADOW_BLOB)
        self.assertEqual(len(self.ordered), 44)
        self.assertEqual(len({p["shadow"]["official_event_id"] for p in self.ordered}), 44)
        self.assertTrue(all(p["shadow"]["probability_publishable"] is False for p in self.ordered))

    def test_altered_shadow_fails_closed(self):
        with self.assertRaises(GradingBlocked) as e:
            freeze_contract(self.shadow + b" ", self.features)
        self.assertEqual(e.exception.code, "NCAAF_POSTMORTEM_SHADOW_GIT_BLOB_CHANGED")

    def test_altered_feature_file_fails_closed(self):
        with self.assertRaises(GradingBlocked) as e:
            freeze_contract(self.shadow, self.features + b" ")
        self.assertEqual(e.exception.code, "NCAAF_POSTMORTEM_PREGAME_FEATURE_HASH_CHANGED")

    def test_verified_espn_final_exact_team_ids_and_scores(self):
        f = self.ordered[0]["feature"]
        result = inspect_espn(serialize(fake_event(f)), event=f, now=CLOCK)
        self.assertEqual(result["grade_status"], "VERIFIED_FINAL")
        self.assertTrue(result["home_won"])
        self.assertEqual(result["home_points"], 28)

    def test_completed_false_is_not_final(self):
        f = self.ordered[0]["feature"]
        item = fake_event(f, completed=False, status="STATUS_SCHEDULED")
        outcome = inspect_espn(serialize(item), event=f, now=CLOCK)
        self.assertEqual(outcome["grade_status"], "UNSETTLED_OR_POSTPONED")

    def test_typed_postponement_without_credit(self):
        f = self.ordered[0]["feature"]
        item = fake_event(f, completed=False, status="STATUS_POSTPONED")
        outcome = inspect_espn(serialize(item), event=f, now=CLOCK)
        self.assertEqual(outcome["grade_status"], "UNSETTLED_OR_POSTPONED")

    def test_event_id_spoof_blocked(self):
        f = self.ordered[0]["feature"]
        item = fake_event(f)
        item["header"]["id"] = "401000000"
        with self.assertRaises(GradingBlocked) as e:
            inspect_espn(serialize(item), event=f, now=CLOCK)
        self.assertEqual(e.exception.code, "NCAAF_POSTMORTEM_ESPN_EVENT_ID_MISMATCH")

    def test_home_away_team_switch_blocked(self):
        f = self.ordered[0]["feature"]
        item = fake_event(f)
        item["header"]["competitions"][0]["competitors"][0]["team"]["id"] = "999"
        with self.assertRaises(GradingBlocked) as e:
            inspect_espn(serialize(item), event=f, now=CLOCK)
        self.assertEqual(e.exception.code, "NCAAF_POSTMORTEM_ESPN_TEAM_ID_MISMATCH")

    def test_score_winner_contradiction_blocked(self):
        f = self.ordered[0]["feature"]
        item = fake_event(f)
        item["header"]["competitions"][0]["competitors"][0]["winner"] = False
        with self.assertRaises(GradingBlocked) as e:
            inspect_espn(serialize(item), event=f, now=CLOCK)
        self.assertEqual(e.exception.code, "NCAAF_POSTMORTEM_WINNER_SCORE_CONTRADICTION")

    def test_missing_scores_blocked(self):
        f = self.ordered[0]["feature"]
        item = fake_event(f)
        del item["header"]["competitions"][0]["competitors"][0]["score"]
        with self.assertRaises(GradingBlocked) as e:
            inspect_espn(serialize(item), event=f, now=CLOCK)
        self.assertEqual(e.exception.code, "NCAAF_POSTMORTEM_FINAL_SCORE_INVALID")

    def test_rescheduled_event_held_not_regraded(self):
        f = self.ordered[0]["feature"]
        item = fake_event(f, start="2026-10-29T00:00:00Z")
        result = inspect_espn(serialize(item), event=f, now=CLOCK)
        self.assertEqual(result["grade_status"], "RESCHEDULED_HOLD")

    def test_inconsistent_final_status_held(self):
        f = self.ordered[0]["feature"]
        item = fake_event(f, status="STATUS_IN_PROGRESS")
        result = inspect_espn(serialize(item), event=f, now=CLOCK)
        self.assertEqual(result["grade_status"], "UNSETTLED_OR_POSTPONED")

    def test_one_final_plus_43_missing_is_honest_partial_cohort(self):
        first = self.ordered[0]
        e_id = first["shadow"]["official_event_id"]
        report = grade(self.ordered, {e_id: serialize(fake_event(first["feature"]))},
                       captured_at=CLOCK, github_sha=SHA, github_run_id="123")
        self.assertEqual(report["status"], "RESEARCH_FORWARD_GRADE_INCOMPLETE_UNCERTIFIED")
        self.assertEqual(report["verified_final_count"], 1)
        self.assertEqual(report["pending_or_held_count"], 43)
        self.assertTrue(0 <= report["research_metrics"]["brier"] <= 1)
        self.assertFalse(report["research_metrics"]["complete_cohort"])
        self.assertIsNone(report["model_probability"])
        self.assertFalse(report["probability_publishable"])
        self.assertFalse(report["can_execute"])

    def test_zero_finals_never_fabricates_performance(self):
        report = grade(self.ordered, {}, captured_at=CLOCK, github_sha=SHA, github_run_id="123")
        self.assertEqual(report["verified_final_count"], 0)
        self.assertIsNone(report["research_metrics"]["brier"])
        self.assertIsNone(report["research_metrics"]["ece_10_bins"])

    def test_44_synthetic_finals_still_do_not_certify(self):
        sources = {
            e["shadow"]["official_event_id"]: serialize(
                fake_event(e["feature"], home_score="28", away_score="21")
            ) for e in self.ordered
        }
        result = grade(self.ordered, sources, captured_at=CLOCK,
                       github_sha=SHA, github_run_id="123")
        self.assertEqual(result["verified_final_count"], 44)
        self.assertEqual(result["status"], "RESEARCH_FORWARD_GRADED_ALL_44_UNCERTIFIED")
        self.assertIsNotNone(result["research_metrics"]["ece_10_bins"])
        self.assertFalse(result["probability_publishable"])
        self.assertEqual(result["historical_source_attestation"], "HOLD")

    def test_pending_not_counted_as_loss(self):
        f = self.ordered[0]["feature"]
        first_id = self.ordered[0]["shadow"]["official_event_id"]
        report = grade(self.ordered,
                       {first_id: serialize(fake_event(f, completed=False, status="STATUS_SCHEDULED"))},
                       captured_at=CLOCK, github_sha=SHA, github_run_id="123")
        self.assertEqual(report["verified_final_count"], 0)
        self.assertEqual(report["events"][0]["grade_status"], "UNSETTLED_OR_POSTPONED")

    def test_wrong_captured_context_rejected(self):
        with self.assertRaises(GradingBlocked) as e:
            grade(self.ordered, {}, captured_at=CLOCK, github_sha="bad", github_run_id="123")
        self.assertEqual(e.exception.code, "NCAAF_POSTMORTEM_CAPTURE_CONTEXT_INVALID")

    def test_malformed_json_source_records_hold(self):
        first_id = self.ordered[0]["shadow"]["official_event_id"]
        report = grade(self.ordered, {first_id: b"not json"},
                       captured_at=CLOCK, github_sha=SHA, github_run_id="123")
        self.assertEqual(report["events"][0]["grade_status"], "SOURCE_REJECTED")
        self.assertEqual(report["events"][0]["reason_code"],
                         "NCAAF_POSTMORTEM_SOURCE_JSON_INVALID")


if __name__ == "__main__":
    unittest.main()
