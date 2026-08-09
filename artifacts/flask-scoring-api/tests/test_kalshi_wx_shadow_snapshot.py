"""
tests/test_kalshi_wx_shadow_snapshot.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — Step 10C snapshot tests

Tests for gate_engine/kalshi_wx_shadow_snapshot.py and its integration with
the five subagents and orchestrator.

No live API calls are made.  All subagents are driven by mock clients or
patched function references.

Test plan
─────────
SC1: Immutability — WeatherResearchSnapshot is frozen; setting any field
     after construction raises dataclasses.FrozenInstanceError.

SC2: No forbidden governance keys — none of the dataclass field names appear
     in FORBIDDEN_GOVERNANCE_KEYS from gate_engine/kalshi_wx_shadow_schema.py.
     Verified with a static scan of dataclasses.fields(WeatherResearchSnapshot).

SC3: All five subagents receive snapshot data in their user messages —
     when run_*_subagent() is called with snapshot=<instance>, the user
     message passed to client.messages.create() contains the snapshot's
     research_snapshot_id, station, and sigma_f values (not just city/date).

SC4: Object-identical snapshot to all five subagents — when the orchestrator
     runs with snapshot=<instance>, every subagent receives id(snapshot) ==
     id(original_instance), proving one snapshot rather than five copies.
"""
from __future__ import annotations

import dataclasses
import os
import sys
import unittest
from typing import Optional
from unittest.mock import MagicMock

# ── Path setup ────────────────────────────────────────────────────────────────
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from gate_engine.kalshi_wx_shadow_snapshot import (
    WeatherResearchSnapshot,
    build_test_snapshot,
)
from gate_engine.kalshi_wx_shadow_schema import FORBIDDEN_GOVERNANCE_KEYS
from gate_engine.kalshi_wx_shadow_subagents import (
    SubagentResult,
    run_forecast_context_subagent,
    run_source_reconciliation_subagent,
    run_contradiction_detection_subagent,
    run_unusual_regime_subagent,
    run_uncertainty_explanation_subagent,
)
from gate_engine.kalshi_wx_shadow_capability_boundary import CapabilityBoundary


# ── Shared test snapshot ──────────────────────────────────────────────────────

def _make_snap(**overrides) -> WeatherResearchSnapshot:
    """Build a representative snapshot with any field overrides."""
    defaults = dict(
        research_snapshot_id="snap-sc-test-001",
        canonical_event_id="event-chi-20260815",
        city="Chicago",
        station="KMDW",
        market_date="2026-08-15",
        source_cutoff_timestamp="2026-08-14T18:00:00Z",
        nws_gridpoint_forecast={"temperature": 88, "unit": "F", "valid_time": "2026-08-15T18:00:00Z"},
        open_meteo_forecast=None,
        noaa_ncei_forecast=None,
        official_observations_at_cutoff={"temp_f": 86.2, "station": "KMDW"},
        forecast_high_used_by_deterministic_model=88.0,
        weather_data_source_tier="NWS_GRIDPOINT",
        forecast_horizon_hours=24.0,
        sigma_f=4.25,
        deterministic_weather_readiness_state="READY",
        source_timestamps={"nws_gridpoint": "2026-08-14T17:55:00Z"},
        source_provenance={"nws_gridpoint": "api.weather.gov/gridpoints/LOT/74,73/forecast"},
        source_failures=(),
        source_disagreements=(),
    )
    defaults.update(overrides)
    return build_test_snapshot(**defaults)


# ── Mock helpers ──────────────────────────────────────────────────────────────

def _make_capturing_client(tool_name: str, tool_output: dict):
    """
    Returns (client, captured_messages_list).

    client.messages.create(**kwargs) appends kwargs['messages'][0]['content']
    to captured_messages_list and returns a fake tool_use response.
    This lets tests inspect the exact user message sent to the model.
    """
    captured: list[str] = []

    class _FakeInput(dict):
        pass

    class _FakeBlock:
        def __init__(self):
            self.type = "tool_use"
            self.name = tool_name
            self.input = dict(tool_output)  # plain dict — MagicMock safe

    class _FakeResponse:
        def __init__(self):
            self.content = [_FakeBlock()]
            self.stop_reason = "tool_use"

    client = MagicMock()

    def _side_effect(**kwargs):
        msgs = kwargs.get("messages", [])
        if msgs:
            captured.append(msgs[0].get("content", ""))
        return _FakeResponse()

    client.messages.create.side_effect = _side_effect
    return client, captured


# ── Per-subagent configs for SC3 ──────────────────────────────────────────────
# (name, run_fn, tool_name, minimal_valid_tool_output, extra_kwargs_for_run_fn)

_SUBAGENT_CONFIGS = [
    (
        "forecast_context",
        run_forecast_context_subagent,
        "emit_forecast_context",
        {
            "scoring_mode": "gaussian_forecast",
            "calibration_status": "CALIBRATED",
            "uncertainty_tier": "LOW",
            "recommended_ceiling": "KALSHI_WATCH",
            "blockers": [],
        },
        {},  # no extra kwargs
    ),
    (
        "source_reconciliation",
        run_source_reconciliation_subagent,
        "emit_source_reconciliation",
        {
            "sources_present": ["nws_gridpoint"],
            "sources_missing": [],
            "conflicts": [],
            "reconciliation_status": "OK",
        },
        {},
    ),
    (
        "contradiction_detection",
        run_contradiction_detection_subagent,
        "emit_contradiction_detection",
        {
            "contradictions_found": [],
            "ceiling_impacted": False,
        },
        {},  # forecast_context and source_reconciliation default to None
    ),
    (
        "unusual_regime",
        run_unusual_regime_subagent,
        "emit_regime_assessment",
        {
            "regime_unusual": False,
            "regime_factors": [],
            "reliability_impact": "NONE",
        },
        {},
    ),
    (
        "uncertainty_explanation",
        run_uncertainty_explanation_subagent,
        "emit_uncertainty_summary",
        {
            "uncertainty_tier": "LOW",
            "uncertainty_sources": ["forecast_horizon"],
            "ceiling_impact": "NONE",
        },
        {},  # forecast_context, contradiction_detection, unusual_regime default to None
    ),
]


# ── SC1: Immutability ─────────────────────────────────────────────────────────

class TestSC1Immutability(unittest.TestCase):

    def test_SC1_setattr_raises_frozen_instance_error(self):
        """
        WeatherResearchSnapshot is a frozen dataclass.
        Attempting to set any attribute after construction must raise
        dataclasses.FrozenInstanceError (a subclass of AttributeError).
        """
        snap = _make_snap()
        with self.assertRaises((dataclasses.FrozenInstanceError, AttributeError)):
            snap.city = "Miami"

    def test_SC1_setattr_any_field_raises(self):
        """
        Frozen constraint applies to ALL fields, not just 'city'.

        Uses the builtin setattr() — NOT object.__setattr__() — because
        object.__setattr__() bypasses the Python-level __setattr__ hook that
        frozen dataclasses install, and would therefore succeed silently.
        setattr() correctly routes through type(snap).__setattr__, which raises
        FrozenInstanceError.
        """
        snap = _make_snap()
        for f in dataclasses.fields(snap):
            with self.subTest(field=f.name):
                with self.assertRaises((dataclasses.FrozenInstanceError, AttributeError)):
                    setattr(snap, f.name, None)

    def test_SC1_two_snapshots_same_values_are_equal(self):
        """Frozen dataclasses with identical field values compare equal."""
        a = _make_snap()
        b = _make_snap()
        self.assertEqual(a, b)

    def test_SC1_source_failures_is_tuple(self):
        """source_failures field is typed as tuple — not a list — to prevent mutation."""
        snap = _make_snap(source_failures=("open_meteo: 503",))
        self.assertIsInstance(snap.source_failures, tuple)

    def test_SC1_source_disagreements_is_tuple(self):
        """source_disagreements field is typed as tuple."""
        snap = _make_snap(source_disagreements=("nws vs open_meteo: 4°F delta",))
        self.assertIsInstance(snap.source_disagreements, tuple)


# ── SC2: No forbidden governance keys ────────────────────────────────────────

class TestSC2NoForbiddenGovernanceKeys(unittest.TestCase):

    def test_SC2_no_field_name_in_forbidden_governance_keys(self):
        """
        No field name on WeatherResearchSnapshot appears in FORBIDDEN_GOVERNANCE_KEYS.
        Uses the same frozenset imported from gate_engine/kalshi_wx_shadow_schema.py.
        """
        field_names = {f.name for f in dataclasses.fields(WeatherResearchSnapshot)}
        violations = field_names & FORBIDDEN_GOVERNANCE_KEYS
        self.assertEqual(
            violations,
            set(),
            f"WeatherResearchSnapshot has field(s) matching FORBIDDEN_GOVERNANCE_KEYS: "
            f"{sorted(violations)}.  Remove or rename them.",
        )

    def test_SC2_forbidden_keys_frozenset_is_non_empty(self):
        """Sanity check: FORBIDDEN_GOVERNANCE_KEYS is non-empty so the above test is meaningful."""
        self.assertGreater(len(FORBIDDEN_GOVERNANCE_KEYS), 0)

    def test_SC2_known_governance_keys_not_present(self):
        """Spot-check a sample of governance key names that must never appear."""
        field_names = {f.name for f in dataclasses.fields(WeatherResearchSnapshot)}
        for forbidden in ("label", "can_execute", "capital_allocation",
                          "execution_permission", "terminal_label", "trade_authorization"):
            with self.subTest(key=forbidden):
                self.assertNotIn(
                    forbidden, field_names,
                    f"Governance key {forbidden!r} must not be a WeatherResearchSnapshot field.",
                )


# ── SC3: All five subagents receive snapshot data in user messages ────────────

class TestSC3SnapshotDataInUserMessages(unittest.TestCase):
    """
    When each run_*_subagent() function is called with snapshot=<instance>,
    the user message forwarded to client.messages.create() must contain the
    snapshot's research_snapshot_id, station, and sigma_f — proving the richer
    evidence block is being used rather than the old city/date-only format.
    """

    _SNAP = _make_snap(
        research_snapshot_id="SC3-UNIQUE-ID",
        station="KMDW",
        sigma_f=7.77,
    )
    _BOUNDARY = CapabilityBoundary()
    _CONTEXT = {"run_id": "run-sc3"}

    def _assert_snapshot_in_message(
        self,
        subagent_label: str,
        run_fn,
        tool_name: str,
        tool_output: dict,
        extra_kwargs: dict,
    ) -> None:
        """
        Helper: run subagent with the shared snapshot and assert key fields
        appear in the user message captured from messages.create().
        """
        client, captured = _make_capturing_client(tool_name, tool_output)

        run_fn(
            client,
            self._CONTEXT,
            self._BOUNDARY,
            snapshot=self._SNAP,
            **extra_kwargs,
        )

        self.assertEqual(
            len(captured), 1,
            f"{subagent_label}: expected exactly one SDK call, got {len(captured)}",
        )
        msg = captured[0]

        # research_snapshot_id uniquely identifies the snapshot in the message
        self.assertIn(
            self._SNAP.research_snapshot_id, msg,
            f"{subagent_label}: research_snapshot_id not found in user message",
        )

        # station is only present when snapshot is used (not in old city/date format)
        self.assertIn(
            self._SNAP.station, msg,
            f"{subagent_label}: station {self._SNAP.station!r} not found in user message",
        )

        # sigma_f must appear in the evidence block
        self.assertIn(
            "sigma_f", msg,
            f"{subagent_label}: 'sigma_f' key not found in user message",
        )

    def test_SC3_forecast_context_receives_snapshot_data(self):
        name, fn, tool, output, extra = _SUBAGENT_CONFIGS[0]
        self._assert_snapshot_in_message(name, fn, tool, output, extra)

    def test_SC3_source_reconciliation_receives_snapshot_data(self):
        name, fn, tool, output, extra = _SUBAGENT_CONFIGS[1]
        self._assert_snapshot_in_message(name, fn, tool, output, extra)

    def test_SC3_contradiction_detection_receives_snapshot_data(self):
        name, fn, tool, output, extra = _SUBAGENT_CONFIGS[2]
        self._assert_snapshot_in_message(name, fn, tool, output, extra)

    def test_SC3_unusual_regime_receives_snapshot_data(self):
        name, fn, tool, output, extra = _SUBAGENT_CONFIGS[3]
        self._assert_snapshot_in_message(name, fn, tool, output, extra)

    def test_SC3_uncertainty_explanation_receives_snapshot_data(self):
        name, fn, tool, output, extra = _SUBAGENT_CONFIGS[4]
        self._assert_snapshot_in_message(name, fn, tool, output, extra)

    def test_SC3_fallback_without_snapshot_uses_city_date(self):
        """
        Backward compatibility: when snapshot is None (default), the user message
        still uses the old city/date format and does NOT contain research_snapshot_id.
        """
        name, fn, tool, output, extra = _SUBAGENT_CONFIGS[0]
        client, captured = _make_capturing_client(tool, output)
        ctx = {"city": "Los Angeles", "date": "2026-08-15", "run_id": "fallback-run"}

        fn(client, ctx, self._BOUNDARY, snapshot=None, **extra)

        self.assertEqual(len(captured), 1)
        msg = captured[0]
        self.assertNotIn("SC3-UNIQUE-ID", msg,
                         "Old fallback path must not inject research_snapshot_id")
        self.assertIn("Los Angeles", msg,
                      "Old fallback path must include city in message")


# ── SC4: Object-identical snapshot to all five subagents ─────────────────────

class TestSC4ObjectIdenticalSnapshotToAllSubagents(unittest.TestCase):
    """
    The orchestrator must pass the SAME snapshot instance (not a copy) to
    all five subagents.  Verified by capturing id(snapshot) at each subagent
    call site and asserting all five ids are equal.
    """

    # Minimal valid tool_input dicts for each subagent (orchestrator reads them)
    _FC  = SubagentResult(subagent_id="forecast_context",
                          tool_name="emit_forecast_context",
                          tool_input={"scoring_mode": "gaussian_forecast",
                                      "calibration_status": "CALIBRATED",
                                      "uncertainty_tier": "LOW",
                                      "recommended_ceiling": "KALSHI_WATCH",
                                      "blockers": []},
                          hook_violations=[], success=True, turns_used=1)
    _SR  = SubagentResult(subagent_id="source_reconciliation",
                          tool_name="emit_source_reconciliation",
                          tool_input={"sources_present": [], "sources_missing": [],
                                      "conflicts": [], "reconciliation_status": "OK"},
                          hook_violations=[], success=True, turns_used=1)
    _CD  = SubagentResult(subagent_id="contradiction_detection",
                          tool_name="emit_contradiction_detection",
                          tool_input={"contradictions_found": [], "ceiling_impacted": False},
                          hook_violations=[], success=True, turns_used=1)
    _UR  = SubagentResult(subagent_id="unusual_regime",
                          tool_name="emit_regime_assessment",
                          tool_input={"regime_unusual": False, "regime_factors": [],
                                      "reliability_impact": "NONE"},
                          hook_violations=[], success=True, turns_used=1)
    _UE  = SubagentResult(subagent_id="uncertainty_explanation",
                          tool_name="emit_uncertainty_summary",
                          tool_input={"uncertainty_tier": "LOW",
                                      "uncertainty_sources": [],
                                      "ceiling_impact": "NONE"},
                          hook_violations=[], success=True, turns_used=1)

    def _run_orchestrator_with_patched_subagents(
        self, snap: WeatherResearchSnapshot
    ) -> list[int]:
        """
        Patch all five subagent functions in the orchestrator module so they
        capture id(snapshot) instead of making SDK calls.  Returns the list
        of captured ids (one per subagent, in execution order).
        """
        import gate_engine.kalshi_wx_shadow_orchestrator as _orch
        from gate_engine.kalshi_wx_shadow_ledger import ShadowLedger
        from gate_engine.kalshi_wx_shadow_capability_boundary import CapabilityBoundary
        from gate_engine.kalshi_wx_shadow_orchestrator import run_shadow_orchestrator

        captured_ids: list[int] = []

        def make_capturer(result: SubagentResult):
            def _fake(client, context, capability_boundary, snapshot=None, **kwargs):
                captured_ids.append(id(snapshot))
                return result
            return _fake

        # Save originals; restore in finally
        _orig = {
            "fc": _orch.run_forecast_context_subagent,
            "sr": _orch.run_source_reconciliation_subagent,
            "cd": _orch.run_contradiction_detection_subagent,
            "ur": _orch.run_unusual_regime_subagent,
            "ue": _orch.run_uncertainty_explanation_subagent,
        }
        try:
            _orch.run_forecast_context_subagent       = make_capturer(self._FC)
            _orch.run_source_reconciliation_subagent  = make_capturer(self._SR)
            _orch.run_contradiction_detection_subagent= make_capturer(self._CD)
            _orch.run_unusual_regime_subagent         = make_capturer(self._UR)
            _orch.run_uncertainty_explanation_subagent= make_capturer(self._UE)

            run_shadow_orchestrator(
                city=snap.city,
                date=snap.market_date,
                run_id=snap.research_snapshot_id,
                sdk_client=MagicMock(),
                capability_boundary=CapabilityBoundary(),
                ledger=ShadowLedger(),
                snapshot=snap,
            )
        finally:
            _orch.run_forecast_context_subagent       = _orig["fc"]
            _orch.run_source_reconciliation_subagent  = _orig["sr"]
            _orch.run_contradiction_detection_subagent= _orig["cd"]
            _orch.run_unusual_regime_subagent         = _orig["ur"]
            _orch.run_uncertainty_explanation_subagent= _orig["ue"]

        return captured_ids

    def test_SC4_all_five_subagents_receive_same_snapshot_object(self):
        """
        All five subagents must receive the SAME snapshot instance — verified
        by object identity (id()), not just equality.
        """
        snap = _make_snap(research_snapshot_id="snap-sc4-identity")
        captured_ids = self._run_orchestrator_with_patched_subagents(snap)

        self.assertEqual(
            len(captured_ids), 5,
            f"Expected 5 subagent calls; got {len(captured_ids)}",
        )
        self.assertEqual(
            len(set(captured_ids)), 1,
            f"All 5 subagents must receive the same snapshot object (same id). "
            f"Got {len(set(captured_ids))} distinct ids: {captured_ids}",
        )
        self.assertEqual(
            captured_ids[0], id(snap),
            "The captured snapshot id must match id(original_snapshot)",
        )

    def test_SC4_snapshot_none_still_runs_all_five_subagents(self):
        """
        When snapshot=None (legacy path), the orchestrator still calls all 5
        subagents.  All five captured ids are id(None).
        """
        snap = _make_snap(research_snapshot_id="snap-sc4-none")
        captured_ids = self._run_orchestrator_with_patched_subagents.__func__(
            self, snap
        )

        # Re-run with snapshot=None by directly calling the helper's logic
        import gate_engine.kalshi_wx_shadow_orchestrator as _orch
        from gate_engine.kalshi_wx_shadow_ledger import ShadowLedger
        from gate_engine.kalshi_wx_shadow_capability_boundary import CapabilityBoundary
        from gate_engine.kalshi_wx_shadow_orchestrator import run_shadow_orchestrator

        none_ids: list[int] = []

        def make_capturer(result):
            def _fake(client, context, capability_boundary, snapshot=None, **kwargs):
                none_ids.append(id(snapshot))
                return result
            return _fake

        _orig = {
            "fc": _orch.run_forecast_context_subagent,
            "sr": _orch.run_source_reconciliation_subagent,
            "cd": _orch.run_contradiction_detection_subagent,
            "ur": _orch.run_unusual_regime_subagent,
            "ue": _orch.run_uncertainty_explanation_subagent,
        }
        try:
            _orch.run_forecast_context_subagent       = make_capturer(self._FC)
            _orch.run_source_reconciliation_subagent  = make_capturer(self._SR)
            _orch.run_contradiction_detection_subagent= make_capturer(self._CD)
            _orch.run_unusual_regime_subagent         = make_capturer(self._UR)
            _orch.run_uncertainty_explanation_subagent= make_capturer(self._UE)

            run_shadow_orchestrator(
                city="NYC", date="2026-08-15", run_id="run-none",
                sdk_client=MagicMock(),
                capability_boundary=CapabilityBoundary(),
                ledger=ShadowLedger(),
                snapshot=None,  # explicit None
            )
        finally:
            _orch.run_forecast_context_subagent       = _orig["fc"]
            _orch.run_source_reconciliation_subagent  = _orig["sr"]
            _orch.run_contradiction_detection_subagent= _orig["cd"]
            _orch.run_unusual_regime_subagent         = _orig["ur"]
            _orch.run_uncertainty_explanation_subagent= _orig["ue"]

        self.assertEqual(len(none_ids), 5)
        self.assertEqual(len(set(none_ids)), 1,
                         "All 5 subagents receive None → all id(None) must be equal")
        self.assertEqual(none_ids[0], id(None))


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main()
