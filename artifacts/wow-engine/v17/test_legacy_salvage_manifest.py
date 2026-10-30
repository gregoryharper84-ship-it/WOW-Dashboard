"""Schema and coverage tests for the frozen legacy migration inventory.

These tests check a planning artifact only; they do NOT certify live V17 routes.
"""
from __future__ import annotations

import json
import pathlib
import unittest

PATH = pathlib.Path(__file__).with_name("legacy_salvage_manifest.json")
DATA = json.loads(PATH.read_text(encoding="utf-8"))
SOURCES = json.loads(PATH.with_name("legacy_salvage_source_inventory.json").read_text(encoding="utf-8"))

EXPECTED = {str(i) for i in range(25)} | {
    "32", "33", "34", "35", "game_winner", "patch_010_amendment"
}


class LegacySalvageManifestTests(unittest.TestCase):
    def test_all_source_sections_explicitly_accounted_for(self):
        ids = [entry["id"] for entry in DATA["entries"]]
        self.assertEqual(len(ids), 31)
        self.assertEqual(set(ids), EXPECTED)
        self.assertEqual(len(ids), len(set(ids)))

    def test_manifest_does_not_claim_production_adoption(self):
        self.assertFalse(DATA["can_execute"])
        self.assertEqual(DATA["deployment_status"], "NOT_DEPLOYED")
        self.assertEqual(DATA["terminal_authority"], "V17_TERMINAL_REDUCER")
        for entry in DATA["entries"]:
            self.assertEqual(entry["production_status"],
                             "UNVERIFIED_DO_NOT_INFER_FROM_LEGACY_SHIPPED")
            self.assertEqual(entry["code_wiring_status"],
                             "REQUIRES_EXACT_SHA_PATH_AUDIT")

    def test_every_section_has_single_owner_and_typed_decision(self):
        workstreams = DATA["workstreams"]
        dispositions = set(DATA["accepted_dispositions"])
        for entry in DATA["entries"]:
            self.assertIn(entry["workstream"], workstreams)
            self.assertIn(entry["disposition"], dispositions)
            self.assertTrue(entry["acceptance_guard"])
            self.assertGreater(entry["source_line"], 0)

    def test_high_risk_legacy_probability_does_not_reenter_v17(self):
        lookup = {entry["id"]: entry["disposition"] for entry in DATA["entries"]}
        self.assertEqual(lookup["3"], "ARCHIVE_V16_ONLY")
        self.assertEqual(lookup["35"], "REPLACE_WITH_V17_CONTRACT")
        self.assertEqual(lookup["23"], "CHALLENGER_ONLY")
        self.assertEqual(lookup["24"], "CHALLENGER_ONLY")
        self.assertEqual(lookup["game_winner"], "CHALLENGER_ONLY")
        self.assertEqual(lookup["5"], "CHALLENGER_ONLY")

    def test_repository_source_inventory_covers_every_section_without_runtime_claims(self):
        self.assertEqual(SOURCES["source_section_count"], 31)
        self.assertEqual(SOURCES["runtime_verification"], "NOT_PERFORMED")
        self.assertFalse(SOURCES["can_execute"])
        expected = {entry["id"] for entry in DATA["entries"]}
        actual = [entry["id"] for entry in SOURCES["entries"]]
        self.assertEqual(set(actual), expected)
        self.assertEqual(len(actual), len(set(actual)))
        for entry in SOURCES["entries"]:
            self.assertFalse(entry["runtime_activation_verified"])
            self.assertTrue(entry["relevant_repo_paths"])
            self.assertTrue(entry["code_evidence_class"])
            self.assertTrue(entry["next_verification"])
            self.assertTrue(entry["owner_workstream"] in DATA["workstreams"])

    def test_no_legacy_section_25_to_31_fabricated(self):
        ids = {entry["id"] for entry in DATA["entries"]}
        for i in range(25, 32):
            self.assertNotIn(str(i), ids)


if __name__ == "__main__":
    unittest.main()
