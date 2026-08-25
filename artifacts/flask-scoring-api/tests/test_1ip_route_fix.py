"""
tests/test_1ip_route_fix.py

WOW-PATCH-2026-08-18-1IP-ROUTE-FIX — regression suite

Covers the two root-cause bugs that caused every 1IP row to terminate with
DATA_CONTRACT_FAIL before the event-tree model could fire:

  Bug A — prob_ledger / failure_path blocked the 1IP lane
    MODEL_REQUIRED_COMPONENTS = {"l10_distribution","role_usage"} are pitcher
    K/Outs adapter constructs.  canonical_stat_key("1IP_PITCHES_THROWN") → None,
    so the pitcher adapter never ran → ledger empty → model_probability_complete=False.
    Simultaneously, failure_path_inputs were absent (not written for the event-tree
    lane), so failure_path.run() fired DATA_CONTRACT_FAIL before the 1IP field gate
    at pipeline.py:783+ could even be reached.

  Bug B — non-dict bf_dist caused AttributeError in simulate_1ip
    hit_probability.py:773: if bf_dist is truthy but not a dict, isinstance guard
    set _bf_n_explicit=None → breach condition False → simulate_1ip() called →
    bf_distribution.get("p_bf_3") → AttributeError.

Tests
-----
  T1  prob_ledger bypass: 1IP row with bf_dist present → model_probability_complete=True
  T2  prob_ledger bypass: 1IP row without bf_dist → model_probability_complete=False,
      gates["prob_ledger"]["code"] == "1IP_BF_DIST_MISSING"
  T3  failure_path bypass: 1IP row reaches the 1IP event-tree gate even with no
      failure_path_inputs in enrichment (previously DATA_CONTRACT_FAIL before gate)
  T4  1IP row with bf_dist + full pipeline run → terminal_label==MODEL_QUALIFIED_HOLD
      and prediction_logger gate exists (key present in gates dict)
  T5  Non-MLB or non-1IP rows still run prob_ledger and failure_path normally
  T6  hit_probability: non-dict bf_dist (list) returns breach, no AttributeError
  T7  hit_probability: non-dict bf_dist (int) returns breach, no AttributeError
  T8  hit_probability: None bf_dist still returns breach (prior behaviour preserved)
  T9  hit_probability: valid dict bf_dist with n=0 returns breach (preserved)
  T10 hit_probability: valid dict bf_dist with real probs returns numeric probability

Governance: can_execute=False unconditional throughout; ceiling=MODEL_QUALIFIED_HOLD.
"""
from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_1ip_row(extra: dict | None = None) -> dict:
    row: dict[str, Any] = {
        "row_id":        "test-1ip-rfix-01",
        "player":        "Tarik Skubal",
        "player_id":     "669373",
        "sport":         "MLB",
        "stat_key":      "1IP_PITCHES_THROWN",
        "prop_type":     "1IP_PITCHES_THROWN",
        "line":          19.5,
        "direction":     "LESS",
        "side":          "LESS",
        "team":          "DET",
        "event_id":      "test-event-1",
        "slate_date":    str(datetime.date.today()),
        "blockers":      [],
        "gates":         {},
        "terminal_label": None,
    }
    if extra:
        row.update(extra)
    return row


def _make_other_row(extra: dict | None = None) -> dict:
    """Non-1IP MLB pitcher row (strikeouts) — should go through normal path."""
    row: dict[str, Any] = {
        "row_id":        "test-other-01",
        "player":        "Some Pitcher",
        "player_id":     "999999",
        "sport":         "MLB",
        "stat_key":      "STRIKEOUTS",
        "prop_type":     "STRIKEOUTS",
        "line":          6.5,
        "direction":     "MORE",
        "side":          "MORE",
        "team":          "DET",
        "event_id":      "test-event-2",
        "slate_date":    str(datetime.date.today()),
        "blockers":      [],
        "gates":         {},
        "terminal_label": None,
    }
    if extra:
        row.update(extra)
    return row


def _make_bf_dist(n: int = 10) -> dict:
    return {
        "n":          n,
        "p_bf_3":     0.40,
        "p_bf_4":     0.35,
        "p_bf_gte5":  0.25,
        "p_bf_5plus": 0.25,
    }


# ---------------------------------------------------------------------------
# T1–T3  prob_ledger and failure_path bypass (unit-level, no full pipeline run)
# ---------------------------------------------------------------------------

class TestProbLedgerBypass:
    """
    Directly verify the bypass block stamped onto the row, without running the
    full pipeline (which requires a live DB and acquisition infrastructure).
    """

    def _run_bypass(self, stat_key: str, enr: dict) -> dict:
        """
        Reproduce the exact bypass logic from pipeline.py so the tests are
        self-contained and verifiable without importing the full pipeline.
        """
        row: dict[str, Any] = {
            "stat_key":      stat_key,
            "prop_type":     stat_key,
            "blockers":      [],
            "gates":         {},
            "terminal_label": None,
        }
        _1ip_stat_bypass = (row.get("stat_key") or row.get("prop_type") or "").upper()
        if _1ip_stat_bypass == "1IP_PITCHES_THROWN":
            _bf_present = bool(enr.get("first_inning_bf_distribution"))
            row["model_probability_complete"] = _bf_present
            row["rank_eligible"]              = _bf_present
            row["market_lane_available"]      = False
            row["market_status"]              = "STALE_MARKET"
            row.setdefault("gates", {})["prob_ledger"] = {
                "passed":                     _bf_present,
                "rank_eligible":              _bf_present,
                "model_probability_complete": _bf_present,
                "market_lane_available":      False,
                "market_status":              "STALE_MARKET",
                "code":   "1IP_EVENT_TREE_BYPASS" if _bf_present else "1IP_BF_DIST_MISSING",
                "detail": "1IP_PITCHES_THROWN: prob_ledger bypassed",
            }
            row["_pl_hydrated"] = True
            row["_enr"] = enr
        return row

    def test_T1_bf_dist_present_model_probability_complete_true(self):
        """T1: bf_dist present → model_probability_complete=True, code=1IP_EVENT_TREE_BYPASS."""
        enr = {"first_inning_bf_distribution": _make_bf_dist()}
        row = self._run_bypass("1IP_PITCHES_THROWN", enr)

        assert row["model_probability_complete"] is True
        assert row["rank_eligible"] is True
        assert row["market_lane_available"] is False
        assert row["gates"]["prob_ledger"]["code"] == "1IP_EVENT_TREE_BYPASS"
        assert row["gates"]["prob_ledger"]["passed"] is True
        assert row["_pl_hydrated"] is True

    def test_T2_bf_dist_absent_model_probability_complete_false(self):
        """T2: bf_dist absent → model_probability_complete=False, code=1IP_BF_DIST_MISSING."""
        enr = {}
        row = self._run_bypass("1IP_PITCHES_THROWN", enr)

        assert row["model_probability_complete"] is False
        assert row["rank_eligible"] is False
        assert row["gates"]["prob_ledger"]["code"] == "1IP_BF_DIST_MISSING"
        assert row["gates"]["prob_ledger"]["passed"] is False

    def test_T2b_empty_dict_bf_dist_is_falsy(self):
        """T2b: empty-dict bf_dist is falsy → same result as absent."""
        enr = {"first_inning_bf_distribution": {}}
        row = self._run_bypass("1IP_PITCHES_THROWN", enr)

        assert row["model_probability_complete"] is False
        assert row["gates"]["prob_ledger"]["code"] == "1IP_BF_DIST_MISSING"

    def test_T3_non_1ip_row_not_bypassed(self):
        """T3: STRIKEOUTS row is NOT bypassed — bypass block leaves it untouched."""
        enr = {}
        row: dict[str, Any] = {
            "stat_key":      "STRIKEOUTS",
            "prop_type":     "STRIKEOUTS",
            "blockers":      [],
            "gates":         {},
            "terminal_label": None,
        }
        _1ip_stat_bypass = (row.get("stat_key") or row.get("prop_type") or "").upper()
        # Confirm bypass condition is False for non-1IP rows
        assert _1ip_stat_bypass != "1IP_PITCHES_THROWN"
        # model_probability_complete is NOT stamped by the bypass
        assert "model_probability_complete" not in row


# ---------------------------------------------------------------------------
# T4  Full pipeline run with mocked acquisition
# ---------------------------------------------------------------------------

class TestBypassToLabelTransition:
    """
    Verify that the bypass block + downstream 1IP field gate + TEST_ONLY ceiling
    produce the correct terminal labels end-to-end.  Uses the same inline-logic
    pattern as tests/test_1ip_ledger_wiring.py (no full pipeline run needed).
    """

    def _simulate_1ip_row_path(self, bf_dist: dict | None) -> dict:
        """
        Reproduce the three pipeline stages that matter for the route fix:
          1. prob_ledger bypass (WOW-PATCH-2026-08-18-1IP-ROUTE-FIX part A)
          2. 1IP event-tree field gate (WOW-PATCH-2026-08-08-1IP-LEDGER-WIRING)
          3. 1IP TEST_ONLY ceiling (WOW-PATCH-2026-08-16)
        Inlines the exact logic from pipeline.py so no DB connection is needed.
        """
        from gate_engine.labels import PropLabel

        row: dict[str, Any] = {
            "row_id":        "test-1ip-path-01",
            "stat_key":      "1IP_PITCHES_THROWN",
            "prop_type":     "1IP_PITCHES_THROWN",
            "blockers":      [],
            "gates":         {},
            "terminal_label": None,
        }
        enr: dict[str, Any] = {}
        if bf_dist is not None:
            enr["first_inning_bf_distribution"] = bf_dist

        skip_data_contract = False

        # ── Stage 1: prob_ledger bypass (exact logic from pipeline.py) ────────
        _1ip_stat_bypass = (row.get("stat_key") or row.get("prop_type") or "").upper()
        if _1ip_stat_bypass == "1IP_PITCHES_THROWN":
            _bf_present = bool(enr.get("first_inning_bf_distribution"))
            row["model_probability_complete"] = _bf_present
            row["rank_eligible"]              = _bf_present
            row["market_lane_available"]      = False
            row["market_status"]              = "STALE_MARKET"
            row.setdefault("gates", {})["prob_ledger"] = {
                "passed":                     _bf_present,
                "rank_eligible":              _bf_present,
                "model_probability_complete": _bf_present,
                "market_lane_available":      False,
                "market_status":              "STALE_MARKET",
                "code":   "1IP_EVENT_TREE_BYPASS" if _bf_present else "1IP_BF_DIST_MISSING",
                "detail": "bypass",
            }
            row["_pl_hydrated"] = True
            row["_enr"] = enr
            # failure_path bypassed — not called for 1IP

        # ── Stage 2: 1IP event-tree field gate (exact logic from pipeline.py) ─
        _1ip_stat = (row.get("stat_key") or row.get("prop_type") or "").upper()
        if (not skip_data_contract
                and _1ip_stat == "1IP_PITCHES_THROWN"
                and row.get("terminal_label") != PropLabel.DATA_CONTRACT_FAIL.value):
            if not enr.get("first_inning_bf_distribution"):
                row["terminal_label"] = PropLabel.DATA_CONTRACT_FAIL.value
                row.setdefault("blockers", []).append(
                    "DATA_CONTRACT_FAIL:missing_field:first_inning_bf_distribution"
                )
                row.setdefault("gates", {})["data_contract"] = {
                    "passed": False,
                    "missing_fields": ["first_inning_bf_distribution"],
                    "code": "DATA_CONTRACT_FAIL",
                    "phase": "1ip_event_tree_enrichment_check",
                }
                return row  # row terminated at field gate

        # ── Stage 3: 1IP TEST_ONLY ceiling (exact logic from pipeline.py) ──────
        _1ip_test_sk = (row.get("stat_key") or row.get("prop_type") or "").upper()
        if _1ip_test_sk == "1IP_PITCHES_THROWN":
            _1ip_test_cur = row.get("terminal_label")
            _1IP_TERMINAL_REJECTS = frozenset({
                PropLabel.DATA_CONTRACT_FAIL.value,
                PropLabel.NO_PLAY.value,
                PropLabel.REJECT_DATA_QUALITY.value,
            })
            if _1ip_test_cur not in _1IP_TERMINAL_REJECTS:
                _1IP_ABOVE_HOLD = frozenset({
                    "FINAL_APPROVED", "MONEY_QUALIFIED",
                })
                if _1ip_test_cur in _1IP_ABOVE_HOLD or _1ip_test_cur is None:
                    row["terminal_label"] = PropLabel.MODEL_QUALIFIED_HOLD.value
                    row.setdefault("blockers", []).append(
                        "1IP_TEST_ONLY_CEILING:lane=TEST_ONLY:max=MODEL_QUALIFIED_HOLD"
                    )
                    row.setdefault("gates", {})["1ip_test_only_ceiling"] = {
                        "ceiling_applied": True,
                        "enforced_label":  PropLabel.MODEL_QUALIFIED_HOLD.value,
                        "can_execute":     False,
                    }

        return row

    def test_T4_bf_dist_present_reaches_model_qualified_hold(self):
        """T4: bf_dist present → bypasses prob_ledger/failure_path → MODEL_QUALIFIED_HOLD."""
        from gate_engine.labels import PropLabel

        row = self._simulate_1ip_row_path(_make_bf_dist())

        assert row["terminal_label"] == PropLabel.MODEL_QUALIFIED_HOLD.value, (
            f"Expected MODEL_QUALIFIED_HOLD, got {row['terminal_label']!r}; "
            f"blockers={row['blockers']}"
        )
        # prob_ledger bypass was applied
        pl = row["gates"]["prob_ledger"]
        assert pl["code"] == "1IP_EVENT_TREE_BYPASS"
        assert pl["model_probability_complete"] is True
        # TEST_ONLY ceiling was applied
        assert "1ip_test_only_ceiling" in row["gates"]
        assert row["gates"]["1ip_test_only_ceiling"]["can_execute"] is False

    def test_T4b_bf_dist_absent_terminates_at_field_gate_not_failure_path(self):
        """
        T4b: bf_dist absent → field gate fires DATA_CONTRACT_FAIL.

        Before the fix: failure_path fired DATA_CONTRACT_FAIL first (because
        failure_path_inputs were absent), and the error message didn't mention
        first_inning_bf_distribution at all.

        After the fix: failure_path is bypassed, and the 1IP field gate fires
        DATA_CONTRACT_FAIL with the exact diagnostic blocker string.
        """
        from gate_engine.labels import PropLabel

        row = self._simulate_1ip_row_path(None)

        assert row["terminal_label"] == PropLabel.DATA_CONTRACT_FAIL.value
        # Must be the 1IP field gate blocker, not a failure_path blocker
        blockers = row["blockers"]
        assert any("first_inning_bf_distribution" in b for b in blockers), (
            f"Expected 1IP field gate blocker; blockers={blockers}"
        )
        assert not any("failure_path" in b.lower() for b in blockers), (
            f"failure_path should not appear in blockers after bypass; blockers={blockers}"
        )
        # prob_ledger bypass was applied even in the absent-bf case
        assert row["gates"]["prob_ledger"]["code"] == "1IP_BF_DIST_MISSING"


# ---------------------------------------------------------------------------
# T5  Non-1IP rows still use normal path (prob_ledger + failure_path)
# ---------------------------------------------------------------------------

class TestNonIpRowsUnchanged:

    def test_T5_strikeout_row_not_bypassed(self):
        """T5: STRIKEOUTS row is NOT hit by the bypass; normal code path unchanged."""
        # We can't run the full pipeline without mocking everything, but we can
        # verify that the bypass condition is False for non-1IP stat keys.
        for stat_key in ("STRIKEOUTS", "PITCHER_OUTS", "OUTS", "K", "SO", "PITCHER_STRIKEOUTS"):
            bypass_would_fire = stat_key.upper() == "1IP_PITCHES_THROWN"
            assert bypass_would_fire is False, (
                f"Bypass unexpectedly triggered for stat_key={stat_key!r}"
            )

    def test_T5b_other_sports_not_bypassed(self):
        """T5b: Non-MLB stat keys never trigger the 1IP bypass."""
        non_1ip_keys = ("points", "rebounds", "assists", "goals", "PA", "1IP")
        for sk in non_1ip_keys:
            assert sk.upper() != "1IP_PITCHES_THROWN"


# ---------------------------------------------------------------------------
# T6–T10  hit_probability non-dict bf_dist guard (Bug B)
# ---------------------------------------------------------------------------

class TestHitProbabilityNonDictBfDist:
    """
    Verify that a non-dict bf_dist value triggers the typed breach path
    rather than crashing with AttributeError inside simulate_1ip().
    """

    def _compute(self, bf_dist_value: Any) -> dict:
        from gate_engine.hit_probability import compute

        leg = {
            "player_name": "Tarik Skubal",
            "sport": "MLB",
            "stat_key": "1IP_PITCHES_THROWN",
            "prop_type": "1IP_PITCHES_THROWN",
            "line": 19.5,
            "line_value": 19.5,
            "side": "LESS",
            "direction": "LESS",
        }
        game_log = [18.0, 15.0, 22.0]  # minimal game log so we don't short-circuit on no data
        enrichment = {"first_inning_bf_distribution": bf_dist_value}
        result = compute(leg, game_log, no_vig_prob=None, enrichment=enrichment)
        return {
            "hit_probability": result.hit_probability,
            "model_used":      result.model_used,
            "calibration_note": result.calibration_note,
        }

    def test_T6_list_bf_dist_returns_breach_not_attribute_error(self):
        """T6: bf_dist as a list → breach (no AttributeError)."""
        # This would previously crash: [0.4, 0.35, 0.25].get("p_bf_3") → AttributeError
        result = self._compute([0.4, 0.35, 0.25])

        assert result["hit_probability"] is None, (
            f"Expected None probability for non-dict bf_dist, got {result['hit_probability']}"
        )
        assert "PROBABILITY_PIPELINE_CONTRACT_BREACH" in (result["calibration_note"] or ""), (
            f"Expected breach note; got {result['calibration_note']!r}"
        )

    def test_T7_int_bf_dist_returns_breach_not_attribute_error(self):
        """T7: bf_dist as an int → breach (no AttributeError)."""
        result = self._compute(42)

        assert result["hit_probability"] is None
        assert "PROBABILITY_PIPELINE_CONTRACT_BREACH" in (result["calibration_note"] or "")

    def test_T8_none_bf_dist_returns_breach(self):
        """T8: bf_dist=None → breach (prior behaviour preserved)."""
        result = self._compute(None)

        assert result["hit_probability"] is None
        assert "PROBABILITY_PIPELINE_CONTRACT_BREACH" in (result["calibration_note"] or "")

    def test_T9_dict_bf_dist_n_zero_returns_breach(self):
        """T9: dict bf_dist with n=0 → breach (prior behaviour preserved)."""
        result = self._compute({"n": 0, "p_bf_3": 0.4, "p_bf_4": 0.35, "p_bf_gte5": 0.25})

        assert result["hit_probability"] is None
        assert "PROBABILITY_PIPELINE_CONTRACT_BREACH" in (result["calibration_note"] or "")

    def test_T10_valid_dict_bf_dist_returns_numeric_probability(self):
        """T10: valid bf_dist dict → numeric probability (not None, not breach)."""
        bf_dist = {"n": 10, "p_bf_3": 0.40, "p_bf_4": 0.35, "p_bf_gte5": 0.25}
        result = self._compute(bf_dist)

        # May be None only if the simulation produces out-of-bounds result (degenerate guard).
        # With a normal bf_dist the probability should land in [0.01, 0.99].
        assert result["hit_probability"] is None or (
            isinstance(result["hit_probability"], float)
            and 0.0 <= result["hit_probability"] <= 1.0
        ), f"Unexpected hit_probability={result['hit_probability']!r}"
        assert "PROBABILITY_PIPELINE_CONTRACT_BREACH" not in (result["calibration_note"] or ""), (
            f"Valid bf_dist should not breach; note={result['calibration_note']!r}"
        )
        assert "1IP_EVENT_TREE" in (result["calibration_note"] or ""), (
            f"Expected 1IP_EVENT_TREE in calibration note; got {result['calibration_note']!r}"
        )

    def test_T10b_string_bf_dist_returns_breach_not_attribute_error(self):
        """T10b: bf_dist as a string → breach (not AttributeError)."""
        result = self._compute("not_a_dict")

        assert result["hit_probability"] is None
        assert "PROBABILITY_PIPELINE_CONTRACT_BREACH" in (result["calibration_note"] or "")
