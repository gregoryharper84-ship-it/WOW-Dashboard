"""Offline regression tests for the V17 legacy evidence-only salvage adapter."""
from __future__ import annotations

import importlib.util
import pathlib
import unittest
from datetime import datetime, timezone

MODULE = pathlib.Path(__file__).with_name("legacy_salvage_diagnostics.py")
SPEC = importlib.util.spec_from_file_location("legacy_salvage_diagnostics", MODULE)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)

NOW = datetime(2026, 10, 27, 17, 0, tzinfo=timezone.utc)


def prop(**kwargs):
    row = {
        "row_key": "p1",
        "official_event_id": "game-verified-001",
        "event_status": "SCHEDULED",
        "scheduled_start_utc": "2026-10-27T18:00:00Z",
        "participant": "Fixture Player",
        "market": "PLAYER_PROP",
        "stat_type": "ASSISTS",
        "period": "FULL_GAME",
        "exact_line": "7+",
        "direction": "MORE",
        "source_timestamp": "2026-10-27T16:59:00Z",
        "market_evidence_type": "NO_MARKET",
    }
    row.update(kwargs)
    return row


class SalvageDiagnosticTests(unittest.TestCase):
    def call(self, row=None, **kwargs):
        return audit.audit_candidate(
            row if row is not None else prop(),
            mode="PROP", now_utc=NOW, source_max_age_seconds=300, **kwargs
        )

    def test_at_least_threshold_preserved_without_numeric_conversion(self):
        row = prop(exact_line="7+")
        before = dict(row)
        result = self.call(row)
        self.assertEqual(row, before)
        self.assertFalse(result["source_fields_missing"])
        self.assertTrue(result["diagnostic_only"])

    def test_no_market_does_not_erase_valid_sporting_evidence(self):
        result = self.call()
        self.assertEqual(result["market_evidence_notes"], ["market_price_unavailable"])
        self.assertEqual(result["sporting_evidence_issues"], [])
        self.assertIsNone(result["calibrated_lower_bound"])

    def test_adjacent_market_never_claims_exact(self):
        result = self.call(prop(market_evidence_type="ADJACENT_LINE"))
        self.assertIn("adjacent_line_not_exact_line_evidence", result["market_evidence_notes"])

    def test_material_update_after_model_flags_rerun(self):
        result = self.call(prop(
            model_timestamp="2026-10-27T16:50:00Z",
            latest_material_update_at="2026-10-27T16:55:00Z",
        ))
        self.assertTrue(result["model_rerun_recommended"])
        self.assertIn("material_update_after_model", result["sporting_evidence_issues"])

    def test_status_eliminates_pregame_eligibility_but_not_record(self):
        for status in ("STARTED", "LIVE", "FINAL", "CANCELED", "POSTPONED"):
            with self.subTest(status=status):
                result = self.call(prop(event_status=status))
                self.assertFalse(result["pregame_eligible_by_status"])
                self.assertEqual(result["row_key"], "p1")

    def test_unverified_event_status_cannot_pass_pregame(self):
        result = self.call(prop(event_status="UNKNOWN"))
        self.assertFalse(result["pregame_eligible_by_status"])
        self.assertIn("event_status_unverified", result["sporting_evidence_issues"])

    def test_stale_source_is_diagnostic_not_model_failure(self):
        result = self.call(prop(source_timestamp="2026-10-27T16:50:00Z"))
        self.assertIn("source_evidence_stale", result["sporting_evidence_issues"])
        self.assertFalse(result["source_is_fresh"])
        self.assertIsNone(result["terminal_label"])

    def test_naive_timestamp_not_assumed_utc(self):
        result = self.call(prop(source_timestamp="2026-10-27T16:59:00"))
        self.assertIn("invalid_or_naive_source_timestamp", result["sporting_evidence_issues"])

    def test_future_source_is_not_fresh(self):
        result = self.call(prop(source_timestamp="2026-10-27T17:01:00Z"))
        self.assertIn("source_timestamp_in_future", result["sporting_evidence_issues"])
        self.assertFalse(result["source_is_fresh"])

    def test_missing_direction_does_not_default_more(self):
        result = self.call(prop(direction=None))
        self.assertIn("direction", result["source_fields_missing"])

    def test_team_event_exact_outcome_and_settlement(self):
        row = {
            "row_key": "w1", "official_event_id": "game-a", "event_status": "SCHEDULED",
            "scheduled_start_utc": "2026-10-27T18:00:00Z",
            "participant": "Team A", "market": "SOCCER_1X2",
            "exact_selection": "HOME", "outcome_space": "HOME/DRAW/AWAY",
            "settlement_rule": "FULL_TIME_90_MINUTES",
            "source_timestamp": "2026-10-27T16:59:00Z",
        }
        result = audit.audit_candidate(
            row, mode="TEAM_EVENT", now_utc=NOW, source_max_age_seconds=300
        )
        self.assertEqual(result["source_fields_missing"], [])
        self.assertIsNone(result["model_probability"])

    def test_compound_market_is_preserved_as_exact_market_label(self):
        result = self.call(prop(market="DOUBLE_DOUBLE", stat_type="COMPOUND", exact_line="Yes"))
        self.assertFalse(result["source_fields_missing"])

    def test_duplicated_rows_not_silently_dropped(self):
        batch = audit.audit_rows(
            [prop(row_key="same"), prop(row_key="same"), prop(row_key="other")],
            mode="PROP", now_utc=NOW, source_max_age_seconds=300,
        )
        self.assertEqual((batch["rows_received"], batch["rows_reported"]), (3, 3))
        self.assertIn("repeated_row_key_check_identity",
                      batch["rows"][1]["sporting_evidence_issues"])
        self.assertFalse(batch["can_execute"])

    def test_freshness_policy_is_route_supplied_not_global(self):
        with self.assertRaises(ValueError):
            audit.audit_candidate(prop(), mode="PROP", now_utc=NOW,
                                  source_max_age_seconds=-1)
        with self.assertRaises(ValueError):
            audit.audit_candidate(prop(), mode="PROP", now_utc=NOW,
                                  source_max_age_seconds=True)

    def test_future_schedule_required_to_call_event_pregame(self):
        result = self.call(prop(scheduled_start_utc="2026-10-27T16:00:00Z"))
        self.assertFalse(result["pregame_eligible_by_status"])
        self.assertIn("scheduled_event_has_started", result["sporting_evidence_issues"])

    def test_invalid_schedule_fails_closed(self):
        result = self.call(prop(scheduled_start_utc="2026-10-27T18:00:00"))
        self.assertFalse(result["pregame_eligible_by_status"])
        self.assertIn("invalid_or_naive_start_timestamp", result["sporting_evidence_issues"])

    def test_out_and_ir_participant_diagnostic(self):
        for status in ("OUT", "SCRATCHED", "IR", "INACTIVE"):
            with self.subTest(status=status):
                result = self.call(prop(critical_participant_status=status))
                self.assertIn("critical_participant_unavailable", result["sporting_evidence_issues"])
                self.assertIsNone(result["terminal_label"])

    def test_model_timestamp_missing_is_distinct_from_model_rerun(self):
        result = self.call(prop(latest_material_update_at="2026-10-27T16:50:00Z"))
        self.assertIn("material_update_without_model_timestamp", result["sporting_evidence_issues"])
        self.assertFalse(result["model_rerun_recommended"])

    def test_malformed_row_key_does_not_crash_or_omit_row(self):
        result = audit.audit_rows(
            [prop(row_key=["wrong-type"]), prop(row_key="good")],
            mode="PROP", now_utc=NOW, source_max_age_seconds=300,
        )
        self.assertEqual(result["rows_reported"], 2)
        self.assertIn("invalid_row_key", result["rows"][0]["sporting_evidence_issues"])

    def test_no_scoring_or_terminal_authority_anywhere(self):
        row = self.call()
        for name in ("model_probability", "calibrated_probability",
                     "calibrated_lower_bound", "terminal_label"):
            self.assertIsNone(row[name])
        self.assertFalse(row["can_execute"])
        self.assertFalse(row["controlling_specialist_invoked"])


if __name__ == "__main__":
    unittest.main()
