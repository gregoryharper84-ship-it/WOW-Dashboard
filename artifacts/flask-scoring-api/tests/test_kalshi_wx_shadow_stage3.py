"""
tests/test_kalshi_wx_shadow_stage3.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — Stage 3 tests

Tests for:
  gate_engine/kalshi_wx_shadow_orchestrator.py
  gate_engine/kalshi_wx_shadow_ledger.py
  gate_engine/kalshi_wx_shadow_client.py (integration)

No live API calls.  All subagent calls use mock clients.
No DB access — the ledger is in-process only.

Test plan — Section OR (Orchestrator) and LD (Ledger)
───────────────────────────────────────────────────────
OR1:  Full orchestrator run — all 5 mock subagents succeed → validate_shadow_output
      returns SHADOW_PASS; final payload has status="COMPLETE".
OR2:  First subagent failure → orchestrator returns a BLOCKED payload that still
      passes validate_shadow_output (BLOCKED is a valid status).
OR3:  Third subagent failure → orchestrator returns BLOCKED; first two subagents
      recorded in ledger.
OR4:  No DB imports in ledger module (structural grep).
OR5:  Contradiction-detection revised ceiling propagates to final payload.
OR6:  Unusual-regime factors become blockers in final payload.
OR7:  research() with flag=True and 5-call mock → ShadowValidationResult returned.
OR8:  research() with flag=False → SHADOW_AGENT_DISABLED (regression).
LD1:  ShadowLedger.record() increments total_recorded.
LD2:  ShadowLedger.get_recent() returns entries in newest-first order.
LD3:  ShadowLedger.violation_count() accumulates across runs.
LD4:  ShadowLedger.clear() resets all counters and entries.
LD5:  ShadowLedger respects max_entries — oldest entries are discarded.
LD6:  ShadowLedger is thread-safe (concurrent writes don't corrupt count).
"""
from __future__ import annotations

import os
import re
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from gate_engine.kalshi_wx_shadow_capability_boundary import CapabilityBoundary
from gate_engine.kalshi_wx_shadow_client import KalshiWxShadowResearchClient
from gate_engine.kalshi_wx_shadow_ledger import ShadowLedger, ShadowLedgerEntry
from gate_engine.kalshi_wx_shadow_orchestrator import (
    _assemble_payload,
    _build_blocked_payload,
    run_shadow_orchestrator,
)
from gate_engine.kalshi_wx_shadow_schema import SHADOW_PASS, ShadowValidationResult, validate_shadow_output
from gate_engine.kalshi_wx_shadow_subagents import SubagentResult

_BOUNDARY = CapabilityBoundary()
_CITY  = "NYC"
_DATE  = "2026-08-08"
_RUN   = "run-stage3"


# ── Mock helpers ──────────────────────────────────────────────────────────────

def _tool_block(name: str, inp: dict) -> MagicMock:
    b = MagicMock()
    b.type = "tool_use"
    b.name = name
    b.input = inp
    return b


def _response(tool_name: str, inp: dict) -> MagicMock:
    r = MagicMock()
    r.stop_reason = "tool_use"
    r.content = [_tool_block(tool_name, inp)]
    return r


def _make_5call_mock() -> tuple[MagicMock, dict]:
    """
    Build a mock client that returns valid responses for all 5 subagents,
    plus the expected assembled payload for reference.
    """
    fc_inp = {
        "scoring_mode": "gaussian_forecast",
        "calibration_status": "UNAVAILABLE",
        "uncertainty_tier": "HIGH",
        "recommended_ceiling": "KALSHI_WATCH",
        "blockers": [],
    }
    sr_inp = {
        "sources_present": ["nws_forecast"],
        "sources_missing": [],
        "conflicts": [],
        "reconciliation_status": "OK",
    }
    cd_inp = {"contradictions_found": [], "ceiling_impacted": False}
    ur_inp = {"regime_unusual": False, "regime_factors": [], "reliability_impact": "NONE"}
    ue_inp = {
        "uncertainty_tier": "HIGH",
        "uncertainty_sources": ["forecast_horizon"],
        "ceiling_impact": "NONE",
    }

    client = MagicMock()
    client.messages.create.side_effect = [
        _response("emit_forecast_context",        fc_inp),
        _response("emit_source_reconciliation",   sr_inp),
        _response("emit_contradiction_detection", cd_inp),
        _response("emit_regime_assessment",        ur_inp),
        _response("emit_uncertainty_summary",      ue_inp),
    ]
    return client, fc_inp


# ── OR1: Full success run ─────────────────────────────────────────────────────

class TestOR1FullSuccessRun(unittest.TestCase):

    def setUp(self):
        self._ledger = ShadowLedger()
        self._client, _ = _make_5call_mock()

    def test_OR1_returns_shadow_validation_result(self):
        result = run_shadow_orchestrator(
            _CITY, _DATE, _RUN, self._client, _BOUNDARY, self._ledger
        )
        self.assertIsInstance(result, ShadowValidationResult)

    def test_OR1_passed_true(self):
        result = run_shadow_orchestrator(
            _CITY, _DATE, _RUN, self._client, _BOUNDARY, self._ledger
        )
        self.assertTrue(result.passed, f"Validation failed: {result.failure_reason!r}")

    def test_OR1_all_5_sdk_calls_made(self):
        run_shadow_orchestrator(
            _CITY, _DATE, _RUN, self._client, _BOUNDARY, self._ledger
        )
        self.assertEqual(self._client.messages.create.call_count, 5)

    def test_OR1_ledger_records_complete_status(self):
        run_shadow_orchestrator(
            _CITY, _DATE, _RUN, self._client, _BOUNDARY, self._ledger
        )
        self.assertEqual(self._ledger.total_recorded(), 1)
        entry = self._ledger.get_recent(1)[0]
        self.assertEqual(entry.status, "COMPLETE")
        self.assertEqual(entry.city, _CITY)
        self.assertEqual(entry.run_id, _RUN)

    def test_OR1_final_payload_passes_schema(self):
        """Directly validate the assembled payload against the closed schema."""
        results = {
            "forecast_context": SubagentResult(
                subagent_id="forecast_context",
                tool_name="emit_forecast_context",
                tool_input={
                    "scoring_mode": "gaussian_forecast",
                    "calibration_status": "UNAVAILABLE",
                    "uncertainty_tier": "HIGH",
                    "recommended_ceiling": "KALSHI_WATCH",
                    "blockers": [],
                },
                hook_violations=[], success=True,
            ),
            "source_reconciliation": SubagentResult(
                subagent_id="source_reconciliation",
                tool_name="emit_source_reconciliation",
                tool_input={"sources_present": [], "sources_missing": [], "conflicts": [], "reconciliation_status": "OK"},
                hook_violations=[], success=True,
            ),
            "contradiction_detection": SubagentResult(
                subagent_id="contradiction_detection",
                tool_name="emit_contradiction_detection",
                tool_input={"contradictions_found": [], "ceiling_impacted": False},
                hook_violations=[], success=True,
            ),
            "unusual_regime": SubagentResult(
                subagent_id="unusual_regime",
                tool_name="emit_regime_assessment",
                tool_input={"regime_unusual": False, "regime_factors": [], "reliability_impact": "NONE"},
                hook_violations=[], success=True,
            ),
            "uncertainty_explanation": SubagentResult(
                subagent_id="uncertainty_explanation",
                tool_name="emit_uncertainty_summary",
                tool_input={"uncertainty_tier": "HIGH", "uncertainty_sources": [], "ceiling_impact": "NONE"},
                hook_violations=[], success=True,
            ),
        }
        payload = _assemble_payload(_CITY, _DATE, _RUN, results)
        validation = validate_shadow_output(payload)
        self.assertTrue(validation.passed, f"Schema fail: {validation.failure_reason!r}")


# ── OR2: First subagent failure → BLOCKED ─────────────────────────────────────

class TestOR2FirstSubagentFailure(unittest.TestCase):

    def setUp(self):
        self._ledger = ShadowLedger()
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("first subagent SDK error")
        self._client = client

    def test_OR2_returns_shadow_validation_result(self):
        result = run_shadow_orchestrator(
            _CITY, _DATE, _RUN, self._client, _BOUNDARY, self._ledger
        )
        self.assertIsInstance(result, ShadowValidationResult)

    def test_OR2_blocked_payload_passes_schema(self):
        """BLOCKED is a valid status — the payload should still pass the schema."""
        result = run_shadow_orchestrator(
            _CITY, _DATE, _RUN, self._client, _BOUNDARY, self._ledger
        )
        self.assertTrue(result.passed, f"BLOCKED payload failed schema: {result.failure_reason!r}")

    def test_OR2_ledger_records_blocked_status(self):
        run_shadow_orchestrator(
            _CITY, _DATE, _RUN, self._client, _BOUNDARY, self._ledger
        )
        entry = self._ledger.get_recent(1)[0]
        self.assertEqual(entry.status, "BLOCKED")

    def test_OR2_only_first_sdk_call_made(self):
        """Orchestrator should not continue to later subagents after the first fails."""
        run_shadow_orchestrator(
            _CITY, _DATE, _RUN, self._client, _BOUNDARY, self._ledger
        )
        self.assertEqual(self._client.messages.create.call_count, 1)


# ── OR3: Third subagent failure ────────────────────────────────────────────────

class TestOR3ThirdSubagentFailure(unittest.TestCase):

    def setUp(self):
        self._ledger = ShadowLedger()
        fc_inp = {
            "scoring_mode": "gaussian_forecast",
            "calibration_status": "UNAVAILABLE",
            "uncertainty_tier": "HIGH",
            "recommended_ceiling": "KALSHI_WATCH",
            "blockers": [],
        }
        sr_inp = {
            "sources_present": ["nws_forecast"],
            "sources_missing": [],
            "conflicts": [],
            "reconciliation_status": "OK",
        }
        client = MagicMock()
        client.messages.create.side_effect = [
            _response("emit_forecast_context",      fc_inp),
            _response("emit_source_reconciliation", sr_inp),
            RuntimeError("third subagent crash"),  # contradiction_detection fails
        ]
        self._client = client

    def test_OR3_blocked_after_3_calls(self):
        run_shadow_orchestrator(
            _CITY, _DATE, _RUN, self._client, _BOUNDARY, self._ledger
        )
        self.assertEqual(self._client.messages.create.call_count, 3)
        entry = self._ledger.get_recent(1)[0]
        self.assertEqual(entry.status, "BLOCKED")

    def test_OR3_first_two_subagents_in_ledger_succeeded(self):
        run_shadow_orchestrator(
            _CITY, _DATE, _RUN, self._client, _BOUNDARY, self._ledger
        )
        entry = self._ledger.get_recent(1)[0]
        self.assertIn("forecast_context",      entry.subagents_succeeded)
        self.assertIn("source_reconciliation", entry.subagents_succeeded)
        self.assertIn("contradiction_detection", entry.subagents_failed)


# ── OR4: No DB imports in ledger module ───────────────────────────────────────

class TestOR4NoDbImportsInLedger(unittest.TestCase):

    def test_OR4_ledger_module_has_no_database_import_statements(self):
        module_path = os.path.join(_REPO, "gate_engine", "kalshi_wx_shadow_ledger.py")
        self.assertTrue(os.path.exists(module_path))
        with open(module_path) as f:
            source = f.read()

        forbidden = [
            "psycopg2", "sqlalchemy", "pg8000", "sqlite3",
            "aiopg", "asyncpg", "tortoise", "peewee", "databases",
        ]
        for pkg in forbidden:
            pattern = re.compile(
                rf"^\s*(import\s+{re.escape(pkg)}|from\s+{re.escape(pkg)})",
                re.MULTILINE,
            )
            hits = pattern.findall(source)
            self.assertFalse(
                hits,
                f"DB import {pkg!r} found in ledger module — violates shadow-only invariant",
            )


# ── OR5: Contradiction detection ceiling propagates ──────────────────────────

class TestOR5CeilingPropagation(unittest.TestCase):

    def test_OR5_revised_ceiling_used_when_ceiling_impacted(self):
        results = {
            "forecast_context": SubagentResult(
                subagent_id="forecast_context",
                tool_name="emit_forecast_context",
                tool_input={
                    "scoring_mode": "gaussian_forecast",
                    "calibration_status": "UNAVAILABLE",
                    "uncertainty_tier": "HIGH",
                    "recommended_ceiling": "KALSHI_WATCH",
                    "blockers": [],
                },
                hook_violations=[], success=True,
            ),
            "source_reconciliation": SubagentResult(
                subagent_id="source_reconciliation",
                tool_name="emit_source_reconciliation",
                tool_input={"sources_present": [], "sources_missing": [], "conflicts": [], "reconciliation_status": "OK"},
                hook_violations=[], success=True,
            ),
            "contradiction_detection": SubagentResult(
                subagent_id="contradiction_detection",
                tool_name="emit_contradiction_detection",
                tool_input={
                    "contradictions_found": ["conflict_A"],
                    "ceiling_impacted": True,
                    "revised_ceiling": "KALSHI_REJECT_NO_EDGE",
                },
                hook_violations=[], success=True,
            ),
            "unusual_regime": SubagentResult(
                subagent_id="unusual_regime",
                tool_name="emit_regime_assessment",
                tool_input={"regime_unusual": False, "regime_factors": [], "reliability_impact": "NONE"},
                hook_violations=[], success=True,
            ),
            "uncertainty_explanation": SubagentResult(
                subagent_id="uncertainty_explanation",
                tool_name="emit_uncertainty_summary",
                tool_input={"uncertainty_tier": "HIGH", "uncertainty_sources": [], "ceiling_impact": "NONE"},
                hook_violations=[], success=True,
            ),
        }
        payload = _assemble_payload(_CITY, _DATE, _RUN, results)
        self.assertEqual(payload["recommended_ceiling"], "KALSHI_REJECT_NO_EDGE")
        self.assertIn("conflict_A", payload["source_conflicts"])


# ── OR6: Unusual regime factors become blockers ───────────────────────────────

class TestOR6UnusualRegimeFactor(unittest.TestCase):

    def test_OR6_regime_factors_added_to_blockers(self):
        results = {
            "forecast_context": SubagentResult(
                subagent_id="forecast_context",
                tool_name="emit_forecast_context",
                tool_input={
                    "scoring_mode": "gaussian_forecast",
                    "calibration_status": "UNAVAILABLE",
                    "uncertainty_tier": "HIGH",
                    "recommended_ceiling": "KALSHI_WATCH",
                    "blockers": ["existing_blocker"],
                },
                hook_violations=[], success=True,
            ),
            "source_reconciliation": SubagentResult(
                subagent_id="source_reconciliation",
                tool_name="emit_source_reconciliation",
                tool_input={"sources_present": [], "sources_missing": [], "conflicts": [], "reconciliation_status": "OK"},
                hook_violations=[], success=True,
            ),
            "contradiction_detection": SubagentResult(
                subagent_id="contradiction_detection",
                tool_name="emit_contradiction_detection",
                tool_input={"contradictions_found": [], "ceiling_impacted": False},
                hook_violations=[], success=True,
            ),
            "unusual_regime": SubagentResult(
                subagent_id="unusual_regime",
                tool_name="emit_regime_assessment",
                tool_input={
                    "regime_unusual": True,
                    "regime_factors": ["heat_dome", "above_normal_dewpoint"],
                    "reliability_impact": "SIGNIFICANT",
                },
                hook_violations=[], success=True,
            ),
            "uncertainty_explanation": SubagentResult(
                subagent_id="uncertainty_explanation",
                tool_name="emit_uncertainty_summary",
                tool_input={"uncertainty_tier": "HIGH", "uncertainty_sources": [], "ceiling_impact": "NONE"},
                hook_violations=[], success=True,
            ),
        }
        payload = _assemble_payload(_CITY, _DATE, _RUN, results)
        self.assertIn("existing_blocker", payload["agent_observed_blockers"])
        self.assertIn("heat_dome",              payload["agent_observed_blockers"])
        self.assertIn("above_normal_dewpoint",  payload["agent_observed_blockers"])


# ── OR7: research() with flag=True → ShadowValidationResult ──────────────────

class TestOR7ResearchFlagOn(unittest.TestCase):

    def test_OR7_research_with_valid_mock_returns_shadow_validation_result(self):
        client, _ = _make_5call_mock()

        with patch("gate_engine.kalshi_wx_shadow_client._SHADOW_ENABLED", True):
            rc = KalshiWxShadowResearchClient(sdk_client=client)
            result = rc.research(city=_CITY, date=_DATE, run_id=_RUN)

        self.assertIsInstance(result, ShadowValidationResult)
        self.assertTrue(result.passed, f"Expected pass; got {result.failure_reason!r}")

    def test_OR7_all_5_calls_made(self):
        client, _ = _make_5call_mock()

        with patch("gate_engine.kalshi_wx_shadow_client._SHADOW_ENABLED", True):
            KalshiWxShadowResearchClient(sdk_client=client).research(
                city=_CITY, date=_DATE, run_id=_RUN
            )

        self.assertEqual(client.messages.create.call_count, 5)


# ── OR8: flag-off regression ──────────────────────────────────────────────────

class TestOR8FlagOffRegression(unittest.TestCase):

    def test_OR8_flag_off_returns_shadow_agent_disabled(self):
        strict = MagicMock()
        strict.messages.create.side_effect = AssertionError("must not be called")

        with patch("gate_engine.kalshi_wx_shadow_client._SHADOW_ENABLED", False):
            result = KalshiWxShadowResearchClient(sdk_client=strict).research(
                city=_CITY, date=_DATE, run_id=_RUN
            )

        self.assertFalse(result.passed)
        self.assertTrue(result.shadow_failure_only)
        self.assertIn("SHADOW_AGENT_DISABLED", result.failure_reason)
        strict.messages.create.assert_not_called()


# ── LD1–LD6: Shadow Ledger ────────────────────────────────────────────────────

class TestLD1Record(unittest.TestCase):

    def test_LD1_record_increments_total_recorded(self):
        ledger = ShadowLedger()
        self.assertEqual(ledger.total_recorded(), 0)
        ledger.record("r1", "NYC", "2026-08-08", "COMPLETE", {}, [])
        self.assertEqual(ledger.total_recorded(), 1)
        ledger.record("r2", "CHI", "2026-08-09", "BLOCKED", {}, [])
        self.assertEqual(ledger.total_recorded(), 2)

    def test_LD1_entry_fields_correct(self):
        ledger = ShadowLedger()
        ledger.record("run-x", "NYC", "2026-08-08", "COMPLETE", {}, [{"v": 1}])
        entry = ledger.get_recent(1)[0]
        self.assertIsInstance(entry, ShadowLedgerEntry)
        self.assertEqual(entry.run_id, "run-x")
        self.assertEqual(entry.city, "NYC")
        self.assertEqual(entry.date, "2026-08-08")
        self.assertEqual(entry.status, "COMPLETE")
        self.assertEqual(entry.hook_violations_count, 1)


class TestLD2GetRecent(unittest.TestCase):

    def test_LD2_get_recent_returns_newest_first(self):
        ledger = ShadowLedger()
        for i in range(5):
            ledger.record(f"r{i}", "NYC", "2026-08-08", "COMPLETE", {}, [])
        recent = ledger.get_recent(3)
        self.assertEqual(len(recent), 3)
        # Most recent recorded (r4) should be first
        self.assertEqual(recent[0].run_id, "r4")
        self.assertEqual(recent[1].run_id, "r3")

    def test_LD2_get_recent_empty_ledger_returns_empty(self):
        ledger = ShadowLedger()
        self.assertEqual(ledger.get_recent(5), [])


class TestLD3ViolationCount(unittest.TestCase):

    def test_LD3_violation_count_accumulates(self):
        ledger = ShadowLedger()
        ledger.record("r1", "NYC", "2026-08-08", "COMPLETE", {}, [{"v": 1}, {"v": 2}])
        ledger.record("r2", "NYC", "2026-08-09", "BLOCKED",  {}, [{"v": 3}])
        self.assertEqual(ledger.violation_count(), 3)

    def test_LD3_zero_violations_when_clean(self):
        ledger = ShadowLedger()
        ledger.record("r1", "NYC", "2026-08-08", "COMPLETE", {}, [])
        self.assertEqual(ledger.violation_count(), 0)


class TestLD4Clear(unittest.TestCase):

    def test_LD4_clear_resets_all(self):
        ledger = ShadowLedger()
        ledger.record("r1", "NYC", "2026-08-08", "COMPLETE", {}, [{"v": 1}])
        ledger.clear()
        self.assertEqual(ledger.total_recorded(), 0)
        self.assertEqual(ledger.violation_count(), 0)
        self.assertEqual(ledger.get_recent(10), [])


class TestLD5MaxEntries(unittest.TestCase):

    def test_LD5_oldest_entries_discarded_when_full(self):
        ledger = ShadowLedger(max_entries=3)
        for i in range(5):
            ledger.record(f"r{i}", "NYC", "2026-08-08", "COMPLETE", {}, [])
        recent = ledger.get_recent(10)
        # Should have at most 3 entries
        self.assertLessEqual(len(recent), 3)
        # Should contain the 3 most recent
        ids = {e.run_id for e in recent}
        self.assertIn("r4", ids)
        self.assertIn("r3", ids)
        self.assertNotIn("r0", ids)


class TestLD6ThreadSafety(unittest.TestCase):

    def test_LD6_concurrent_writes_dont_corrupt_count(self):
        ledger = ShadowLedger()
        n_threads = 20
        n_per_thread = 10

        def _write():
            for i in range(n_per_thread):
                ledger.record(f"r-{i}", "NYC", "2026-08-08", "COMPLETE", {}, [])

        threads = [threading.Thread(target=_write) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(ledger.total_recorded(), n_threads * n_per_thread)


# ── OR9: KALSHI_REJECT_UNCALIBRATED removal regression ───────────────────────
#
# Added 2026-08-09 (WOW-PATCH-2026-08-09-KALSHI-WX-UNCALIBRATED-REMOVAL).
# Confirms the removed label cannot re-enter the system via subagent tool enums,
# the orchestrator ceiling validator, or _safe_ceiling().

class TestOR9UncalibratedRemovalRegression(unittest.TestCase):
    """
    Regression: KALSHI_REJECT_UNCALIBRATED was removed from the canonical registry
    because no weather route handler assigns weather_label="WEATHER_REJECT_UNCALIBRATED".
    Confirm the removal propagated to all derived sets in the shadow pipeline.
    """

    def test_OR9a_UNCALIBRATED_not_in_orchestrator_ceiling_capable_labels(self):
        """_CEILING_CAPABLE_LABELS in the orchestrator must not contain the removed label."""
        from gate_engine import kalshi_wx_shadow_orchestrator as orch
        self.assertNotIn(
            "KALSHI_REJECT_UNCALIBRATED",
            orch._CEILING_CAPABLE_LABELS,
            "_CEILING_CAPABLE_LABELS must derive from the canonical registry, "
            "which no longer contains KALSHI_REJECT_UNCALIBRATED",
        )

    def test_OR9b_safe_ceiling_falls_back_for_removed_label(self):
        """_safe_ceiling must NOT accept KALSHI_REJECT_UNCALIBRATED — fall back to KALSHI_WATCH."""
        from gate_engine.kalshi_wx_shadow_orchestrator import _safe_ceiling, _DEFAULT_CEILING
        result = _safe_ceiling("KALSHI_REJECT_UNCALIBRATED")
        self.assertEqual(
            result,
            _DEFAULT_CEILING,
            f"_safe_ceiling('KALSHI_REJECT_UNCALIBRATED') must fall back to "
            f"{_DEFAULT_CEILING!r}, got {result!r}",
        )

    def test_OR9c_UNCALIBRATED_not_in_subagent_valid_ceilings(self):
        """_VALID_CEILINGS in subagents (used for tool-schema enums) must not contain the removed label."""
        from gate_engine import kalshi_wx_shadow_subagents as sa
        self.assertNotIn(
            "KALSHI_REJECT_UNCALIBRATED",
            sa._VALID_CEILINGS,
            "_VALID_CEILINGS must derive from the canonical registry — "
            "KALSHI_REJECT_UNCALIBRATED must be absent",
        )

    def test_OR9d_forecast_context_tool_enum_excludes_removed_label(self):
        """The forecast_context tool schema enum must not expose KALSHI_REJECT_UNCALIBRATED to the model."""
        from gate_engine.kalshi_wx_shadow_subagents import _FC_TOOL_DEF
        # Walk the schema to find the recommended_ceiling enum
        props = _FC_TOOL_DEF["input_schema"]["properties"]
        ceiling_enum = props["recommended_ceiling"]["enum"]
        self.assertNotIn(
            "KALSHI_REJECT_UNCALIBRATED",
            ceiling_enum,
            "forecast_context recommended_ceiling enum must not contain the removed label",
        )

    def test_OR9e_contradiction_detection_tool_enum_excludes_removed_label(self):
        """The contradiction_detection tool schema enum must not expose KALSHI_REJECT_UNCALIBRATED."""
        from gate_engine.kalshi_wx_shadow_subagents import _CD_TOOL_DEF
        props = _CD_TOOL_DEF["input_schema"]["properties"]
        ceiling_enum = props["revised_ceiling"]["enum"]
        self.assertNotIn(
            "KALSHI_REJECT_UNCALIBRATED",
            ceiling_enum,
            "contradiction_detection revised_ceiling enum must not contain the removed label",
        )

    def test_OR9f_valid_ceilings_size_matches_canonical_registry(self):
        """_VALID_CEILINGS size must equal the canonical registry size (5)."""
        from gate_engine import kalshi_wx_shadow_subagents as sa
        from gate_engine.kalshi_wx_terminal_labels import KALSHI_WX_TERMINAL_LABEL_REGISTRY
        self.assertEqual(
            len(sa._VALID_CEILINGS),
            len(KALSHI_WX_TERMINAL_LABEL_REGISTRY),
            f"_VALID_CEILINGS has {len(sa._VALID_CEILINGS)} entries; "
            f"canonical registry has {len(KALSHI_WX_TERMINAL_LABEL_REGISTRY)}; they must match",
        )


if __name__ == "__main__":
    unittest.main()
