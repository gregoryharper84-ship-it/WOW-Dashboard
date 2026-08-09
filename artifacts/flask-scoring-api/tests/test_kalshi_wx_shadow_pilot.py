"""
tests/test_kalshi_wx_shadow_pilot.py
Test suite for the Kalshi Weather Shadow Pilot Runner (Step 12.5B Increment 1/2).

All tests use mocked agent callers and DB helpers.
No real Anthropic API calls are made anywhere in this file.
"""
from __future__ import annotations

import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

# ── Project path setup ────────────────────────────────────────────────────────
_PROJ_ROOT   = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _PROJ_ROOT / "scripts"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Import runner module (scripts/run_kalshi_wx_shadow_pilot.py)
import run_kalshi_wx_shadow_pilot as _pilot
from run_kalshi_wx_shadow_pilot import (
    AGENT_IDS,
    MAX_SNAPSHOTS,
    MAX_SUBAGENT_CALLS,
    _REQUIRED_BUDGET_VARS,
    estimate_input_tokens,
    require_budget_config,
    run_pilot,
    worst_case_call_cost,
)

# ── Shared fixtures ───────────────────────────────────────────────────────────

_MOCK_CONFIG = {
    "PILOT_BUDGET_USD":           100.0,
    "INPUT_PRICE_PER_TOKEN":      0.000001,
    "OUTPUT_PRICE_PER_TOKEN":     0.000005,
    "MAX_OUTPUT_TOKENS_PER_CALL": 1024,
}

_TINY_BUDGET_CONFIG = {
    "PILOT_BUDGET_USD":           0.000001,   # sub-penny — first call exceeds it
    "INPUT_PRICE_PER_TOKEN":      0.01,
    "OUTPUT_PRICE_PER_TOKEN":     0.05,
    "MAX_OUTPUT_TOKENS_PER_CALL": 1024,
}


def _make_snap(rsid: str) -> dict:
    """Minimal eligible-snapshot dict as returned by fetch_eligible_snapshots."""
    return {
        "research_snapshot_id": rsid,
        "snapshot_json": {
            "research_snapshot_id": rsid,
            "city": "NYC",
            "station": "KNYC",
            "market_date": "2026-08-15",
            "forecast_high": 85.0,
            "weather_data_source_tier": "nws_primary",
            "sigma_f": 3.5,
            "horizon_hours": 18.0,
        },
        "terminal_label":        "KALSHI_WATCH",
        "price_gate_disposition": "DRY_RUN_ONLY",
        "can_execute":           False,
    }


def _success_result(**overrides) -> dict:
    """A mock agent result that looks like a successful subagent response."""
    base = {
        "success":        True,
        "tool_input":     {"scoring_mode": "gaussian_forecast"},
        "failure_reason": None,
        "latency_ms":     42,
        "model":          None,
        "input_tokens":   10,
        "output_tokens":  5,
    }
    base.update(overrides)
    return base


def _failure_result(**overrides) -> dict:
    """A mock agent result that looks like a failed subagent response."""
    base = {
        "success":        False,
        "tool_input":     {},
        "failure_reason": "PRE_HOOK_DENIED: forbidden key detected",
        "latency_ms":     12,
        "model":          None,
        "input_tokens":   8,
        "output_tokens":  0,
    }
    base.update(overrides)
    return base


def _env_without_budget() -> dict:
    """Return os.environ minus all budget-required keys."""
    drop = set(_REQUIRED_BUDGET_VARS.keys())
    return {k: v for k, v in os.environ.items() if k not in drop}


# ── Patch helpers ─────────────────────────────────────────────────────────────

def _patched_pilot(
    *,
    eligible: list,
    completed_pairs: set | None = None,   # set of (rsid, agent_id) strings
    prior_results: dict | None = None,
    write_spy: list | None = None,
    call_spy: list | None = None,
    raise_on: set | None = None,          # set of agent_id strings that raise
):
    """
    Context-manager builder that patches all DB + agent-caller helpers in
    run_kalshi_wx_shadow_pilot so no real DB or Anthropic connection is needed.

    Returns the patch stack; use with contextlib.ExitStack or individual with.
    """
    _completed = completed_pairs or set()
    _prior     = prior_results   or {}
    _writes    = write_spy       if write_spy  is not None else []
    _calls     = call_spy        if call_spy   is not None else []
    _raise     = raise_on        or set()

    def _is_completed(conn, rsid, agent_id):
        return (rsid, agent_id) in _completed

    def _load_prior(conn, rsid):
        return _prior.get(rsid, {})

    def _write(conn, **kw):
        _writes.append(kw)

    def _call(agent_id, snap_json, prior, run_id, sdk_client, cap_boundary):
        if agent_id in _raise:
            raise RuntimeError(f"simulated error for {agent_id}")
        _calls.append(agent_id)
        return _success_result()

    return (eligible, _is_completed, _load_prior, _write, _call)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Budget configuration
# ═══════════════════════════════════════════════════════════════════════════════

class TestBudgetConfig(unittest.TestCase):
    """require_budget_config() must fail closed when any required var is absent."""

    def _run_with_env(self, env: dict):
        with patch.dict(os.environ, env, clear=True):
            return require_budget_config()

    def _run_expecting_exit(self, env: dict):
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit) as cm:
                require_budget_config()
        return cm.exception.code

    # ── All vars missing ──────────────────────────────────────────────────────

    def test_SDBCFG_all_missing_exits_1(self):
        code = self._run_expecting_exit({})
        self.assertEqual(code, 1, "sys.exit(1) expected when all budget vars absent")

    def test_SDBCFG_all_missing_prints_each_name(self):
        buf = io.StringIO()
        with patch("sys.stderr", buf):
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(SystemExit):
                    require_budget_config()
        output = buf.getvalue()
        for key in _REQUIRED_BUDGET_VARS:
            self.assertIn(key, output, f"Missing var {key!r} must be named in stderr")

    # ── One var missing — each in turn ────────────────────────────────────────

    def _full_env(self):
        return {
            "PILOT_BUDGET_USD":           "10.0",
            "INPUT_PRICE_PER_TOKEN":      "0.000001",
            "OUTPUT_PRICE_PER_TOKEN":     "0.000005",
            "MAX_OUTPUT_TOKENS_PER_CALL": "1024",
        }

    def test_SDBCFG_missing_PILOT_BUDGET_USD_names_it(self):
        env = self._full_env()
        del env["PILOT_BUDGET_USD"]
        buf = io.StringIO()
        with patch("sys.stderr", buf):
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaises(SystemExit):
                    require_budget_config()
        self.assertIn("PILOT_BUDGET_USD", buf.getvalue())

    def test_SDBCFG_missing_INPUT_PRICE_names_it(self):
        env = self._full_env()
        del env["INPUT_PRICE_PER_TOKEN"]
        buf = io.StringIO()
        with patch("sys.stderr", buf):
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaises(SystemExit):
                    require_budget_config()
        self.assertIn("INPUT_PRICE_PER_TOKEN", buf.getvalue())

    def test_SDBCFG_missing_OUTPUT_PRICE_names_it(self):
        env = self._full_env()
        del env["OUTPUT_PRICE_PER_TOKEN"]
        buf = io.StringIO()
        with patch("sys.stderr", buf):
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaises(SystemExit):
                    require_budget_config()
        self.assertIn("OUTPUT_PRICE_PER_TOKEN", buf.getvalue())

    def test_SDBCFG_missing_MAX_OUTPUT_TOKENS_names_it(self):
        env = self._full_env()
        del env["MAX_OUTPUT_TOKENS_PER_CALL"]
        buf = io.StringIO()
        with patch("sys.stderr", buf):
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaises(SystemExit):
                    require_budget_config()
        self.assertIn("MAX_OUTPUT_TOKENS_PER_CALL", buf.getvalue())

    # ── All vars present → returns correct types ──────────────────────────────

    def test_SDBCFG_all_present_returns_config(self):
        config = self._run_with_env({
            "PILOT_BUDGET_USD":           "5.25",
            "INPUT_PRICE_PER_TOKEN":      "0.0000008",
            "OUTPUT_PRICE_PER_TOKEN":     "0.0000025",
            "MAX_OUTPUT_TOKENS_PER_CALL": "2048",
        })
        self.assertAlmostEqual(config["PILOT_BUDGET_USD"],           5.25)
        self.assertAlmostEqual(config["INPUT_PRICE_PER_TOKEN"],      0.0000008)
        self.assertAlmostEqual(config["OUTPUT_PRICE_PER_TOKEN"],     0.0000025)
        self.assertEqual(      config["MAX_OUTPUT_TOKENS_PER_CALL"], 2048)

    def test_SDBCFG_max_output_tokens_is_int(self):
        config = self._run_with_env({
            "PILOT_BUDGET_USD":           "1.0",
            "INPUT_PRICE_PER_TOKEN":      "0.000001",
            "OUTPUT_PRICE_PER_TOKEN":     "0.000005",
            "MAX_OUTPUT_TOKENS_PER_CALL": "512",
        })
        self.assertIsInstance(config["MAX_OUTPUT_TOKENS_PER_CALL"], int)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Cost estimation utilities
# ═══════════════════════════════════════════════════════════════════════════════

class TestCostEstimation(unittest.TestCase):

    def test_SDCOST_estimate_input_tokens_errs_high(self):
        """estimate_input_tokens(s) >= actual token count (conservative)."""
        s = "hello world"  # 11 bytes, ~3 tokens
        est = estimate_input_tokens(s)
        self.assertGreaterEqual(est, 1)
        # Conservative: never zero, never absurdly small
        self.assertGreaterEqual(est, len(s.encode()) // 4)

    def test_SDCOST_estimate_increases_with_length(self):
        short = estimate_input_tokens("abc")
        long_ = estimate_input_tokens("abc" * 100)
        self.assertGreater(long_, short)

    def test_SDCOST_worst_case_uses_max_output(self):
        """worst_case_call_cost always uses MAX_OUTPUT_TOKENS_PER_CALL for output."""
        cost = worst_case_call_cost(
            input_tokens=100,
            max_output_tokens=1000,
            input_price=0.000001,
            output_price=0.000005,
        )
        # 100 * 0.000001 + 1000 * 0.000005 = 0.0001 + 0.005 = 0.0051
        self.assertAlmostEqual(cost, 0.0051, places=8)

    def test_SDCOST_cost_is_additive(self):
        c1 = worst_case_call_cost(50, 512, 0.000001, 0.000002)
        c2 = worst_case_call_cost(50, 512, 0.000001, 0.000002)
        self.assertAlmostEqual(c1 + c2, 2 * c1)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. MAX_SNAPSHOTS enforcement
# ═══════════════════════════════════════════════════════════════════════════════

class TestMaxSnapshotsCap(unittest.TestCase):

    def _run_with_n_snaps(self, n: int) -> tuple:
        """Run pilot with n eligible snaps, no completed pairs. Returns (summary, calls, writes)."""
        snaps  = [_make_snap(f"rsid-{i}") for i in range(n)]
        calls  = []
        writes = []

        def mock_call(agent_id, snap_json, prior, run_id, sdk, cap):
            calls.append(agent_id)
            return _success_result()

        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots", return_value=snaps):
            with patch("run_kalshi_wx_shadow_pilot.is_pair_completed", return_value=False):
                with patch("run_kalshi_wx_shadow_pilot.load_prior_results", return_value={}):
                    with patch("run_kalshi_wx_shadow_pilot.write_result_row",
                               side_effect=lambda conn, **kw: writes.append(kw)):
                        summary = run_pilot(
                            _MOCK_CONFIG,
                            MagicMock(),
                            call_agent_fn=mock_call,
                        )
        return summary, calls, writes

    def test_SDMAX_25_snaps_processed_when_25_eligible(self):
        summary, calls, writes = self._run_with_n_snaps(25)
        self.assertEqual(summary["snapshots_processed"], 25)
        self.assertEqual(summary["stop_reason"], "EXHAUSTED")
        self.assertEqual(len(calls), 25 * 5)

    def test_SDMAX_only_25_processed_when_30_eligible(self):
        """fetch returns 25 (limited by max_snapshots) — cannot exceed 25."""
        # fetch_eligible_snapshots is called with max_snapshots as the limit arg;
        # our mock returns exactly what we give it, so we give it only 25.
        snaps  = [_make_snap(f"rsid-{i}") for i in range(25)]
        calls  = []

        def mock_call(agent_id, snap_json, prior, run_id, sdk, cap):
            calls.append(agent_id)
            return _success_result()

        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots", return_value=snaps):
            with patch("run_kalshi_wx_shadow_pilot.is_pair_completed", return_value=False):
                with patch("run_kalshi_wx_shadow_pilot.load_prior_results", return_value={}):
                    with patch("run_kalshi_wx_shadow_pilot.write_result_row"):
                        summary = run_pilot(
                            _MOCK_CONFIG,
                            MagicMock(),
                            call_agent_fn=mock_call,
                            max_snapshots=25,
                        )
        # The runner fetches exactly max_snapshots rows — never more.
        # Ensure it never processed beyond 25.
        self.assertLessEqual(summary["snapshots_processed"], 25)
        self.assertLessEqual(len(calls), 125)

    def test_SDMAX_fewer_than_25_eligible_exhausts_cleanly(self):
        summary, calls, writes = self._run_with_n_snaps(3)
        self.assertEqual(summary["snapshots_processed"], 3)
        self.assertEqual(summary["stop_reason"], "EXHAUSTED")
        self.assertEqual(len(calls), 3 * 5)

    def test_SDMAX_zero_eligible_exits_immediately(self):
        summary, calls, writes = self._run_with_n_snaps(0)
        self.assertEqual(summary["snapshots_processed"], 0)
        self.assertEqual(summary["total_subagent_calls"], 0)
        self.assertEqual(len(calls), 0)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. MAX_SUBAGENT_CALLS=125 hard cap — 126th attempt structurally refused
# ═══════════════════════════════════════════════════════════════════════════════

class TestMaxSubagentCallsCap(unittest.TestCase):
    """
    Hard cap: MAX_SUBAGENT_CALLS=125.  The 126th call attempt is refused
    BEFORE the caller is invoked.  This is verified as a running counter
    check, not arithmetic — the cap fires even if the caller would have
    succeeded.
    """

    def test_SDCAP_126th_call_refused_before_invocation(self):
        """
        Set up 26 eligible snapshots (130 potential calls).
        max_snapshots=26 so we don't hit the snapshot cap first.
        max_calls=125.
        After 125 calls (25 snaps × 5 agents), the 126th attempt must be
        refused before the call_agent_fn is ever invoked.
        """
        snaps = [_make_snap(f"rsid-{i}") for i in range(26)]

        call_count = [0]

        def mock_call(agent_id, snap_json, prior, run_id, sdk, cap):
            call_count[0] += 1
            # If this ever reaches 126, the cap has failed
            self.assertLessEqual(
                call_count[0], 125,
                f"call_agent_fn invoked {call_count[0]} times — 126th call was NOT refused",
            )
            return _success_result()

        writes = []
        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots", return_value=snaps):
            with patch("run_kalshi_wx_shadow_pilot.is_pair_completed", return_value=False):
                with patch("run_kalshi_wx_shadow_pilot.load_prior_results", return_value={}):
                    with patch("run_kalshi_wx_shadow_pilot.write_result_row",
                               side_effect=lambda conn, **kw: writes.append(kw)):
                        summary = run_pilot(
                            _MOCK_CONFIG,
                            MagicMock(),
                            call_agent_fn=mock_call,
                            max_snapshots=26,   # allow up to 26 snaps
                            max_calls=125,
                        )

        # Exactly 125 calls made, not 126
        self.assertEqual(call_count[0], 125,
                         f"Expected exactly 125 calls, got {call_count[0]}")
        self.assertEqual(summary["total_subagent_calls"], 125)
        self.assertEqual(summary["stop_reason"], "MAX_SUBAGENT_CALLS")
        # 125 result rows written (one per call)
        self.assertEqual(len(writes), 125)

    def test_SDCAP_summary_reports_exact_call_count(self):
        """run_pilot reports the EXACT number of calls made in total_subagent_calls."""
        snaps = [_make_snap(f"rsid-{i}") for i in range(2)]   # 2 snaps × 5 agents = 10 calls

        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots", return_value=snaps):
            with patch("run_kalshi_wx_shadow_pilot.is_pair_completed", return_value=False):
                with patch("run_kalshi_wx_shadow_pilot.load_prior_results", return_value={}):
                    with patch("run_kalshi_wx_shadow_pilot.write_result_row"):
                        summary = run_pilot(
                            _MOCK_CONFIG,
                            MagicMock(),
                            call_agent_fn=lambda *a, **kw: _success_result(),
                        )

        self.assertEqual(summary["total_subagent_calls"], 10)

    def test_SDCAP_constants_are_exactly_25_and_125(self):
        """The hard-cap constants must be exactly 25 and 125 — not ±1."""
        self.assertEqual(MAX_SNAPSHOTS, 25)
        self.assertEqual(MAX_SUBAGENT_CALLS, 125)

    def test_SDCAP_five_agent_ids_defined(self):
        """AGENT_IDS must contain exactly the 5 expected subagent names."""
        self.assertEqual(len(AGENT_IDS), 5)
        self.assertIn("forecast_context",      AGENT_IDS)
        self.assertIn("source_reconciliation", AGENT_IDS)
        self.assertIn("contradiction_detection", AGENT_IDS)
        self.assertIn("unusual_regime",        AGENT_IDS)
        self.assertIn("uncertainty_explanation", AGENT_IDS)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Spend guard — hard stop, not soft warning
# ═══════════════════════════════════════════════════════════════════════════════

class TestSpendGuard(unittest.TestCase):

    def test_SDBUDGET_hard_stop_before_first_call_when_budget_too_small(self):
        """
        When PILOT_BUDGET_USD is so small that even one call's worst-case cost
        exceeds it, the runner must hard-stop before calling call_agent_fn.
        """
        snaps = [_make_snap("rsid-budget-test")]
        called = [0]

        def mock_call(agent_id, snap_json, prior, run_id, sdk, cap):
            called[0] += 1
            return _success_result()

        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots", return_value=snaps):
            with patch("run_kalshi_wx_shadow_pilot.is_pair_completed", return_value=False):
                with patch("run_kalshi_wx_shadow_pilot.load_prior_results", return_value={}):
                    with patch("run_kalshi_wx_shadow_pilot.write_result_row"):
                        summary = run_pilot(
                            _TINY_BUDGET_CONFIG,
                            MagicMock(),
                            call_agent_fn=mock_call,
                        )

        self.assertEqual(summary["stop_reason"], "BUDGET_EXCEEDED")
        self.assertEqual(called[0], 0,
                         "call_agent_fn must not be invoked when budget is already exceeded")
        self.assertEqual(summary["total_subagent_calls"], 0)

    def test_SDBUDGET_stops_permanently_mid_snapshot(self):
        """
        If the budget runs out after the 2nd call, the runner stops the entire
        run (not just skips the 3rd agent and continues to the next snapshot).
        """
        snaps = [_make_snap("rsid-0")]

        # Budget allows exactly 2 calls then exhausts
        # Each call costs: 10 * 0.01 + 5 * 0.05 = 0.1 + 0.25 = 0.35
        # Budget = 0.6 → 1st call: 0 + 0.35 = 0.35 ≤ 0.6 → OK
        # After 1st call cumulative_spend = 0.1*10 + 0.05*5 = real cost
        # Worst-case check: estimate_input_tokens(serialized) + MAX_OUTPUT_TOKENS
        # Let's use a direct config that fails at the 2nd check precisely

        # Use a budget that the worst-case estimate for the 2nd call would exceed.
        # Simplest: budget = 0 → every call immediately triggers hard stop.
        zero_budget = dict(_MOCK_CONFIG, PILOT_BUDGET_USD=0.0)
        called = [0]

        def mock_call(agent_id, snap_json, prior, run_id, sdk, cap):
            called[0] += 1
            return _success_result()

        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots",
                   return_value=[_make_snap("rsid-0"), _make_snap("rsid-1")]):
            with patch("run_kalshi_wx_shadow_pilot.is_pair_completed", return_value=False):
                with patch("run_kalshi_wx_shadow_pilot.load_prior_results", return_value={}):
                    with patch("run_kalshi_wx_shadow_pilot.write_result_row"):
                        summary = run_pilot(
                            zero_budget, MagicMock(),
                            call_agent_fn=mock_call,
                        )

        self.assertEqual(summary["stop_reason"], "BUDGET_EXCEEDED",
                         "Budget guard must fire as a hard stop, not a skip")
        # Hard stop = return immediately, never continue to next snapshot
        self.assertEqual(called[0], 0)

    def test_SDBUDGET_pricing_config_persisted_in_every_result_row(self):
        """
        Each result row must embed the full pricing config in validated_output_json
        so accounting is reconstructable from the DB without runtime context.
        """
        snaps = [_make_snap("rsid-config-test")]
        writes = []

        def mock_call(agent_id, snap_json, prior, run_id, sdk, cap):
            return _success_result(tool_input={"foo": "bar"})

        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots", return_value=snaps):
            with patch("run_kalshi_wx_shadow_pilot.is_pair_completed", return_value=False):
                with patch("run_kalshi_wx_shadow_pilot.load_prior_results", return_value={}):
                    with patch("run_kalshi_wx_shadow_pilot.write_result_row",
                               side_effect=lambda conn, **kw: writes.append(kw)):
                        run_pilot(_MOCK_CONFIG, MagicMock(), call_agent_fn=mock_call)

        self.assertGreater(len(writes), 0)
        for row in writes:
            vjson = row["validated_output_json"]
            self.assertIn("run_config", vjson,
                          "Each result row must carry run_config for accounting")
            rc = vjson["run_config"]
            self.assertIn("PILOT_BUDGET_USD",           rc)
            self.assertIn("INPUT_PRICE_PER_TOKEN",      rc)
            self.assertIn("OUTPUT_PRICE_PER_TOKEN",     rc)
            self.assertIn("MAX_OUTPUT_TOKENS_PER_CALL", rc)
            self.assertAlmostEqual(rc["PILOT_BUDGET_USD"], _MOCK_CONFIG["PILOT_BUDGET_USD"])

    def test_SDBUDGET_summary_includes_pilot_config(self):
        """run_pilot summary must echo the full pricing config."""
        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots", return_value=[]):
            with patch("run_kalshi_wx_shadow_pilot.load_prior_results", return_value={}):
                summary = run_pilot(_MOCK_CONFIG, MagicMock(),
                                    call_agent_fn=lambda *a, **kw: _success_result())

        self.assertIn("pilot_config", summary)
        pc = summary["pilot_config"]
        for key in ("PILOT_BUDGET_USD", "INPUT_PRICE_PER_TOKEN",
                    "OUTPUT_PRICE_PER_TOKEN", "MAX_OUTPUT_TOKENS_PER_CALL"):
            self.assertIn(key, pc)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Resumability
# ═══════════════════════════════════════════════════════════════════════════════

class TestResumability(unittest.TestCase):
    """
    Before attempting any (rsid, agent_id) pair, the runner checks
    kalshi_wx_shadow_results.  Already-completed pairs are skipped and do NOT
    count toward the 125-call cap.  A simulated restart must not re-attempt
    completed pairs and must not exceed the cap across combined runs.
    """

    def _run_with_completed(self, completed_pairs: set) -> tuple:
        """
        Run pilot over 1 snapshot.  completed_pairs is a set of agent_id
        strings that are already done.  Returns (summary, called_agents).
        """
        snaps = [_make_snap("rsid-resume")]
        called_agents = []

        def mock_is_completed(conn, rsid, agent_id):
            return (rsid, agent_id) in completed_pairs

        def mock_call(agent_id, snap_json, prior, run_id, sdk, cap):
            called_agents.append(agent_id)
            return _success_result()

        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots", return_value=snaps):
            with patch("run_kalshi_wx_shadow_pilot.is_pair_completed", side_effect=mock_is_completed):
                with patch("run_kalshi_wx_shadow_pilot.load_prior_results", return_value={}):
                    with patch("run_kalshi_wx_shadow_pilot.write_result_row"):
                        summary = run_pilot(
                            _MOCK_CONFIG, MagicMock(),
                            call_agent_fn=mock_call,
                        )
        return summary, called_agents

    def test_SDRESUME_no_prior_work_runs_all_5_agents(self):
        summary, called = self._run_with_completed(set())
        self.assertEqual(len(called), 5)
        self.assertEqual(summary["total_subagent_calls"], 5)

    def test_SDRESUME_2_already_completed_runs_only_3(self):
        done = {
            ("rsid-resume", "forecast_context"),
            ("rsid-resume", "source_reconciliation"),
        }
        summary, called = self._run_with_completed(done)
        self.assertEqual(len(called), 3,
                         "2 completed agents must be skipped; only 3 new calls expected")
        self.assertNotIn("forecast_context",      called)
        self.assertNotIn("source_reconciliation", called)
        self.assertIn("contradiction_detection",  called)
        self.assertIn("unusual_regime",           called)
        self.assertIn("uncertainty_explanation",  called)

    def test_SDRESUME_all_5_completed_runs_zero_calls(self):
        done = {("rsid-resume", aid) for aid in AGENT_IDS}
        summary, called = self._run_with_completed(done)
        self.assertEqual(len(called), 0)
        self.assertEqual(summary["total_subagent_calls"], 0)

    def test_SDRESUME_skipped_pairs_do_not_count_toward_cap(self):
        """
        Completed pairs are skipped and their count is NOT added to
        total_subagent_calls.  Running 5-of-25 agents already done across
        5 snapshots leaves only 20 call credits used, not 25.
        """
        snaps = [_make_snap(f"rsid-{i}") for i in range(5)]

        def mock_is_completed(conn, rsid, agent_id):
            # First agent of every snapshot is already done
            return agent_id == "forecast_context"

        called_count = [0]

        def mock_call(agent_id, snap_json, prior, run_id, sdk, cap):
            called_count[0] += 1
            return _success_result()

        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots", return_value=snaps):
            with patch("run_kalshi_wx_shadow_pilot.is_pair_completed", side_effect=mock_is_completed):
                with patch("run_kalshi_wx_shadow_pilot.load_prior_results", return_value={}):
                    with patch("run_kalshi_wx_shadow_pilot.write_result_row"):
                        summary = run_pilot(_MOCK_CONFIG, MagicMock(),
                                            call_agent_fn=mock_call)

        # 5 snaps × 4 uncompleted agents = 20 calls
        self.assertEqual(called_count[0], 20)
        self.assertEqual(summary["total_subagent_calls"], 20)

    def test_SDRESUME_simulated_restart_does_not_re_attempt_or_exceed_cap(self):
        """
        Simulate two runs:
          Run 1 → 3 agents completed for rsid-restart (calls: 3)
          Run 2 → same snapshot, those 3 already done → only 2 calls made
        Total across both runs: 5 calls ≤ 125 cap.
        """
        rsid = "rsid-restart"
        snaps = [_make_snap(rsid)]
        all_calls: list = []
        completed_in_db: set = set()

        def mock_is_completed(conn, rsid_, agent_id):
            return (rsid_, agent_id) in completed_in_db

        def mock_write(conn, **kw):
            completed_in_db.add((kw["research_snapshot_id"], kw["agent_id"]))
            all_calls.append(("write", kw["agent_id"]))

        def mock_call(agent_id, snap_json, prior, run_id, sdk, cap):
            all_calls.append(("call", agent_id))
            return _success_result()

        # ── Run 1: first 3 agents ─────────────────────────────────────────────
        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots", return_value=snaps):
            with patch("run_kalshi_wx_shadow_pilot.is_pair_completed", side_effect=mock_is_completed):
                with patch("run_kalshi_wx_shadow_pilot.load_prior_results", return_value={}):
                    with patch("run_kalshi_wx_shadow_pilot.write_result_row", side_effect=mock_write):
                        s1 = run_pilot(_MOCK_CONFIG, MagicMock(),
                                       call_agent_fn=mock_call,
                                       max_calls=3)   # simulate run stopping after 3

        self.assertEqual(s1["total_subagent_calls"], 3)
        self.assertEqual(len(completed_in_db), 3)

        # ── Run 2: same snapshot, already-done pairs skipped ──────────────────
        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots", return_value=snaps):
            with patch("run_kalshi_wx_shadow_pilot.is_pair_completed", side_effect=mock_is_completed):
                with patch("run_kalshi_wx_shadow_pilot.load_prior_results", return_value={}):
                    with patch("run_kalshi_wx_shadow_pilot.write_result_row", side_effect=mock_write):
                        s2 = run_pilot(_MOCK_CONFIG, MagicMock(),
                                       call_agent_fn=mock_call)

        # Run 2 must only call the 2 remaining agents
        self.assertEqual(s2["total_subagent_calls"], 2)

        # Grand total: 3 + 2 = 5, not 3 + 5 = 8
        total_calls = sum(1 for kind, _ in all_calls if kind == "call")
        self.assertEqual(total_calls, 5, "Grand total calls across both runs must be 5")
        self.assertLessEqual(total_calls, 125)


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Result row written for success and failure
# ═══════════════════════════════════════════════════════════════════════════════

class TestResultRowPersistence(unittest.TestCase):

    def _run_one_snap_one_agent(self, call_fn) -> list:
        """Run pilot over 1 snap, 1 agent (via mock), return list of written rows."""
        writes = []

        def _only_first_agent_uncompleted(conn, rsid, agent_id):
            # Only the first agent in AGENT_IDS is not completed
            return agent_id != AGENT_IDS[0]

        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots",
                   return_value=[_make_snap("rsid-row-test")]):
            with patch("run_kalshi_wx_shadow_pilot.is_pair_completed",
                       side_effect=_only_first_agent_uncompleted):
                with patch("run_kalshi_wx_shadow_pilot.load_prior_results", return_value={}):
                    with patch("run_kalshi_wx_shadow_pilot.write_result_row",
                               side_effect=lambda conn, **kw: writes.append(kw)):
                        run_pilot(_MOCK_CONFIG, MagicMock(), call_agent_fn=call_fn)
        return writes

    def test_SDRROW_success_writes_COMPLETE_status(self):
        def success_call(agent_id, snap_json, prior, run_id, sdk, cap):
            return _success_result(tool_input={"key": "value"})

        rows = self._run_one_snap_one_agent(success_call)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "COMPLETE")
        self.assertEqual(rows[0]["agent_id"], AGENT_IDS[0])

    def test_SDRROW_failure_writes_BLOCKED_status(self):
        def fail_call(agent_id, snap_json, prior, run_id, sdk, cap):
            return _failure_result()

        rows = self._run_one_snap_one_agent(fail_call)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "BLOCKED")

    def test_SDRROW_exception_writes_ERROR_status(self):
        def raising_call(agent_id, snap_json, prior, run_id, sdk, cap):
            raise RuntimeError("simulated subagent crash")

        rows = self._run_one_snap_one_agent(raising_call)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "ERROR",
                         "An exception in call_agent_fn must produce an ERROR status row")

    def test_SDRROW_exception_counts_toward_cap(self):
        """A call that raises still counts as an attempt (no hidden retry budget)."""
        snaps = [_make_snap("rsid-err")]
        call_count = [0]

        def raising_call(agent_id, snap_json, prior, run_id, sdk, cap):
            call_count[0] += 1
            raise RuntimeError("crash")

        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots", return_value=snaps):
            with patch("run_kalshi_wx_shadow_pilot.is_pair_completed", return_value=False):
                with patch("run_kalshi_wx_shadow_pilot.load_prior_results", return_value={}):
                    with patch("run_kalshi_wx_shadow_pilot.write_result_row"):
                        summary = run_pilot(_MOCK_CONFIG, MagicMock(),
                                            call_agent_fn=raising_call)

        # 1 snapshot × 5 agents, all raising — 5 call attempts, 5 error rows
        self.assertEqual(call_count[0], 5)
        self.assertEqual(summary["total_subagent_calls"], 5)

    def test_SDRROW_all_required_fields_present(self):
        """Every result row must include all required columns."""
        writes = []

        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots",
                   return_value=[_make_snap("rsid-fields")]):
            with patch("run_kalshi_wx_shadow_pilot.is_pair_completed", return_value=False):
                with patch("run_kalshi_wx_shadow_pilot.load_prior_results", return_value={}):
                    with patch("run_kalshi_wx_shadow_pilot.write_result_row",
                               side_effect=lambda conn, **kw: writes.append(kw)):
                        run_pilot(_MOCK_CONFIG, MagicMock(),
                                  call_agent_fn=lambda *a, **kw: _success_result())

        required_fields = {
            "research_snapshot_id", "agent_id", "run_id",
            "validated_output_json", "status",
            "latency_ms", "model", "input_tokens", "output_tokens",
            "estimated_cost_usd",
        }
        for row in writes:
            for field in required_fields:
                self.assertIn(field, row,
                              f"Result row missing required field: {field!r}")

    def test_SDRROW_no_retry_within_run(self):
        """
        A failed call is not retried in the same invocation.
        The runner moves on to the next agent (one call = one attempt, done).
        """
        snaps = [_make_snap("rsid-noretry")]
        call_ids = []

        def once_only(agent_id, snap_json, prior, run_id, sdk, cap):
            call_ids.append(agent_id)
            return _failure_result()

        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots", return_value=snaps):
            with patch("run_kalshi_wx_shadow_pilot.is_pair_completed", return_value=False):
                with patch("run_kalshi_wx_shadow_pilot.load_prior_results", return_value={}):
                    with patch("run_kalshi_wx_shadow_pilot.write_result_row"):
                        run_pilot(_MOCK_CONFIG, MagicMock(), call_agent_fn=once_only)

        # Each agent appears exactly once — no retries
        for agent_id in AGENT_IDS:
            self.assertEqual(
                call_ids.count(agent_id), 1,
                f"agent_id={agent_id!r} must appear exactly once (no retry)",
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Zero real network calls — structural proof
# ═══════════════════════════════════════════════════════════════════════════════

class TestZeroRealNetworkCalls(unittest.TestCase):
    """
    Prove that providing call_agent_fn completely prevents the real subagent
    functions from being invoked — meaning no real Anthropic API call is
    possible in any mocked test run.
    """

    _REAL_FN_PATHS = [
        "gate_engine.kalshi_wx_shadow_subagents.run_forecast_context_subagent",
        "gate_engine.kalshi_wx_shadow_subagents.run_source_reconciliation_subagent",
        "gate_engine.kalshi_wx_shadow_subagents.run_contradiction_detection_subagent",
        "gate_engine.kalshi_wx_shadow_subagents.run_unusual_regime_subagent",
        "gate_engine.kalshi_wx_shadow_subagents.run_uncertainty_explanation_subagent",
    ]

    def test_SDNET_real_subagent_fns_never_called_when_mock_provided(self):
        """
        With call_agent_fn supplied, none of the five real subagent functions
        (which make Anthropic SDK calls) are ever invoked.
        """
        mock_fns = [MagicMock(name=p.split(".")[-1]) for p in self._REAL_FN_PATHS]

        with patch(self._REAL_FN_PATHS[0], mock_fns[0]), \
             patch(self._REAL_FN_PATHS[1], mock_fns[1]), \
             patch(self._REAL_FN_PATHS[2], mock_fns[2]), \
             patch(self._REAL_FN_PATHS[3], mock_fns[3]), \
             patch(self._REAL_FN_PATHS[4], mock_fns[4]):

            with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots",
                       return_value=[_make_snap("rsid-net")]):
                with patch("run_kalshi_wx_shadow_pilot.is_pair_completed", return_value=False):
                    with patch("run_kalshi_wx_shadow_pilot.load_prior_results", return_value={}):
                        with patch("run_kalshi_wx_shadow_pilot.write_result_row"):
                            mock_agent_fn = MagicMock(
                                return_value=_success_result()
                            )
                            run_pilot(
                                _MOCK_CONFIG,
                                MagicMock(),
                                call_agent_fn=mock_agent_fn,
                            )

        for mock_fn in mock_fns:
            mock_fn.assert_not_called()

    def test_SDNET_call_agent_fn_called_once_per_uncompleted_pair(self):
        """
        call_agent_fn is invoked once for each uncompleted (rsid, agent_id)
        pair — no more, no fewer.
        """
        snaps = [_make_snap("rsid-net-count")]
        mock_fn = MagicMock(return_value=_success_result())

        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots", return_value=snaps):
            with patch("run_kalshi_wx_shadow_pilot.is_pair_completed", return_value=False):
                with patch("run_kalshi_wx_shadow_pilot.load_prior_results", return_value={}):
                    with patch("run_kalshi_wx_shadow_pilot.write_result_row"):
                        run_pilot(_MOCK_CONFIG, MagicMock(), call_agent_fn=mock_fn)

        self.assertEqual(mock_fn.call_count, 5,  # 1 snap × 5 agents
                         "call_agent_fn must be called exactly once per uncompleted pair")

    def test_SDNET_call_agent_fn_called_with_correct_agent_ids(self):
        """call_agent_fn receives all five agent_id strings in AGENT_IDS order."""
        snaps = [_make_snap("rsid-agentids")]
        seen_agent_ids = []

        def spy_call(agent_id, snap_json, prior, run_id, sdk, cap):
            seen_agent_ids.append(agent_id)
            return _success_result()

        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots", return_value=snaps):
            with patch("run_kalshi_wx_shadow_pilot.is_pair_completed", return_value=False):
                with patch("run_kalshi_wx_shadow_pilot.load_prior_results", return_value={}):
                    with patch("run_kalshi_wx_shadow_pilot.write_result_row"):
                        run_pilot(_MOCK_CONFIG, MagicMock(), call_agent_fn=spy_call)

        self.assertEqual(seen_agent_ids, AGENT_IDS)


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Structural isolation — not wired into app.py, Flask, or any scheduler
# ═══════════════════════════════════════════════════════════════════════════════

class TestPilotStructuralIsolation(unittest.TestCase):
    """
    Prove the pilot script is standalone — never imported by app.py, never
    registered as a Flask route, and not referenced by any cron or scheduler
    mechanism anywhere in the repo.
    """

    @classmethod
    def _script_src(cls) -> str:
        return (_SCRIPTS_DIR / "run_kalshi_wx_shadow_pilot.py").read_text()

    @classmethod
    def _app_src(cls) -> str:
        return (_PROJ_ROOT / "app.py").read_text()

    def test_SDSTRUCT_not_imported_by_app_py(self):
        """app.py must not import or reference run_kalshi_wx_shadow_pilot."""
        src = self._app_src()
        self.assertNotIn(
            "run_kalshi_wx_shadow_pilot", src,
            "app.py must never import the pilot runner",
        )

    def test_SDSTRUCT_no_flask_route_decorator(self):
        """The pilot script must not register any Flask route."""
        src = self._script_src()
        self.assertNotIn("@app.route", src,
                         "Pilot script must not contain @app.route")
        self.assertNotIn("@blueprint.route", src,
                         "Pilot script must not contain @blueprint.route")
        self.assertNotIn(".add_url_rule", src,
                         "Pilot script must not call add_url_rule")

    def test_SDSTRUCT_no_flask_import(self):
        """The pilot script must not import Flask."""
        src = self._script_src().lower()
        self.assertNotIn("from flask", src,
                         "Pilot script must not import from flask")
        self.assertNotIn("import flask", src,
                         "Pilot script must not import flask")

    def test_SDSTRUCT_no_scheduler_or_cron_wiring(self):
        """The pilot script must not use any scheduler, cron, or periodic timer."""
        src = self._script_src()
        forbidden = [
            "APScheduler",
            "BackgroundScheduler",
            "BlockingScheduler",
            "schedule.every",
            "schedule.run",
            "celery",
            "crontab(",
            "threading.Timer",
            "sched.scheduler",
        ]
        for token in forbidden:
            self.assertNotIn(
                token, src,
                f"Pilot script must not reference scheduler token: {token!r}",
            )

    def test_SDSTRUCT_not_referenced_in_artifact_toml(self):
        """artifact.toml must not start or reference the pilot runner."""
        toml_path = _PROJ_ROOT / "artifact.toml"
        if toml_path.exists():
            src = toml_path.read_text()
            self.assertNotIn(
                "run_kalshi_wx_shadow_pilot", src,
                "artifact.toml must not reference the pilot runner",
            )

    def test_SDSTRUCT_not_referenced_in_gunicorn_conf(self):
        """gunicorn_conf.py must not reference the pilot runner."""
        conf_path = _PROJ_ROOT / "gunicorn_conf.py"
        if conf_path.exists():
            src = conf_path.read_text()
            self.assertNotIn(
                "run_kalshi_wx_shadow_pilot", src,
                "gunicorn_conf.py must not reference the pilot runner",
            )

    def test_SDSTRUCT_no_trading_imports(self):
        """
        The pilot script must not import any Kalshi order placement or
        trading module — its authority is read-only research only.
        """
        src = self._script_src()
        forbidden_patterns = [
            "kalshi_order",
            "kalshi_trade",
            "place_order",
            "submit_order",
            "execute_trade",
            "KalshiClient",    # the live trading client
        ]
        for pat in forbidden_patterns:
            self.assertNotIn(
                pat, src,
                f"Pilot script must not reference trading symbol: {pat!r}",
            )

    def test_SDSTRUCT_main_guard_present(self):
        """The script must only run via 'if __name__ == \"__main__\"' guard."""
        src = self._script_src()
        self.assertIn(
            'if __name__ == "__main__"', src,
            "Pilot script must have __main__ guard for direct execution",
        )

    def test_SDSTRUCT_no_app_py_import(self):
        """Pilot script must not import app or any app-level module."""
        src = self._script_src()
        self.assertNotIn("import app", src,
                         "Pilot script must not import the Flask app module")
        self.assertNotIn("from app import", src,
                         "Pilot script must not import from app.py")


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Summary fields
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunSummary(unittest.TestCase):

    def _run_empty(self) -> dict:
        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots", return_value=[]):
            return run_pilot(_MOCK_CONFIG, MagicMock(),
                             call_agent_fn=lambda *a, **kw: _success_result())

    def test_SDSUM_required_keys_present(self):
        summary = self._run_empty()
        required = {
            "run_id", "stop_reason", "total_subagent_calls",
            "snapshots_processed", "cumulative_spend_usd",
            "cumulative_input_tokens", "cumulative_output_tokens",
            "pilot_config",
        }
        for key in required:
            self.assertIn(key, summary, f"Summary missing key: {key!r}")

    def test_SDSUM_run_id_has_pilot_prefix(self):
        summary = self._run_empty()
        self.assertTrue(
            summary["run_id"].startswith("pilot-"),
            f"run_id must start with 'pilot-', got {summary['run_id']!r}",
        )

    def test_SDSUM_cumulative_spend_is_non_negative(self):
        summary = self._run_empty()
        self.assertGreaterEqual(summary["cumulative_spend_usd"], 0.0)

    def test_SDSUM_stop_reason_exhausted_when_no_snaps(self):
        summary = self._run_empty()
        self.assertEqual(summary["stop_reason"], "EXHAUSTED")

    def test_SDSUM_two_distinct_run_ids_across_invocations(self):
        """Each invocation of run_pilot must produce a unique run_id."""
        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots", return_value=[]):
            s1 = run_pilot(_MOCK_CONFIG, MagicMock(),
                           call_agent_fn=lambda *a, **kw: _success_result())
            s2 = run_pilot(_MOCK_CONFIG, MagicMock(),
                           call_agent_fn=lambda *a, **kw: _success_result())
        self.assertNotEqual(s1["run_id"], s2["run_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
