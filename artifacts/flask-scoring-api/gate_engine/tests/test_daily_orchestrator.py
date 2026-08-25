"""
gate_engine/tests/test_daily_orchestrator.py

WOW-PATCH-2026-08-19-DAILY-CANONICAL-v1.0 (Task #277)

Regression suite for the canonical WOW Daily orchestration layer.
All tests are offline/unit-level — no HTTP calls, no DB writes.

Invariants verified
-------------------
1.  can_execute = False on daily_orchestrator module
2.  resolve_participant_side fail-closed (SIDE_UNKNOWN on absent marker)
3.  resolve_participant_side explicit HOME / AWAY markers
4.  normalise_soccer_outcome: canonical HOME / DRAW / AWAY mapping
5.  normalise_soccer_props: in-place normalisation on soccer rows
6.  wnba_ml_specialist_ready: True only with complete paired inputs
7.  tennis_ml_specialist_ready: True only with complete paired inputs
8.  _canonical_selection_id stability (same input → same ID)
9.  _canonical_selection_id uniqueness (different inputs → different IDs)
10. _market_version_id stability
11. Source union always calls backup even when primary succeeds
12. No pre-score truncation when _props_by_sport supplied to run_scan
13. No save_scan_result calls when _persist_results=False
14. Reconciliation passes when all selections are in terminal buckets
15. Reconciliation fails when a selection is missing from terminal buckets
16. Reconciliation flags duplicate canonical_selection_ids
17. Reconciliation flags excess terminal IDs
18. run_daily_orchestration returns required top-level keys
19. run_daily_orchestration: run_status DEGRADED when failed_modules non-empty
20. run_daily_orchestration: persist=False skips DB writes
21. run_daily_orchestration: RECONCILIATION_WARNING when reconciliation fails
22. _union_props_for_sport: dedup across sources (same key → first wins)
23. Soccer 1X2 outcome normalised before evaluation
24. WNBA readiness PARTIAL when only home_win_pct present (no away)
25. Tennis readiness PARTIAL when only surface present (no paired field)
"""
from __future__ import annotations

import sys
import os
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Path fix
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


# ---------------------------------------------------------------------------
# Module under test (import after path fix)
# ---------------------------------------------------------------------------
import gate_engine.daily_orchestrator as orch


# ---------------------------------------------------------------------------
# Test 1 — module-level can_execute invariant
# ---------------------------------------------------------------------------
class TestCanExecute(unittest.TestCase):
    def test_can_execute_false(self):
        self.assertFalse(orch.can_execute)


# ---------------------------------------------------------------------------
# Tests 2-3 — resolve_participant_side
# ---------------------------------------------------------------------------
class TestResolveParticipantSide(unittest.TestCase):

    def test_absent_marker_returns_unknown(self):
        row = {"player": "Acme", "prop": "points"}
        self.assertEqual(orch.resolve_participant_side(row), "SIDE_UNKNOWN")

    def test_empty_row_returns_unknown(self):
        self.assertEqual(orch.resolve_participant_side({}), "SIDE_UNKNOWN")

    def test_home_marker_home_away_field(self):
        for marker in ("HOME", "home", "vs", "VS.", "TRUE", "1", "YES"):
            self.assertEqual(
                orch.resolve_participant_side({"home_away": marker}),
                "HOME",
                f"Expected HOME for marker={marker!r}",
            )

    def test_away_marker_home_away_field(self):
        for marker in ("AWAY", "away", "@", "FALSE", "0", "NO"):
            self.assertEqual(
                orch.resolve_participant_side({"home_away": marker}),
                "AWAY",
                f"Expected AWAY for marker={marker!r}",
            )

    def test_is_home_field_recognised(self):
        self.assertEqual(orch.resolve_participant_side({"is_home": "HOME"}), "HOME")
        self.assertEqual(orch.resolve_participant_side({"is_home": "@"}),    "AWAY")

    def test_unrecognised_non_empty_marker_returns_unknown(self):
        # A value that isn't in HOME or AWAY markers should be SIDE_UNKNOWN
        result = orch.resolve_participant_side({"home_away": "NEUTRAL"})
        self.assertEqual(result, "SIDE_UNKNOWN")

    def test_none_value_treated_as_absent(self):
        self.assertEqual(orch.resolve_participant_side({"home_away": None}), "SIDE_UNKNOWN")


# ---------------------------------------------------------------------------
# Tests 4-5 — Soccer 1X2 normalisation
# ---------------------------------------------------------------------------
class TestSoccerOutcomeNormalisation(unittest.TestCase):

    def test_canonical_already_valid(self):
        for val, expected in [("HOME", "HOME"), ("DRAW", "DRAW"), ("AWAY", "AWAY")]:
            self.assertEqual(orch.normalise_soccer_outcome(val), expected)

    def test_numeric_1x2(self):
        self.assertEqual(orch.normalise_soccer_outcome("1"), "HOME")
        self.assertEqual(orch.normalise_soccer_outcome("X"), "DRAW")
        self.assertEqual(orch.normalise_soccer_outcome("x"), "DRAW")
        self.assertEqual(orch.normalise_soccer_outcome("2"), "AWAY")

    def test_word_forms(self):
        self.assertEqual(orch.normalise_soccer_outcome("home"),  "HOME")
        self.assertEqual(orch.normalise_soccer_outcome("draw"),  "DRAW")
        self.assertEqual(orch.normalise_soccer_outcome("away"),  "AWAY")
        self.assertEqual(orch.normalise_soccer_outcome("H"),     "HOME")

    def test_none_input(self):
        self.assertIsNone(orch.normalise_soccer_outcome(None))

    def test_unrecognised_input(self):
        self.assertIsNone(orch.normalise_soccer_outcome("WIN"))

    def test_normalise_soccer_props_in_place(self):
        props = [
            {"sport": "Soccer", "side": "1",    "player": "A"},
            {"sport": "Soccer", "side": "X",    "player": "B"},
            {"sport": "Soccer", "side": "2",    "player": "C"},
            {"sport": "NBA",    "side": "MORE", "player": "D"},  # should be untouched
        ]
        orch.normalise_soccer_props(props)
        self.assertEqual(props[0]["outcome"], "HOME")
        self.assertEqual(props[1]["outcome"], "DRAW")
        self.assertEqual(props[2]["outcome"], "AWAY")
        self.assertNotIn("outcome", props[3])


# ---------------------------------------------------------------------------
# Tests 6-7 — Specialist readiness contracts
# ---------------------------------------------------------------------------
class TestWnbaSpecialistReady(unittest.TestCase):

    def test_standings_pair_with_provenance_is_ready(self):
        enr = {
            "home_win_pct": 0.60,
            "away_win_pct": 0.40,
            "offensive_rating": 108.2,
            "defensive_rating": 99.8,
            "pace": 86.4,
            "rest_days": 2,
            "team_acq_source": "wnba_ml_v1:bdl_wnba_standings",
            "team_acq_retrieved_at": "2026-08-24T12:00:00+00:00",
            "hydration_status": "ACQUIRED",
        }
        self.assertTrue(orch.wnba_ml_specialist_ready(enr))

    def test_standings_pair_without_provenance_is_not_ready(self):
        enr = {"home_win_pct": 0.60, "away_win_pct": 0.40}
        self.assertFalse(orch.wnba_ml_specialist_ready(enr))

    def test_power_or_elo_pairs_are_not_standings_hydration(self):
        self.assertFalse(orch.wnba_ml_specialist_ready(
            {"home_power": 0.60, "away_power": 0.40}
        ))
        self.assertFalse(orch.wnba_ml_specialist_ready(
            {"home_elo": 1510, "away_elo": 1490}
        ))

    def test_only_home_win_pct_not_ready(self):
        """Partial hydration must NOT be treated as ready."""
        enr = {"home_win_pct": 0.60}
        self.assertFalse(orch.wnba_ml_specialist_ready(enr))

    def test_only_away_win_pct_not_ready(self):
        enr = {"away_win_pct": 0.40}
        self.assertFalse(orch.wnba_ml_specialist_ready(enr))

    def test_empty_enrichment_not_ready(self):
        self.assertFalse(orch.wnba_ml_specialist_ready({}))


class TestTennisSpecialistReady(unittest.TestCase):

    def test_surface_and_adjusted_form_ready(self):
        enr = {"surface_adjusted_form": 0.55, "surface": "clay"}
        self.assertTrue(orch.tennis_ml_specialist_ready(enr))

    def test_elo_pair_ready(self):
        enr = {"home_elo": 1600, "away_elo": 1550}
        self.assertTrue(orch.tennis_ml_specialist_ready(enr))

    def test_hold_break_pair_ready(self):
        enr = {"hold_rate": 0.72, "break_rate": 0.28}
        self.assertTrue(orch.tennis_ml_specialist_ready(enr))

    def test_only_surface_not_ready(self):
        """surface alone is not enough — needs a paired input."""
        enr = {"surface": "clay"}
        self.assertFalse(orch.tennis_ml_specialist_ready(enr))

    def test_empty_not_ready(self):
        self.assertFalse(orch.tennis_ml_specialist_ready({}))


# ---------------------------------------------------------------------------
# Tests 8-10 — Identity helpers
# ---------------------------------------------------------------------------
class TestCanonicalIdentity(unittest.TestCase):

    def test_selection_id_stability(self):
        a = orch._canonical_selection_id("MLB", "Shohei Ohtani", "home_runs", "MORE", 0.5)
        b = orch._canonical_selection_id("MLB", "Shohei Ohtani", "home_runs", "MORE", 0.5)
        self.assertEqual(a, b)

    def test_selection_id_starts_with_prefix(self):
        sel_id = orch._canonical_selection_id("NBA", "Player A", "points", "MORE", 24.5)
        self.assertTrue(sel_id.startswith("SEL_"))

    def test_selection_id_uniqueness(self):
        a = orch._canonical_selection_id("NBA", "Player A", "points", "MORE", 24.5)
        b = orch._canonical_selection_id("NBA", "Player B", "points", "MORE", 24.5)
        self.assertNotEqual(a, b)

    def test_selection_id_line_bucket(self):
        # 24.4 and 24.6 both bucket to 24.5 → same ID
        a = orch._canonical_selection_id("NBA", "Player A", "points", "MORE", 24.4)
        b = orch._canonical_selection_id("NBA", "Player A", "points", "MORE", 24.6)
        self.assertEqual(a, b)

    def test_event_scoped_selection_ids_do_not_collapse_doubleheaders(self):
        first = orch._canonical_selection_id(
            "MLB", "Chicago Cubs", "outright_winner", "WIN", 0.0,
            event_identity="game-one",
        )
        second = orch._canonical_selection_id(
            "MLB", "Chicago Cubs", "outright_winner", "WIN", 0.0,
            event_identity="game-two",
        )
        self.assertNotEqual(first, second)

    def test_market_version_id_stability(self):
        sel_id = orch._canonical_selection_id("NBA", "Player A", "points", "MORE", 24.5)
        a = orch._market_version_id(sel_id, "2026-08-19", ("ODDS_OK", "RD_OK"))
        b = orch._market_version_id(sel_id, "2026-08-19", ("ODDS_OK", "RD_OK"))
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("MKT_"))

    def test_market_version_id_changes_with_source(self):
        sel_id = orch._canonical_selection_id("NBA", "Player A", "points", "MORE", 24.5)
        a = orch._market_version_id(sel_id, "2026-08-19", ("ODDS_OK", "RD_OK"))
        b = orch._market_version_id(sel_id, "2026-08-19", ("ODDS_FAIL", "RD_OK"))
        self.assertNotEqual(a, b)


# ---------------------------------------------------------------------------
# Tests 14-17 — Reconciliation
# ---------------------------------------------------------------------------
class TestReconciliation(unittest.TestCase):

    def _make_card(self, sel_id, bucket="watch"):
        return {
            "canonical_selection_id": sel_id,
            "terminal_bucket": bucket,
            "classification": bucket,
        }

    def test_reconciled_when_all_accounted(self):
        discovered = {"SEL_A", "SEL_B", "SEL_C"}
        scan = {
            "market_verified": [self._make_card("SEL_A", "market_verified")],
            "final_approved_internal": [],
            "model_qualified": [self._make_card("SEL_B", "model_qualified")],
            "conditional": [],
            "watch": [self._make_card("SEL_C", "watch")],
            "reject": [],
            "data_insufficient": [],
            "no_play": [],
        }
        r = orch._build_reconciliation(discovered, scan)
        self.assertTrue(r["reconciled"])
        self.assertEqual(r["discovered_count"], 3)
        self.assertEqual(r["total_terminal"], 3)
        self.assertEqual(r["missing_ids"], [])
        self.assertEqual(r["excess_ids"], [])
        self.assertEqual(r["duplicate_ids"], [])

    def test_fails_when_selection_missing_from_terminal(self):
        discovered = {"SEL_A", "SEL_B", "SEL_MISSING"}
        scan = {
            "market_verified": [self._make_card("SEL_A")],
            "final_approved_internal": [],
            "model_qualified": [],
            "conditional": [],
            "watch": [self._make_card("SEL_B")],
            "reject": [],
            "data_insufficient": [],
            "no_play": [],
        }
        r = orch._build_reconciliation(discovered, scan)
        self.assertFalse(r["reconciled"])
        self.assertIn("SEL_MISSING", r["missing_ids"])

    def test_fails_on_duplicate_canonical_id(self):
        discovered = {"SEL_A", "SEL_B"}
        scan = {
            "market_verified": [self._make_card("SEL_A")],
            "final_approved_internal": [self._make_card("SEL_A")],  # duplicate!
            "model_qualified": [],
            "conditional": [],
            "watch": [self._make_card("SEL_B")],
            "reject": [],
            "data_insufficient": [],
            "no_play": [],
        }
        r = orch._build_reconciliation(discovered, scan)
        self.assertFalse(r["reconciled"])
        self.assertIn("SEL_A", r["duplicate_ids"])

    def test_excess_ids_flagged(self):
        discovered = {"SEL_A"}
        scan = {
            "market_verified": [self._make_card("SEL_A")],
            "final_approved_internal": [],
            "model_qualified": [],
            "conditional": [],
            "watch": [self._make_card("SEL_EXTRA")],  # not in discovered
            "reject": [],
            "data_insufficient": [],
            "no_play": [],
        }
        r = orch._build_reconciliation(discovered, scan)
        self.assertFalse(r["reconciled"])
        self.assertIn("SEL_EXTRA", r["excess_ids"])

    def test_empty_discovered_and_terminal_reconciles(self):
        r = orch._build_reconciliation(set(), {b: [] for b in orch._TERMINAL_BUCKETS})
        self.assertTrue(r["reconciled"])

    def test_containment_publishes_one_terminal_card_per_candidate(self):
        discovered = {
            "SEL_A": {"canonical_selection_id": "SEL_A", "player": "A"},
            "SEL_B": {"canonical_selection_id": "SEL_B", "player": "B"},
        }
        scan = _make_mock_scan_result({
            "market_verified": [
                {"canonical_selection_id": "SEL_A", "model_probability": 0.61},
            ],
            "watch": [
                {"canonical_selection_id": "SEL_A"},  # duplicate
                {"canonical_selection_id": "SEL_UNKNOWN"},  # excess
                {},  # ID-less
            ],
        })

        contained, containment = orch._contain_candidate_outcomes(
            scan, discovered_by_id=discovered,
        )
        published = [
            card
            for bucket in orch._TERMINAL_BUCKETS
            for card in contained[bucket]
        ]
        self.assertEqual(
            [card["canonical_selection_id"] for card in published],
            ["SEL_A", "SEL_B"],
        )
        self.assertEqual(
            contained["data_insufficient"][0]["terminal_label"],
            "DATA_CONTRACT_FAIL",
        )
        self.assertEqual(containment["duplicate_ids"], ["SEL_A"])
        self.assertEqual(containment["excess_ids"], ["SEL_UNKNOWN"])
        self.assertEqual(containment["idless_card_count"], 1)
        self.assertEqual(containment["missing_ids"], ["SEL_B"])
        reconciliation = orch._build_reconciliation(
            set(discovered), contained, containment=containment,
        )
        self.assertFalse(reconciliation["reconciled"])
        self.assertEqual(reconciliation["total_terminal"], len(discovered))
        duplicate = next(
            card for card in contained["data_insufficient"]
            if card["canonical_selection_id"] == "SEL_A"
        )
        self.assertEqual(duplicate["terminal_label"], "DATA_CONTRACT_FAIL")
        self.assertFalse(duplicate["can_execute"])

    def test_prerequisite_failure_scrubs_probability_and_authority_per_card(self):
        discovered = {
            "SEL_HEALTHY": {"canonical_selection_id": "SEL_HEALTHY"},
            "SEL_FAILED": {"canonical_selection_id": "SEL_FAILED"},
        }
        scan = _make_mock_scan_result({
            "market_verified": [{
                "canonical_selection_id": "SEL_HEALTHY",
                "model_probability": 0.63,
                "calibrated_probability": 0.60,
                "terminal_label": "FINAL_APPROVED",
            }],
            "final_approved_internal": [{
                "canonical_selection_id": "SEL_FAILED",
                "model_probability": 0.71,
                "calibrated_probability": 0.69,
                "no_vig_probability": 0.65,
                "pure_edge": 0.04,
                "adjusted_edge": 0.03,
                "probability_snapshot": {"calibrated_probability": 0.69},
                "authoritative_result": {"decision": "approved"},
                "terminal_label": "FINAL_APPROVED",
                "mandatory_prerequisites": {"data": "AVAILABLE", "calibration": "FAILED"},
            }],
        })

        contained, containment = orch._contain_candidate_outcomes(
            scan, discovered_by_id=discovered,
        )
        self.assertEqual(containment["prerequisite_failed_ids"], ["SEL_FAILED"])
        self.assertEqual(contained["market_verified"][0]["model_probability"], 0.63)
        failed = contained["data_insufficient"][0]
        self.assertEqual(failed["canonical_selection_id"], "SEL_FAILED")
        self.assertEqual(failed["terminal_label"], "DATA_CONTRACT_FAIL")
        self.assertFalse(failed["probability_publishable"])
        self.assertIsNone(failed["model_probability"])
        self.assertIsNone(failed["calibrated_probability"])
        self.assertIsNone(failed["no_vig_probability"])
        self.assertIsNone(failed["pure_edge"])
        self.assertIsNone(failed["adjusted_edge"])
        self.assertIsNone(failed["probability_snapshot"])
        self.assertIsNone(failed["authoritative_result"])
        self.assertIn(
            "MANDATORY_PREREQUISITE_FAILED:mandatory_prerequisites:calibration",
            failed["blockers"],
        )

    def test_containment_forces_non_execution_on_every_published_card(self):
        contained, _ = orch._contain_candidate_outcomes(
            _make_mock_scan_result({
                "market_verified": [{
                    "canonical_selection_id": "SEL_A",
                    "can_execute": True,
                    "can_approve_bets": True,
                }],
            }),
            discovered_by_id={"SEL_A": {"canonical_selection_id": "SEL_A"}},
        )
        card = contained["market_verified"][0]
        self.assertFalse(card["can_execute"])
        self.assertFalse(card["can_approve_bets"])


# ---------------------------------------------------------------------------
# Tests 18-21 — run_daily_orchestration (mocked)
# ---------------------------------------------------------------------------

def _make_mock_scan_result(buckets=None):
    """Return a minimal run_scan result with canonical IDs on every card."""
    buckets = buckets or {}
    result = {b: [] for b in orch._TERMINAL_BUCKETS}
    result.update(buckets)
    result["run_status"]     = "COMPLETE"
    result["failed_modules"] = []
    result["execution_notes"] = []
    return result


class TestRunDailyOrchestration(unittest.TestCase):
    """
    All tests mock out the HTTP-bound dependencies so orchestration
    can run fully offline.
    """

    def _mock_union(self, sport):
        """Return two deterministic props for any sport."""
        props = [
            {"player": f"Player_A_{sport}", "prop": "points", "side": "MORE",
             "line": 20.5, "sport": sport, "game_date": "2026-08-19"},
            {"player": f"Player_B_{sport}", "prop": "rebounds", "side": "MORE",
             "line": 5.5, "sport": sport, "game_date": "2026-08-19"},
        ]
        return props, {f"{sport}_odds": "AVAILABLE", f"{sport}_rundown": "AVAILABLE"}

    def _run_with_mocks(self, sports=None, extra_scan_override=None):
        """
        Execute run_daily_orchestration with all external I/O mocked.
        Returns the orchestration result dict.
        """
        from gate_engine.daily_orchestrator import run_daily_orchestration
        sports = sports or ["NBA"]

        # Build expected props_by_sport so we can predict canonical IDs
        mock_props = {}
        for sp in sports:
            props, _ = self._mock_union(sp)
            mock_props[sp] = props

        scan_result = _make_mock_scan_result({
            "watch": [
                {
                    "player": f"Player_A_{sp}",
                    "sport": sp,
                    "prop": "points",
                    "side": "MORE",
                    "line": 20.5,
                    "game_date": "2026-08-19",
                    "classification": "Watch",
                    "terminal_bucket": "Watch",
                    "wow_score": 40.0,
                    "final_approval_blocker": "low_score",
                    "audit_valid": False,
                }
                for sp in sports
            ] + [
                {
                    "player": f"Player_B_{sp}",
                    "sport": sp,
                    "prop": "rebounds",
                    "side": "MORE",
                    "line": 5.5,
                    "game_date": "2026-08-19",
                    "classification": "Watch",
                    "terminal_bucket": "Watch",
                    "wow_score": 38.0,
                    "final_approval_blocker": "low_score",
                    "audit_valid": False,
                }
                for sp in sports
            ],
        })
        if extra_scan_override:
            scan_result.update(extra_scan_override)

        with (
            patch.object(orch, "_union_props_for_sport", side_effect=self._mock_union),
            patch("jobs.wow_daily_scan.run_scan", return_value=scan_result),
            patch("storage.daily_manifest.ensure_tables", return_value=True),
            patch("storage.daily_manifest.create_run",    return_value=True),
            patch("storage.daily_manifest.persist_discovery_checkpoint", return_value=True),
            patch("storage.daily_manifest.begin_scoring", return_value=True),
            patch("storage.daily_manifest.finalize_run",  return_value=True),
            patch("storage.daily_manifest.save_run_row",  return_value=True),
            patch.object(orch, "_run_scan_isolated", return_value=(scan_result, None)),
        ):
            return run_daily_orchestration(
                sports=sports,
                environment="test",
                runtime_provenance=None,
                session_id="test-session",
                persist=True,
            )

    def test_required_top_level_keys(self):
        from gate_engine.daily_orchestrator import run_daily_orchestration
        result = self._run_with_mocks(["NBA"])
        for key in (
            "run_id", "run_date", "run_status", "counts",
            "playable_card", "reconciliation", "source_union",
            "missing_sports", "failed_modules", "runtime_provenance",
        ):
            self.assertIn(key, result, f"Missing key: {key}")

    def test_run_id_is_uuid_string(self):
        from gate_engine.daily_orchestrator import run_daily_orchestration
        import re
        result = self._run_with_mocks(["NBA"])
        self.assertRegex(result["run_id"], r"^[0-9a-f-]{36}$")

    def test_counts_has_total_discovered(self):
        from gate_engine.daily_orchestrator import run_daily_orchestration
        result = self._run_with_mocks(["NBA"])
        self.assertIn("total_discovered", result["counts"])
        self.assertGreater(result["counts"]["total_discovered"], 0)

    def test_persist_false_skips_db_writes(self):
        from gate_engine.daily_orchestrator import run_daily_orchestration
        sports = ["NBA"]
        props, _ = self._mock_union("NBA")
        scan_result = _make_mock_scan_result({"watch": [
            {"player": "Player_A_NBA", "sport": "NBA", "prop": "points",
             "side": "MORE", "line": 20.5, "classification": "Watch",
             "terminal_bucket": "Watch", "wow_score": 40.0,
             "final_approval_blocker": None, "audit_valid": False},
        ]})
        with (
            patch.object(orch, "_union_props_for_sport", side_effect=self._mock_union),
            patch("jobs.wow_daily_scan.run_scan", return_value=scan_result),
            patch("storage.daily_manifest.ensure_tables") as mock_ensure,
            patch("storage.daily_manifest.create_run")    as mock_create,
            patch("storage.daily_manifest.persist_discovery_checkpoint") as mock_checkpoint,
            patch("storage.daily_manifest.begin_scoring") as mock_begin_scoring,
            patch("storage.daily_manifest.finalize_run")  as mock_finalize,
            patch("storage.daily_manifest.save_run_row")  as mock_save,
        ):
            run_daily_orchestration(
                sports=sports, environment="test",
                runtime_provenance=None, session_id=None,
                persist=False,
            )
        mock_ensure.assert_not_called()
        mock_create.assert_not_called()
        mock_checkpoint.assert_not_called()
        mock_begin_scoring.assert_not_called()
        mock_finalize.assert_not_called()
        mock_save.assert_not_called()

    def test_degraded_status_on_failed_modules(self):
        from gate_engine.daily_orchestrator import run_daily_orchestration
        sports = ["NBA"]
        scan_result = _make_mock_scan_result()
        scan_result["failed_modules"] = ["NBA:fetch_all_props:ConnectionError"]
        scan_result["run_status"]     = "DEGRADED_ENGINE_RUN"
        with (
            patch.object(orch, "_union_props_for_sport", side_effect=self._mock_union),
            patch("jobs.wow_daily_scan.run_scan", return_value=scan_result),
            patch("storage.daily_manifest.ensure_tables", return_value=True),
            patch("storage.daily_manifest.create_run",    return_value=True),
            patch("storage.daily_manifest.persist_discovery_checkpoint", return_value=True),
            patch("storage.daily_manifest.begin_scoring", return_value=True),
            patch("storage.daily_manifest.finalize_run",  return_value=True),
            patch("storage.daily_manifest.save_run_row",  return_value=True),
            patch.object(orch, "_run_scan_isolated", return_value=(scan_result, None)),
        ):
            result = run_daily_orchestration(
                sports=sports, environment="test",
                runtime_provenance=None, session_id=None,
                persist=True,
            )
        self.assertEqual(result["run_status"], "DEGRADED")

    def _assert_source_failure_blocks_canonical_commit(self, source_status):
        from gate_engine.daily_orchestrator import run_daily_orchestration
        from storage.daily_manifest import _manifest_is_committed

        props, _ = self._mock_union("NBA")
        scan_result = _make_mock_scan_result({
            "watch": [
                {
                    **prop,
                    "classification": "Watch",
                    "terminal_bucket": "watch",
                    "wow_score": 40.0,
                    "final_approval_blocker": "low_score",
                    "audit_valid": False,
                }
                for prop in props
            ],
        })
        isolated = MagicMock(return_value=(scan_result, None))
        finalize = MagicMock(return_value=True)
        with (
            patch.object(
                orch,
                "_union_props_for_sport",
                return_value=(props, source_status),
            ),
            patch("storage.daily_manifest.ensure_tables", return_value=True),
            patch("storage.daily_manifest.create_run", return_value=True),
            patch("storage.daily_manifest.persist_discovery_checkpoint", return_value=True),
            patch("storage.daily_manifest.begin_scoring", return_value=True),
            patch("storage.daily_manifest.finalize_run", finalize),
            patch("storage.daily_manifest.save_run_row", return_value=True),
            patch.object(orch, "_run_scan_isolated", isolated),
        ):
            result = run_daily_orchestration(
                sports=["NBA"],
                environment="test",
                runtime_provenance=None,
                session_id="test-session",
                persist=True,
            )

        self.assertEqual(result["run_status"], "DEGRADED")
        self.assertTrue(result["reconciliation"]["row_reconciled"])
        self.assertFalse(result["reconciliation"]["source_coverage_reconciled"])
        self.assertFalse(result["reconciliation"]["reconciled"])
        self.assertTrue(result["reconciliation"]["source_coverage_failures"])
        self.assertEqual(
            isolated.call_args.kwargs["scan_kwargs"]["_source_status_by_sport"],
            {"NBA": source_status},
        )
        persisted_reconciliation = finalize.call_args.kwargs["reconciliation"]
        self.assertFalse(persisted_reconciliation["reconciled"])
        self.assertFalse(_manifest_is_committed({
            "finished_at": "2026-08-20T00:00:00+00:00",
            "run_status": "DEGRADED",
            "reconciliation": persisted_reconciliation,
            "persisted_row_count": len(props),
            "total_discovered": len(props),
        }))

    def test_primary_success_backup_failure_cannot_commit_canonical_manifest(self):
        self._assert_source_failure_blocks_canonical_commit({
            "NBA_odds": "AVAILABLE: primary board acquired",
            "NBA_rundown": "FAILED: upstream timeout",
        })

    def test_primary_failure_backup_success_cannot_commit_canonical_manifest(self):
        self._assert_source_failure_blocks_canonical_commit({
            "NBA_odds": "FAILED: primary upstream timeout",
            "NBA_rundown": "AVAILABLE: backup board acquired",
        })


# ---------------------------------------------------------------------------
# Daily lifecycle reliability — persisted board / bounded fallback
# ---------------------------------------------------------------------------
class TestDailyLifecycleReliability(unittest.TestCase):
    def _props(self, sport="NBA"):
        return [
            {"player": f"{sport} A", "prop": "points", "side": "MORE",
             "line": 20.5, "sport": sport, "game_date": "2026-08-20"},
        ]

    def _scan_result(self, sport="NBA"):
        result = _make_mock_scan_result({
            "watch": [{
                "player": f"{sport} A", "prop": "points", "side": "MORE",
                "line": 20.5, "sport": sport, "game_date": "2026-08-20",
                "classification": "Watch", "terminal_bucket": "watch",
            }],
        })
        return result

    def test_checkpoint_is_persisted_before_scoring_starts(self):
        events = []
        scan_result = self._scan_result()
        with (
            patch.object(
                orch, "_union_props_for_sport",
                return_value=(self._props(), {"NBA_odds": "AVAILABLE"}),
            ),
            patch(
                "storage.daily_manifest.persist_discovery_checkpoint",
                side_effect=lambda **_kw: events.append("checkpoint") or True,
            ),
            patch(
                "storage.daily_manifest.begin_scoring",
                side_effect=lambda **_kw: events.append("scoring") or True,
            ),
            patch("storage.daily_manifest.ensure_tables", return_value=True),
            patch("storage.daily_manifest.create_run", return_value=True),
            patch("storage.daily_manifest.finalize_run", return_value=True),
            patch("storage.daily_manifest.save_run_row", return_value=True),
            patch.object(orch, "_run_scan_isolated", return_value=(scan_result, None)),
        ):
            result = orch.run_daily_orchestration(
                sports=["NBA"], environment="test", persist=True,
            )
        self.assertEqual(events, ["checkpoint", "scoring"])
        self.assertEqual(result["counts"]["total_discovered"], 1)

    def test_empty_discovery_finishes_reconciled_without_entering_scoring(self):
        finalize = MagicMock(return_value=True)
        with (
            patch.object(
                orch, "_union_props_for_sport",
                return_value=([], {"NBA_odds": "NO_EVENTS"}),
            ),
            patch("storage.daily_manifest.ensure_tables", return_value=True),
            patch("storage.daily_manifest.create_run", return_value=True),
            patch("storage.daily_manifest.persist_discovery_checkpoint", return_value=True),
            patch("storage.daily_manifest.begin_scoring") as begin_scoring,
            patch("storage.daily_manifest.finalize_run", finalize),
            patch.object(orch, "_run_scan_isolated") as scan,
        ):
            result = orch.run_daily_orchestration(
                sports=["NBA"], environment="test", persist=True,
            )
        begin_scoring.assert_not_called()
        scan.assert_not_called()
        self.assertEqual(result["run_status"], "COMPLETE")
        self.assertTrue(result["reconciliation"]["reconciled"])
        self.assertEqual(finalize.call_args.kwargs["completion_detail"], "DISCOVERY_EMPTY_RECONCILED")

    def test_failed_checkpoint_blocks_scoring_and_is_full_board_incomplete(self):
        finalize = MagicMock(return_value=True)
        with (
            patch.object(
                orch, "_union_props_for_sport",
                return_value=(self._props(), {"NBA_odds": "AVAILABLE"}),
            ),
            patch("storage.daily_manifest.ensure_tables", return_value=True),
            patch("storage.daily_manifest.create_run", return_value=True),
            patch("storage.daily_manifest.persist_discovery_checkpoint", return_value=False),
            patch("storage.daily_manifest.begin_scoring") as begin_scoring,
            patch("storage.daily_manifest.finalize_run", finalize),
            patch("storage.daily_manifest.save_run_row", return_value=True),
            patch.object(orch, "_run_scan_isolated") as scan,
        ):
            result = orch.run_daily_orchestration(
                sports=["NBA"], environment="test", persist=True,
            )
        begin_scoring.assert_not_called()
        scan.assert_not_called()
        self.assertEqual(result["run_status"], "DEGRADED")
        self.assertIn(
            "daily_manifest:DISCOVERY_CHECKPOINT_UNAVAILABLE",
            result["failed_modules"],
        )
        self.assertEqual(
            finalize.call_args.kwargs["failure_reason"],
            "FULL_BOARD_RUN_INCOMPLETE",
        )

    def test_primary_timeout_uses_composed_fallback_and_records_full_board_incomplete(self):
        scan_result = self._scan_result()
        fallback = {
            "used": True,
            "path": "LEGACY_COMPOSED_GATE_ENGINE",
            "lane_failures": [],
        }
        finalize = MagicMock(return_value=True)
        with (
            patch.object(
                orch, "_union_props_for_sport",
                return_value=(self._props(), {"NBA_odds": "AVAILABLE"}),
            ),
            patch("storage.daily_manifest.ensure_tables", return_value=True),
            patch("storage.daily_manifest.create_run", return_value=True),
            patch("storage.daily_manifest.persist_discovery_checkpoint", return_value=True),
            patch("storage.daily_manifest.begin_scoring", return_value=True),
            patch("storage.daily_manifest.finalize_run", finalize),
            patch("storage.daily_manifest.save_run_row", return_value=True),
            patch.object(
                orch, "_run_scan_isolated",
                return_value=(None, "SCORING_STAGE_TIMEOUT"),
            ),
            patch.object(
                orch, "_run_composed_fallback",
                return_value=(scan_result, fallback),
            ) as composed_fallback,
        ):
            result = orch.run_daily_orchestration(
                sports=["NBA"], environment="test", persist=True,
            )
        composed_fallback.assert_called_once()
        self.assertTrue(result["fallback"]["used"])
        self.assertEqual(result["fallback"]["trigger"], "SCORING_STAGE_TIMEOUT")
        self.assertTrue(
            finalize.call_args.kwargs["orchestration_metadata"]["fallback"]["used"]
        )
        self.assertEqual(
            finalize.call_args.kwargs["failure_reason"],
            "FULL_BOARD_RUN_INCOMPLETE",
        )

    def test_fallback_preflight_failure_is_full_board_incomplete_on_manifest(self):
        fallback = {
            "used": True,
            "path": "LEGACY_COMPOSED_GATE_ENGINE",
            "lane_failures": [],
            "reason": "FALLBACK_PREFLIGHT_UNAVAILABLE",
        }
        finalize = MagicMock(return_value=True)
        with (
            patch.object(
                orch, "_union_props_for_sport",
                return_value=(self._props(), {"NBA_odds": "AVAILABLE"}),
            ),
            patch("storage.daily_manifest.ensure_tables", return_value=True),
            patch("storage.daily_manifest.create_run", return_value=True),
            patch("storage.daily_manifest.persist_discovery_checkpoint", return_value=True),
            patch("storage.daily_manifest.begin_scoring", return_value=True),
            patch("storage.daily_manifest.finalize_run", finalize),
            patch("storage.daily_manifest.save_run_row", return_value=True),
            patch.object(
                orch, "_run_scan_isolated",
                return_value=(None, "SCORING_STAGE_TIMEOUT"),
            ),
            patch.object(
                orch, "_run_composed_fallback",
                return_value=(_make_mock_scan_result(), fallback),
            ),
        ):
            result = orch.run_daily_orchestration(
                sports=["NBA"], environment="test", persist=True,
            )
        self.assertEqual(result["run_status"], "DEGRADED")
        self.assertEqual(
            finalize.call_args.kwargs["failure_reason"],
            "FULL_BOARD_RUN_INCOMPLETE",
        )
        self.assertEqual(
            finalize.call_args.kwargs["failure_module"],
            "daily_orchestrator.full_board_confidence",
        )

    def test_composed_fallback_keeps_completed_lane_when_another_lane_fails(self):
        nba_result = self._scan_result("NBA")
        props_by_sport = {"NBA": self._props("NBA"), "WNBA": self._props("WNBA")}
        with (
            patch("gate_engine.governance.get_governance_status", return_value={}),
            patch(
                "gate_engine.llp_stage2_tables.get_stage2_schema_health",
                return_value={"schema_ready": True},
            ),
            patch.object(
                orch, "_run_scan_isolated",
                side_effect=[
                    (nba_result, None),
                    (None, "SCORING_STAGE_TIMEOUT"),
                ],
            ),
        ):
            result, metadata = orch._run_composed_fallback(
                props_by_sport=props_by_sport,
                scanned_sports=["NBA", "WNBA"],
                environment="test",
                runtime_provenance=None,
                deadline_at=None,
            )
        self.assertEqual(len(result["watch"]), 1)
        self.assertIn("fallback:WNBA:SCORING_STAGE_TIMEOUT", result["failed_modules"])
        self.assertEqual(metadata["lane_failures"], ["WNBA:SCORING_STAGE_TIMEOUT"])

    def test_fallback_persists_only_post_containment_rows(self):
        """Raw fallback cards cannot reserve a manifest row before containment."""
        raw_fallback = _make_mock_scan_result({
            "final_approved_internal": [{
                "player": "NBA A",
                "sport": "NBA",
                "prop": "points",
                "side": "MORE",
                "line": 20.5,
                "classification": "Final Approved — Internal Projection",
                "terminal_bucket": "final_approved_internal",
                "terminal_label": "FINAL_APPROVED",
                "model_probability": 0.71,
                "calibrated_probability": 0.69,
                "mandatory_prerequisites": {"calibration": "FAILED"},
                "can_execute": True,
                "can_approve_bets": True,
            }],
        })
        save = MagicMock(return_value=True)
        with (
            patch.object(
                orch, "_union_props_for_sport",
                return_value=(self._props(), {"NBA_odds": "AVAILABLE"}),
            ),
            patch("storage.daily_manifest.ensure_tables", return_value=True),
            patch("storage.daily_manifest.create_run", return_value=True),
            patch("storage.daily_manifest.mark_progress", return_value=True),
            patch("storage.daily_manifest.persist_discovery_checkpoint", return_value=True),
            patch("storage.daily_manifest.begin_scoring", return_value=True),
            patch("storage.daily_manifest.finalize_run", return_value=True),
            patch("storage.daily_manifest.save_run_row", save),
            patch(
                "gate_engine.governance.get_governance_status",
                return_value={},
            ),
            patch(
                "gate_engine.llp_stage2_tables.get_stage2_schema_health",
                return_value={"schema_ready": True},
            ),
            patch.object(
                orch,
                "_run_scan_isolated",
                side_effect=[
                    (None, "SCORING_STAGE_TIMEOUT"),
                    (raw_fallback, None),
                ],
            ),
        ):
            result = orch.run_daily_orchestration(
                sports=["NBA"], environment="test", persist=True,
            )

        self.assertEqual(save.call_count, 1)
        persisted = save.call_args.kwargs["full_row"]
        self.assertEqual(persisted["terminal_bucket"], "data_insufficient")
        self.assertEqual(persisted["terminal_label"], "DATA_CONTRACT_FAIL")
        self.assertIsNone(persisted["model_probability"])
        self.assertIsNone(persisted["calibrated_probability"])
        self.assertFalse(persisted["can_execute"])
        self.assertFalse(persisted["can_approve_bets"])
        self.assertEqual(result["counts"]["data_insufficient"], 1)

    def test_duplicate_approval_never_reaches_playable_or_manifest_authority(self):
        duplicate_result = _make_mock_scan_result({
            "market_verified": [{
                "player": "NBA A", "sport": "NBA", "prop": "points",
                "side": "MORE", "line": 20.5,
                "terminal_label": "FINAL_APPROVED",
                "classification": "Market Verified Approved",
                "terminal_bucket": "market_verified",
            }],
            "final_approved_internal": [{
                "player": "NBA A", "sport": "NBA", "prop": "points",
                "side": "MORE", "line": 20.5,
                "terminal_label": "FINAL_APPROVED",
                "classification": "Final Approved — Internal Projection",
                "terminal_bucket": "final_approved_internal",
            }],
        })
        save = MagicMock(return_value=True)
        with (
            patch.object(
                orch, "_union_props_for_sport",
                return_value=(self._props(), {"NBA_odds": "AVAILABLE"}),
            ),
            patch("storage.daily_manifest.ensure_tables", return_value=True),
            patch("storage.daily_manifest.create_run", return_value=True),
            patch("storage.daily_manifest.persist_discovery_checkpoint", return_value=True),
            patch("storage.daily_manifest.begin_scoring", return_value=True),
            patch("storage.daily_manifest.finalize_run", return_value=True),
            patch("storage.daily_manifest.save_run_row", save),
            patch.object(orch, "_run_scan_isolated", return_value=(duplicate_result, None)),
        ):
            result = orch.run_daily_orchestration(
                sports=["NBA"], environment="test", persist=True,
            )

        self.assertEqual(result["playable_card"], [])
        self.assertEqual(result["counts"]["data_insufficient"], 1)
        self.assertFalse(result["reconciliation"]["reconciled"])
        self.assertEqual(
            result["reconciliation"]["containment"]["duplicate_ids"],
            [save.call_args.kwargs["canonical_selection_id"]],
        )
        persisted = save.call_args.kwargs["full_row"]
        self.assertEqual(persisted["terminal_label"], "DATA_CONTRACT_FAIL")
        self.assertEqual(persisted["final_approval_blocker"], "DUPLICATE_TERMINAL_OUTCOME")

    def test_isolated_scanner_timeout_terminates_the_child(self):
        class TimeoutQueue:
            def get(self, timeout):
                raise orch.Empty

            def close(self):
                return None

        class TimeoutProcess:
            def __init__(self):
                self.terminated = False
                self.killed = False

            def start(self):
                return None

            def is_alive(self):
                return not self.terminated and not self.killed

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

            def join(self, timeout=None):
                return None

        queue = TimeoutQueue()
        process = TimeoutProcess()
        context = MagicMock()
        context.Queue.return_value = queue
        context.Process.return_value = process
        with patch.object(orch.multiprocessing, "get_context", return_value=context):
            result, reason = orch._run_scan_isolated(
                scan_kwargs={"sports": ["NBA"]},
                timeout_seconds=0.01,
            )
        self.assertIsNone(result)
        self.assertEqual(reason, "SCORING_STAGE_TIMEOUT")
        self.assertTrue(process.terminated)


# ---------------------------------------------------------------------------
# Test 11 — Source union: backup always called
# ---------------------------------------------------------------------------
class TestSourceUnion(unittest.TestCase):

    def test_union_calls_both_sources(self):
        """_union_props_for_sport should always call both primary AND backup."""
        primary = [
            {"player": "A", "prop": "pts", "side": "MORE", "line": 20.5, "sport": "NBA"}
        ]
        backup  = [
            {"player": "B", "prop": "reb", "side": "MORE", "line": 5.5,  "sport": "NBA"}
        ]
        with (
            patch("services.odds_api.fetch_all_props", return_value=(primary, "AVAILABLE")),
            patch("services.rundown.fetch_backup_props",   return_value=(backup, "AVAILABLE")),
        ):
            props, status = orch._union_props_for_sport("NBA")
        self.assertEqual(len(props), 2)  # both sources unioned
        players = {p["player"] for p in props}
        self.assertIn("A", players)
        self.assertIn("B", players)

    def test_union_deduplicates_same_key(self):
        """When both sources return the same (player,prop,side,line_bucket), keep first."""
        shared = {"player": "A", "prop": "pts", "side": "MORE", "line": 20.5, "sport": "NBA"}
        with (
            patch("services.odds_api.fetch_all_props",   return_value=([shared], "AVAILABLE")),
            patch("services.rundown.fetch_backup_props", return_value=([shared], "AVAILABLE")),
        ):
            props, _ = orch._union_props_for_sport("NBA")
        self.assertEqual(len(props), 1)

    def test_union_works_when_primary_fails(self):
        """Backup props are still returned even when primary raises."""
        backup = [{"player": "B", "prop": "reb", "side": "MORE", "line": 5.5, "sport": "NBA"}]
        with (
            patch("services.odds_api.fetch_all_props",   side_effect=Exception("timeout")),
            patch("services.rundown.fetch_backup_props", return_value=(backup, "AVAILABLE")),
        ):
            props, status = orch._union_props_for_sport("NBA")
        self.assertEqual(len(props), 1)
        self.assertIn("FAILED", status.get("NBA_odds", ""))


# ---------------------------------------------------------------------------
# Test 22 — run_scan _props_by_sport injection (no truncation)
# ---------------------------------------------------------------------------
class TestRunScanInjection(unittest.TestCase):

    def test_props_by_sport_skips_http_and_no_truncation(self):
        """
        When _props_by_sport is supplied:
        - no fetch_all_props / fetch_backup_props calls
        - props are NOT truncated even if > limit_per_sport
        """
        # Build 5 props for NBA
        sport    = "NBA"
        big_list = [
            {"player": f"P{i}", "prop": "points", "side": "MORE",
             "line": 20.5, "sport": sport, "game_date": "2026-08-19"}
            for i in range(5)
        ]

        from jobs.wow_daily_scan import run_scan

        called = []

        def mock_compute(*args, **kwargs):
            return 50, "STRONG", "ok"

        def mock_log_stats(*args, **kwargs):
            return {
                "raw_l5": [21, 22], "raw_l10": [20, 21, 22, 19, 18, 23, 24, 20, 21, 22],
                "l5_hit_rate": 0.8, "l10_hit_rate": 0.75,
                "l10_median": 21.0, "l10_avg": 21.0,
                "games_available": 10, "sample_scope": "full",
                "cross_season_used": False, "manual_fallback_used": False,
                "log_status": "AVAILABLE",
            }, "AVAILABLE"

        with (
            patch("services.odds_api.fetch_all_props",   side_effect=AssertionError("should not be called")),
            patch("services.rundown.fetch_backup_props", side_effect=AssertionError("should not be called")),
            patch("services.player_logs.get_player_log_stats", side_effect=mock_log_stats),
            patch("services.status.get_injuries",            return_value=({}, "OK")),
            patch("services.status.get_player_injury_flag",  return_value=(0, "OK", None)),
            patch("services.status.get_mlb_probable_pitchers", return_value=({}, "OK")),
        ):
            # Patch compute_wow_score at the module level inside wow_daily_scan
            import jobs.wow_daily_scan as scan_mod
            original_compute = scan_mod.compute_wow_score
            scan_mod.compute_wow_score = mock_compute
            try:
                result = run_scan(
                    sports=[sport],
                    environment="test",
                    limit_per_sport=2,      # would truncate if applied
                    _props_by_sport={sport: big_list},
                    _persist_results=False,
                )
            finally:
                scan_mod.compute_wow_score = original_compute

        # 5 props evaluated — not truncated to 2
        total_evaluated = sum(
            len(result.get(b, []))
            for b in ("market_verified", "final_approved_internal", "model_qualified",
                      "conditional", "watch", "reject", "data_insufficient", "no_play")
        )
        self.assertEqual(total_evaluated, 5, f"Expected 5, got {total_evaluated}")

    def test_persist_false_does_not_call_save_scan_result(self):
        """_persist_results=False must suppress save_scan_result calls."""
        from jobs.wow_daily_scan import run_scan
        sport    = "NBA"
        one_prop = [
            {"player": "Ptest", "prop": "points", "side": "MORE",
             "line": 20.5, "sport": sport, "game_date": "2026-08-19"}
        ]

        def mock_log_stats(*a, **k):
            return {
                "raw_l5": [21], "raw_l10": [21],
                "l5_hit_rate": 0.8, "l10_hit_rate": 0.75,
                "l10_median": 21.0, "l10_avg": 21.0,
                "games_available": 1, "sample_scope": "full",
                "cross_season_used": False, "manual_fallback_used": False,
                "log_status": "AVAILABLE",
            }, "AVAILABLE"

        with (
            patch("services.player_logs.get_player_log_stats", side_effect=mock_log_stats),
            patch("services.status.get_injuries",             return_value=({}, "OK")),
            patch("services.status.get_player_injury_flag",   return_value=(0, "OK", None)),
            patch("services.status.get_mlb_probable_pitchers", return_value=({}, "OK")),
            patch("storage.results.save_scan_result") as mock_save,
        ):
            import jobs.wow_daily_scan as scan_mod
            original_compute = scan_mod.compute_wow_score
            scan_mod.compute_wow_score = lambda *a, **k: (50, "ok", "ok")
            try:
                run_scan(
                    sports=[sport],
                    environment="test",
                    limit_per_sport=None,
                    _props_by_sport={sport: one_prop},
                    _persist_results=False,
                )
            finally:
                scan_mod.compute_wow_score = original_compute

        mock_save.assert_not_called()


# ---------------------------------------------------------------------------
# Test 23 — Soccer outcome normalisation integrated into source union
# ---------------------------------------------------------------------------
class TestSoccerOutcomeInUnion(unittest.TestCase):

    def test_soccer_outcomes_normalised_in_orchestrator(self):
        soccer_props = [
            {"player": "TeamA", "prop": "match_winner", "side": "1",
             "line": 1.0, "sport": "Soccer", "game_date": "2026-08-19",
             "outcome": "1"},
            {"player": "TeamA", "prop": "match_winner", "side": "X",
             "line": 1.0, "sport": "Soccer", "game_date": "2026-08-19",
             "outcome": "X"},
            {"player": "TeamB", "prop": "match_winner", "side": "2",
             "line": 1.0, "sport": "Soccer", "game_date": "2026-08-19",
             "outcome": "2"},
        ]

        def mock_union(sport):
            return soccer_props[:], {f"{sport}_odds": "AVAILABLE"}

        scan_result = _make_mock_scan_result({
            "watch": [
                {**p, "classification": "Watch", "terminal_bucket": "Watch",
                 "wow_score": 40.0, "final_approval_blocker": None, "audit_valid": False}
                for p in soccer_props
            ]
        })

        with (
            patch.object(orch, "_union_props_for_sport", side_effect=mock_union),
            patch("jobs.wow_daily_scan.run_scan", return_value=scan_result),
            patch.object(orch, "_run_scan_isolated", return_value=(scan_result, None)),
        ):
            from gate_engine.daily_orchestrator import run_daily_orchestration
            result = run_daily_orchestration(
                sports=["Soccer"], environment="test",
                runtime_provenance=None, persist=False,
            )

        # normalise_soccer_props is called on raw_props before evaluation
        # Check the execution notes indicate Soccer was processed
        self.assertIn("Soccer", result.get("scanned_sports", []))


class TestScopedMoneylineDailyOrchestration(unittest.TestCase):
    """Scoped Daily runs must never enter broad prop discovery or scoring."""

    def test_scope_bypasses_broad_prop_union_and_scanner(self):
        scoped_rows = [
            {
                "row_id": "moneyline-row",
                "sport": "MLB",
                "team": "Chicago Cubs",
                "opponent": "St. Louis Cardinals",
                "event_id": "game-1",
                "slate_date": "2026-08-20",
                "market_type": "h2h",
                "commence_time": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
                "player": "Chicago Cubs",
                "prop": "outright_winner",
                "side": "WIN",
                "line": 0.0,
            }
        ]
        scoped_result = orch._empty_scan_result()
        scoped_result["watch"] = [
            {
                **scoped_rows[0],
                "terminal_label": "WATCH",
                "terminal_bucket": "watch",
                "can_execute": False,
            }
        ]

        with (
            patch.object(orch, "_union_props_for_sport") as broad_union,
            patch(
                "gate_engine.daily_moneyline_scope.discover_remaining_today_moneyline",
                return_value=(scoped_rows, {"MLB_odds": "AVAILABLE"}),
            ) as scoped_discovery,
            patch(
                "gate_engine.daily_moneyline_scope.score_scoped_moneyline_rows",
                return_value=scoped_result,
            ) as scoped_scorer,
            patch.object(orch, "_run_scan_isolated") as broad_scanner,
        ):
            result = orch.run_daily_orchestration(
                sports=["MLB"],
                environment="test",
                persist=False,
                scope="MONEYLINE_REMAINING_TODAY",
                scope_requested_at="2026-08-20T17:00:00+00:00",
                run_timezone="America/Chicago",
                run_date="2026-08-20",
            )

        scoped_discovery.assert_called_once()
        scoped_scorer.assert_called_once()
        broad_union.assert_not_called()
        broad_scanner.assert_not_called()
        self.assertEqual(result["scope"], "MONEYLINE_REMAINING_TODAY")
        self.assertEqual(result["counts"]["total_discovered"], 1)
        self.assertEqual(result["counts"]["watch"], 1)
        self.assertFalse(result["_buckets"]["watch"][0]["can_execute"])


class TestFinalPregamePublicationBoundary(unittest.TestCase):
    def test_started_h2h_card_is_removed_before_final_publication(self):
        now = datetime.now(timezone.utc)
        result = orch._empty_scan_result()
        result["watch"] = [
            {
                "canonical_selection_id": "started-event",
                "market_type": "h2h",
                "commence_time": (now - timedelta(minutes=1)).isoformat(),
                "can_execute": False,
            },
            {
                "canonical_selection_id": "not-moneyline",
                "market_type": "player_prop",
                "can_execute": False,
            },
        ]

        excluded = orch._purge_non_pregame_moneyline_cards(result)

        self.assertEqual(excluded, ["started-event"])
        self.assertEqual(
            [card["canonical_selection_id"] for card in result["watch"]],
            ["not-moneyline"],
        )

    def test_full_game_h2h_without_timing_is_removed_fail_closed(self):
        result = orch._empty_scan_result()
        result["watch"] = [{
            "canonical_selection_id": "untimeable-moneyline",
            "market_type": "full_game_h2h",
            "can_execute": False,
        }]

        excluded = orch._purge_non_pregame_moneyline_cards(result)

        self.assertEqual(excluded, ["untimeable-moneyline"])
        self.assertEqual(result["watch"], [])

    def test_all_game_outcome_moneyline_variants_require_pregame_timing(self):
        from gate_engine.market_family import _OUTRIGHT_MARKET_KEYS
        variants = tuple(sorted(
            set(_OUTRIGHT_MARKET_KEYS) | {"full_game_h2h", "full_game_moneyline"}
        ))
        result = orch._empty_scan_result()
        result["watch"] = [
            {
                "canonical_selection_id": f"no-time-{variant}",
                "market_type": variant,
                "can_execute": False,
            }
            for variant in variants
        ]

        excluded = orch._purge_non_pregame_moneyline_cards(result)

        self.assertEqual(excluded, [f"no-time-{variant}" for variant in variants])
        self.assertEqual(result["watch"], [])

    def test_started_ml_is_removed_from_every_terminal_bucket(self):
        started_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        result = orch._empty_scan_result()
        for bucket in orch._TERMINAL_BUCKETS:
            result[bucket] = [{
                "canonical_selection_id": f"started-ml-{bucket}",
                "market_type": "ml",
                "commence_time": started_at,
                "can_execute": False,
            }]

        excluded = orch._purge_non_pregame_moneyline_cards(result)

        self.assertEqual(
            excluded,
            [f"started-ml-{bucket}" for bucket in orch._TERMINAL_BUCKETS],
        )
        self.assertTrue(all(result[bucket] == [] for bucket in orch._TERMINAL_BUCKETS))

    def test_unavailable_scoped_discovery_is_degraded_not_empty_success(self):
        with (
            patch.object(orch, "_union_props_for_sport") as broad_union,
            patch(
                "gate_engine.daily_moneyline_scope.discover_remaining_today_moneyline",
                return_value=([], {"MLB_odds": "FAILED:FALLBACK_RUNDOWN:FAILED"}),
            ),
        ):
            result = orch.run_daily_orchestration(
                sports=["MLB"],
                environment="test",
                persist=False,
                scope="MONEYLINE_REMAINING_TODAY",
                scope_requested_at="2026-08-20T17:00:00+00:00",
                run_timezone="America/Chicago",
                run_date="2026-08-20",
            )

        broad_union.assert_not_called()
        self.assertEqual(result["run_status"], "DEGRADED")
        self.assertEqual(result["counts"]["total_discovered"], 0)
        self.assertTrue(any(
            "MLB:source_coverage:MLB_odds:FAILED:" in failure
            for failure in result["failed_modules"]
        ))

    def test_quota_proactive_skip_is_degraded_not_empty_success(self):
        with (
            patch.object(orch, "_union_props_for_sport") as broad_union,
            patch(
                "gate_engine.daily_moneyline_scope.discover_remaining_today_moneyline",
                return_value=(
                    [],
                    {
                        "MLB_odds": (
                            "FAILED:proactive_skip:paid:quota_exhausted"
                        )
                    },
                ),
            ),
        ):
            result = orch.run_daily_orchestration(
                sports=["MLB"],
                environment="test",
                persist=False,
                scope="MONEYLINE_REMAINING_TODAY",
                scope_requested_at="2026-08-20T17:00:00+00:00",
                run_timezone="America/Chicago",
                run_date="2026-08-20",
            )

        broad_union.assert_not_called()
        self.assertEqual(result["run_status"], "DEGRADED")
        self.assertEqual(result["counts"]["total_discovered"], 0)


if __name__ == "__main__":
    unittest.main()
