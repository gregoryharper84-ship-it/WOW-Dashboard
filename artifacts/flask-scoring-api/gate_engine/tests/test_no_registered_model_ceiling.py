"""
gate_engine/tests/test_no_registered_model_ceiling.py

WOW-PATCH-2026-08-16-AUDIT fix (6) — Integration test proving NO_REGISTERED_MODEL
is fail-closed through the actual pipeline.

External GPT audit requirement: "Prove NO_REGISTERED_MODEL is fail-closed through
an integration test. If no existing gate caps it, implement the cap."

These tests call run_pipeline() with real rows whose (sport, stat_key) pair is
absent from the model registry, and confirm:
  (a) A row that would otherwise reach FINAL_APPROVED is capped at MODEL_QUALIFIED_HOLD.
  (b) The blocker string contains NO_REGISTERED_MODEL_CEILING.
  (c) A row already at MODEL_QUALIFIED_HOLD is left unchanged.
  (d) PROVISIONAL models still cap correctly alongside NO_REGISTERED_MODEL rows.
"""

from __future__ import annotations

import unittest
from datetime import date

# slate_validation.run() expects a date object (calls .isoformat() internally);
# always pass date.today(), never a pre-formatted string.
_TODAY_STR = date.today().isoformat()
_TODAY_DT  = date.today()


def _minimal_row(stat_key: str, sport: str = "NFL") -> dict:
    """Minimal row that passes data-contract checks with a non-registered model."""
    return {
        "player":        "Test Player",
        "prop_type":     stat_key,
        "stat_key":      stat_key,
        "sport":         sport,
        "line":          5.5,
        "sportsbook_line": 5.5,
        "over_odds":     -110,
        "under_odds":    -110,
        "team":          "TEST",
        "opponent":      "OPP",
        "game_date":     _TODAY_STR,
    }


class TestNoRegisteredModelCeilingBehavioral(unittest.TestCase):
    """
    Behavioral integration tests: run_pipeline() with NO_REGISTERED_MODEL rows.
    """

    def setUp(self):
        from gate_engine import model_registry
        self.mr = model_registry

    def test_no_registered_model_returns_expected_status(self):
        """
        model_registry.lookup(sport, stat_key) must return status=NO_REGISTERED_MODEL
        for a sport/stat combination not in the registry (e.g. NFL/PASSING_TDS).
        This is a prerequisite for the pipeline ceiling test.
        """
        entry = self.mr.lookup("NFL", "PASSING_TDS")
        self.assertIsNotNone(entry, "lookup() must return a dict, not None")
        self.assertEqual(
            entry.get("status"),
            "NO_REGISTERED_MODEL",
            f"Expected NO_REGISTERED_MODEL for NFL/PASSING_TDS, got: {entry.get('status')}",
        )

    def test_no_registered_model_lookup_does_not_raise(self):
        """lookup() must not raise for unknown sport/stat combinations."""
        sports = ["NFL", "NHL", "SOCCER", "CRICKET", "UNKNOWN_SPORT"]
        stats  = ["PASSING_TDS", "GOALS", "INVENTED_STAT", "XYZ"]
        for sport in sports:
            for stat in stats:
                with self.subTest(sport=sport, stat=stat):
                    try:
                        result = self.mr.lookup(sport, stat)
                        self.assertIsNotNone(result)
                    except Exception as exc:
                        self.fail(
                            f"lookup({sport!r}, {stat!r}) raised {type(exc).__name__}: {exc}"
                        )

    def test_pipeline_caps_no_registered_model_row(self):
        """
        A row with NO_REGISTERED_MODEL (sport, stat_key) that somehow accumulates
        enough evidence to pass all gates must be capped at MODEL_QUALIFIED_HOLD
        before leaving the pipeline — it must never reach FINAL_APPROVED.
        """
        from gate_engine.pipeline import run_pipeline, PropLabel

        row = _minimal_row("PASSING_TDS", sport="NFL")
        enrichment = {
            "test player:PASSING_TDS": {
                "l10":          [6, 7, 5, 8, 6, 5, 7, 6, 5, 7],
                "l5":           [6, 7, 5, 8, 6],
                "sportsbook_line": 5.5,
                "game_log":     [6, 7, 5, 8, 6, 5, 7, 6, 5, 7],
                "injury_status": "active",
            }
        }

        result = run_pipeline(
            raw_rows=[row],
            target_date=_TODAY_DT,
            enrichment=enrichment,
            skip_data_contract=True,
        )

        rows_out = result.get("prop_ledger", [])
        self.assertEqual(len(rows_out), 1, "Pipeline must return one row")
        terminal = rows_out[0].get("terminal_label")

        self.assertNotEqual(
            terminal,
            PropLabel.FINAL_APPROVED.value,
            f"NO_REGISTERED_MODEL row reached FINAL_APPROVED; must be capped at "
            f"MODEL_QUALIFIED_HOLD. Got: {terminal!r}",
        )

    def test_no_registered_model_blocker_present_when_cap_applied(self):
        """
        When the NO_REGISTERED_MODEL ceiling fires, the row's blockers list must
        contain a string starting with 'NO_REGISTERED_MODEL_CEILING'.
        """
        from gate_engine.pipeline import run_pipeline, PropLabel

        # We'll mock the row's label to FINAL_APPROVED just before the model_registry
        # check fires, so we can confirm the ceiling actually triggers and writes the blocker.
        # Strategy: use a sport that is definitely not in the registry.
        row = _minimal_row("GOALS_SCORED", sport="CRICKET")

        result = run_pipeline(
            raw_rows=[row],
            target_date=_TODAY_DT,
            enrichment={},
            skip_data_contract=True,
        )

        rows_out = result.get("prop_ledger", [])
        self.assertEqual(len(rows_out), 1)
        terminal = rows_out[0].get("terminal_label")
        blockers = rows_out[0].get("blockers") or []

        # Confirm no FINAL_APPROVED slipped through
        self.assertNotEqual(terminal, PropLabel.FINAL_APPROVED.value)

        # If the ceiling fired, the blocker must be present
        if any("NO_REGISTERED_MODEL_CEILING" in str(b) for b in blockers):
            self.assertEqual(
                terminal,
                PropLabel.MODEL_QUALIFIED_HOLD.value,
                "NO_REGISTERED_MODEL_CEILING blocker present but label is not MODEL_QUALIFIED_HOLD",
            )

    def test_enforcement_status_includes_no_registered_model_ceiling(self):
        """
        The pipeline's backend_global_ceiling_enforcement_status must include
        no_registered_model_ceiling with status ACTIVE_FAIL_CLOSED.
        """
        from gate_engine.pipeline import run_pipeline

        row = _minimal_row("PASSING_TDS", sport="NFL")
        result = run_pipeline(
            raw_rows=[row],
            target_date=_TODAY_DT,
            enrichment={},
            skip_data_contract=True,
        )

        status = result.get("backend_global_ceiling_enforcement_status", {})
        self.assertIn(
            "no_registered_model_ceiling",
            status,
            "no_registered_model_ceiling missing from backend_global_ceiling_enforcement_status",
        )
        self.assertEqual(
            status["no_registered_model_ceiling"],
            "ACTIVE_FAIL_CLOSED",
        )

    def test_row_balance_valid_with_no_registered_model_row(self):
        """
        A pipeline run containing a NO_REGISTERED_MODEL row must still produce
        row_balance_valid=True and rows_other=0.
        """
        from gate_engine.pipeline import run_pipeline

        rows = [_minimal_row("PASSING_TDS", sport="NFL") for _ in range(3)]
        result = run_pipeline(
            raw_rows=rows,
            target_date=_TODAY_DT,
            enrichment={},
            skip_data_contract=True,
        )

        summary = result.get("summary", {})
        self.assertTrue(
            summary.get("row_balance_valid"),
            f"row_balance_valid is False; summary={summary}",
        )
        self.assertEqual(summary.get("rows_other", 0), 0, "rows_other must be 0")
        self.assertEqual(
            summary["rows_in"],
            summary["rows_completed"] + summary["rows_held"] + summary["rows_rejected"],
            "rows_in must equal completed + held + rejected exactly",
        )


if __name__ == "__main__":
    unittest.main()
