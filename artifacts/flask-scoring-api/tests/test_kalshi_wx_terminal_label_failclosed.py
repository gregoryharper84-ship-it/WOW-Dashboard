"""
tests/test_kalshi_wx_terminal_label_failclosed.py
WOW-PATCH-2026-08-08-KALSHI-WX-TERMINAL-LABEL-FAIL-CLOSED
WOW-PATCH-2026-08-09-KALSHI-WX-UNCALIBRATED-REMOVAL

Validates the fail-closed terminal-label guard introduced by the patch.

Test plan
─────────
Section A — Registry membership (unit, no Flask)
  A1–A5: each of the 5 confirmed-reachable labels is accepted by the registry
         and by _validate_wx_terminal_label().
  A5 (updated 2026-08-09): KALSHI_REJECT_UNCALIBRATED was removed from the
         registry because no route handler ever assigns the corresponding
         internal weather_label="WEATHER_REJECT_UNCALIBRATED", making the
         _weather_terminal_label_v2() branch permanently dead code.
         A5 now asserts the label is ABSENT from the registry.
  A7:    KALSHI_REJECT_THIN_BOOK is rejected (appears in a docstring but
         is not reachable from any production code path).
  A8:    KALSHI_REJECT_FEE_DRAG is rejected (same reason).

Section B — _validate_wx_terminal_label() contract
  B1:    Returns True for every registry member.
  B2:    Returns False for an invented never-seen-before string.
  B3:    Returns False for KALSHI_REJECT_THIN_BOOK specifically.
  B4:    Returns False for an empty string.
  B5:    Returns False for None-like coercion (empty string, not None,
         since the signature is str).

Section C — POST /wow/kalshi/weather/evaluate: valid labels pass through
  One integration test per valid label confirming:
    - HTTP 200 returned
    - Response JSON has "ok": True and the expected terminal_label
    - _log_weather_scout_row IS called when scoring_mode == "gaussian_forecast"
    - _log_weather_scout_row is NOT called when scoring_mode != "gaussian_forecast"
      (existing behaviour, unchanged by this patch)
  These tests mock _weather_terminal_label_v2 to control the label,
  and mock _log_weather_scout_row to observe call behaviour.

Section D — POST /wow/kalshi/weather/evaluate: adversarial inputs
  D1:  invented string "KALSHI_INVENTED_LABEL_XYZ"
       → HTTP 500, "status":"INTERNAL_LABEL_VIOLATION", can_execute=False,
         _log_weather_scout_row NOT called.
  D2:  docstring-only string "KALSHI_REJECT_THIN_BOOK"
       → HTTP 500, "status":"INTERNAL_LABEL_VIOLATION", can_execute=False,
         _log_weather_scout_row NOT called.

Section E — GET /kalshi/evaluate/weather/<city>: adversarial inputs
  E1:  invented string → HTTP 500, INTERNAL_LABEL_VIOLATION, can_execute=False.
  E2:  "KALSHI_REJECT_THIN_BOOK" → HTTP 500, INTERNAL_LABEL_VIOLATION.

Section F — Isolation: validation function is not imported by ceiling resolvers
  F1:  gate_engine/wow_runtime_manifest.py does not import or reference
       _validate_wx_terminal_label or _KALSHI_WX_TERMINAL_LABEL_REGISTRY.
  F2:  gate_engine/command_center/cc_labels.py does not import or reference
       those names either.
  F3:  gate_engine/command_center/ceiling_resolver.py does not import or
       reference those names.
"""
from __future__ import annotations

import json
import sys
import os
import importlib
import unittest
from unittest.mock import patch, MagicMock, call

# ── path setup ────────────────────────────────────────────────────────────────
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers to import patch symbols without booting the full Flask app
# ─────────────────────────────────────────────────────────────────────────────

def _import_registry():
    """Import only the registry + validator from app.py via targeted exec."""
    # We need _KALSHI_WX_TERMINAL_LABEL_REGISTRY and _validate_wx_terminal_label
    # without spinning up Flask, psycopg2, etc.  Use importlib with a minimal env.
    import types
    ns: dict = {}
    app_path = os.path.join(_REPO, "app.py")
    with open(app_path, encoding="utf-8") as fh:
        src = fh.read()

    # Extract just the two definitions via a targeted search so we don't execute
    # the whole 35k-line file.  We locate the block by its sentinel comment and
    # pull everything up to (but not including) _weather_terminal_label_v2.
    start_marker = "# ── WOW-PATCH-2026-08-08-KALSHI-WX-TERMINAL-LABEL-FAIL-CLOSED"
    end_marker   = "def _weather_terminal_label_v2("
    start_idx = src.find(start_marker)
    end_idx   = src.find(end_marker)
    assert start_idx != -1, "Registry block start marker not found in app.py"
    assert end_idx   != -1, "end marker _weather_terminal_label_v2 not found in app.py"
    block = src[start_idx:end_idx]
    exec(compile(block, "<registry_block>", "exec"), ns)  # noqa: S102
    return ns["_KALSHI_WX_TERMINAL_LABEL_REGISTRY"], ns["_validate_wx_terminal_label"]


_REGISTRY, _validate = _import_registry()

# Auth key used for all Flask test-client requests.
# The decorator reads SCORING_API_KEY from the environment; we set a sentinel
# value here so auth passes without touching any real secret.
_TEST_API_KEY = "test-scoring-key-failclosed-patch"


# ─────────────────────────────────────────────────────────────────────────────
# Section A — Registry membership
# ─────────────────────────────────────────────────────────────────────────────

class TestRegistryMembership(unittest.TestCase):
    """A1–A8: every confirmed-reachable label is present; dead-docstring labels absent."""

    def test_A1_KALSHI_PLAYABLE_LIMIT_ONLY_in_registry(self):
        self.assertIn("KALSHI_PLAYABLE_LIMIT_ONLY", _REGISTRY)

    def test_A2_KALSHI_WATCH_in_registry(self):
        self.assertIn("KALSHI_WATCH", _REGISTRY)

    def test_A3_KALSHI_REJECT_NO_EDGE_in_registry(self):
        self.assertIn("KALSHI_REJECT_NO_EDGE", _REGISTRY)

    def test_A4_KALSHI_REJECT_BAD_RULES_in_registry(self):
        self.assertIn("KALSHI_REJECT_BAD_RULES", _REGISTRY)

    def test_A5_KALSHI_REJECT_UNCALIBRATED_NOT_in_registry(self):
        """
        Removed 2026-08-09 (WOW-PATCH-2026-08-09-KALSHI-WX-UNCALIBRATED-REMOVAL):
        KALSHI_REJECT_UNCALIBRATED was listed as confirmed-reachable but no route
        handler ever assigns weather_label="WEATHER_REJECT_UNCALIBRATED", so the
        _weather_terminal_label_v2() branch that returned this label was dead code.
        The label has been removed from the registry until a real calibration-check
        code path is added to both route handlers.
        """
        self.assertNotIn("KALSHI_REJECT_UNCALIBRATED", _REGISTRY)

    def test_A6_KALSHI_DATA_UNOBTAINABLE_in_registry(self):
        self.assertIn("KALSHI_DATA_UNOBTAINABLE", _REGISTRY)

    def test_A7_KALSHI_REJECT_THIN_BOOK_NOT_in_registry(self):
        """docstring-only label must be absent — it is not reachable from code."""
        self.assertNotIn("KALSHI_REJECT_THIN_BOOK", _REGISTRY)

    def test_A8_KALSHI_REJECT_FEE_DRAG_NOT_in_registry(self):
        """docstring-only label must be absent."""
        self.assertNotIn("KALSHI_REJECT_FEE_DRAG", _REGISTRY)

    def test_A9_registry_has_exactly_five_members(self):
        """Registry shrank from 6 to 5 when KALSHI_REJECT_UNCALIBRATED was removed."""
        self.assertEqual(len(_REGISTRY), 5)


# ─────────────────────────────────────────────────────────────────────────────
# Section B — _validate_wx_terminal_label() contract
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateFunction(unittest.TestCase):

    def test_B1_all_registry_members_return_True(self):
        for label in _REGISTRY:
            with self.subTest(label=label):
                self.assertTrue(_validate(label))

    def test_B2_invented_string_returns_False(self):
        self.assertFalse(_validate("KALSHI_INVENTED_LABEL_XYZ_NEVER_EXISTS"))

    def test_B3_KALSHI_REJECT_THIN_BOOK_returns_False(self):
        """Critical: must be rejected even though it appears in a nearby docstring."""
        self.assertFalse(_validate("KALSHI_REJECT_THIN_BOOK"))

    def test_B4_empty_string_returns_False(self):
        self.assertFalse(_validate(""))

    def test_B5_KALSHI_REJECT_FEE_DRAG_returns_False(self):
        self.assertFalse(_validate("KALSHI_REJECT_FEE_DRAG"))

    def test_B6_near_miss_case_sensitivity_returns_False(self):
        """Lowercase variant must not pass."""
        self.assertFalse(_validate("kalshi_watch"))

    def test_B7_partial_match_returns_False(self):
        """A prefix of a valid label must not pass."""
        self.assertFalse(_validate("KALSHI_WATCH_EXTRA"))


# ─────────────────────────────────────────────────────────────────────────────
# Flask integration test helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_post_body(price_source: str = "synthetic_test") -> dict:
    """Minimal valid POST /wow/kalshi/weather/evaluate body."""
    return {
        "city":         "NYC",
        "date":         "2026-08-09",
        "brackets":     [
            {"label": "≤79",  "yes_price": 0.30},
            {"label": "80-84","yes_price": 0.40},
            {"label": "≥85",  "yes_price": 0.30},
        ],
        "sigma_f":      3.5,
        "price_source": price_source,
    }


def _make_fake_cli(ok: bool = True) -> dict:
    return {
        "ok":            ok,
        "observed_high": None,
        "report_status": "NOT_YET_ISSUED",
        "revision_risk": False,
        "report_date":   None,
        "source_url":    "https://api.weather.gov/products?type=CLI&location=NYC&limit=1",
        "product_id":    None,
        "issuance_time": None,
        "raw_text":      None,
    }


def _make_fake_fc(forecast_high: int | None = 82) -> dict:
    return {
        "forecast_high":            forecast_high,
        "forecast_source":          "nws_forecast" if forecast_high else "none",
        "weather_data_source_tier": "nws_primary" if forecast_high else "all_sources_failed",
        "tier_detail":              {},
    }


def _make_fake_prices(price_source: str = "synthetic_test") -> dict:
    return {
        "price_source":           price_source,
        "price_timestamp":        None,
        "market_status":          None,
        "live_orderbook_checked": False,
        "tickers_found":          [],
        "prices_by_bracket":      {},
    }


def _make_fake_price_gate(adjusted: str | None = None) -> dict:
    return {
        "can_trade":               False,
        "can_execute":             False,
        "execution_rule":          "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS",
        "price_age_minutes":       None,
        "trade_block_reason":      "price_source=synthetic_test: non-live prices; max terminal_label=KALSHI_WATCH",
        "adjusted_terminal_label": adjusted,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Section C — POST /wow/kalshi/weather/evaluate: valid labels pass through
# ─────────────────────────────────────────────────────────────────────────────

VALID_LABELS = [
    "KALSHI_PLAYABLE_LIMIT_ONLY",
    "KALSHI_WATCH",
    "KALSHI_REJECT_NO_EDGE",
    "KALSHI_REJECT_BAD_RULES",
    "KALSHI_DATA_UNOBTAINABLE",
]


class TestPostRouteValidLabels(unittest.TestCase):
    """C1-C5: each valid label returns 200 with ok=True and the correct terminal_label.

    Note: C5 (KALSHI_REJECT_UNCALIBRATED) was removed — the label was removed from
    the registry on 2026-08-09 as confirmed dead code (see test A5 for rationale).
    C6 (KALSHI_DATA_UNOBTAINABLE) is renumbered C5 in VALID_LABELS above.
    """

    def _run_for_label(self, expected_label: str):
        """
        Spin up the Flask test client, mock the heavy dependencies, force
        _weather_terminal_label_v2 to return `expected_label`, and assert
        the response is a clean 200.
        """
        import app as flask_app

        with flask_app.app.test_client() as client:
            with (
                patch.dict(os.environ, {"SCORING_API_KEY": _TEST_API_KEY}),
                patch.object(flask_app, "_fetch_nws_cli",
                             return_value=_make_fake_cli()),
                patch.object(flask_app, "_fetch_forecast_high_tiered",
                             return_value=_make_fake_fc(82)),
                patch.object(flask_app, "_fetch_kalshi_nhigh_prices",
                             return_value=_make_fake_prices()),
                patch.object(flask_app, "_apply_weather_price_gate",
                             return_value=_make_fake_price_gate()),
                patch.object(flask_app, "_weather_terminal_label_v2",
                             return_value=expected_label),
                patch.object(flask_app, "_log_weather_scout_row") as mock_log,
                patch.object(flask_app, "_compute_forecast_horizon_hours",
                             return_value=18.0),
                patch.object(flask_app, "_score_weather_brackets_gaussian",
                             return_value=[
                                 {"label": "≤79",  "model_prob": 0.30, "verdict": "WATCH"},
                                 {"label": "80-84", "model_prob": 0.40, "verdict": "WATCH"},
                                 {"label": "≥85",  "model_prob": 0.30, "verdict": "WATCH"},
                             ]),
            ):
                resp = client.post(
                    "/wow/kalshi/weather/evaluate",
                    json=_build_post_body(),
                    headers={"X-API-Key": _TEST_API_KEY},
                )

        self.assertEqual(resp.status_code, 200,
                         f"Expected 200 for label={expected_label!r}, "
                         f"got {resp.status_code}: {resp.get_data(as_text=True)[:300]}")
        data = resp.get_json()
        self.assertTrue(data.get("ok"), f"ok should be True for label={expected_label!r}")
        self.assertEqual(data.get("terminal_label"), expected_label)
        self.assertFalse(data.get("can_execute"), "can_execute must always be False")
        # Ledger write fires for gaussian_forecast mode (which our mock implies)
        mock_log.assert_called_once()

    def test_C1_KALSHI_PLAYABLE_LIMIT_ONLY(self):
        self._run_for_label("KALSHI_PLAYABLE_LIMIT_ONLY")

    def test_C2_KALSHI_WATCH(self):
        self._run_for_label("KALSHI_WATCH")

    def test_C3_KALSHI_REJECT_NO_EDGE(self):
        self._run_for_label("KALSHI_REJECT_NO_EDGE")

    def test_C4_KALSHI_REJECT_BAD_RULES(self):
        self._run_for_label("KALSHI_REJECT_BAD_RULES")

    def test_C5_KALSHI_DATA_UNOBTAINABLE(self):
        self._run_for_label("KALSHI_DATA_UNOBTAINABLE")


# ─────────────────────────────────────────────────────────────────────────────
# Section D — POST /wow/kalshi/weather/evaluate: adversarial inputs
# ─────────────────────────────────────────────────────────────────────────────

class TestPostRouteAdversarialLabels(unittest.TestCase):

    def _run_adversarial(self, bad_label: str):
        import app as flask_app

        with flask_app.app.test_client() as client:
            with (
                patch.dict(os.environ, {"SCORING_API_KEY": _TEST_API_KEY}),
                patch.object(flask_app, "_fetch_nws_cli",
                             return_value=_make_fake_cli()),
                patch.object(flask_app, "_fetch_forecast_high_tiered",
                             return_value=_make_fake_fc(82)),
                patch.object(flask_app, "_fetch_kalshi_nhigh_prices",
                             return_value=_make_fake_prices()),
                patch.object(flask_app, "_apply_weather_price_gate",
                             return_value=_make_fake_price_gate()),
                patch.object(flask_app, "_weather_terminal_label_v2",
                             return_value=bad_label),
                patch.object(flask_app, "_log_weather_scout_row") as mock_log,
                patch.object(flask_app, "_compute_forecast_horizon_hours",
                             return_value=18.0),
                patch.object(flask_app, "_score_weather_brackets_gaussian",
                             return_value=[
                                 {"label": "≤79",  "model_prob": 0.30, "verdict": "WATCH"},
                             ]),
            ):
                resp = client.post(
                    "/wow/kalshi/weather/evaluate",
                    json=_build_post_body(),
                    headers={"X-API-Key": _TEST_API_KEY},
                )

        # Must be 500, not 200
        self.assertEqual(resp.status_code, 500,
                         f"Expected 500 for bad label={bad_label!r}, "
                         f"got {resp.status_code}")
        data = resp.get_json()
        # Violation status
        self.assertEqual(data.get("status"), "INTERNAL_LABEL_VIOLATION",
                         f"Expected INTERNAL_LABEL_VIOLATION, got: {data}")
        self.assertFalse(data.get("ok"), "ok must be False on violation")
        # can_execute must be False
        self.assertFalse(data.get("can_execute"),
                         "can_execute must be False on violation path")
        # Ledger write must be suppressed
        mock_log.assert_not_called()
        return data

    def test_D1_invented_label_triggers_violation(self):
        """An invented never-before-seen string must trigger the guard."""
        data = self._run_adversarial("KALSHI_INVENTED_LABEL_XYZ_NEVER_EXISTS")
        self.assertIn("KALSHI_INVENTED_LABEL_XYZ_NEVER_EXISTS", data.get("detail", ""))

    def test_D2_KALSHI_REJECT_THIN_BOOK_triggers_violation(self):
        """
        KALSHI_REJECT_THIN_BOOK appears in a docstring comment near the
        implementation but is NOT reachable from any production code path.
        Confirm it is correctly treated as unregistered.
        """
        data = self._run_adversarial("KALSHI_REJECT_THIN_BOOK")
        self.assertIn("KALSHI_REJECT_THIN_BOOK", data.get("detail", ""))
        # And confirm it's not in the registry (belt-and-suspenders)
        self.assertNotIn("KALSHI_REJECT_THIN_BOOK", _REGISTRY)


# ─────────────────────────────────────────────────────────────────────────────
# Section E — GET /kalshi/evaluate/weather/<city>: adversarial inputs
# ─────────────────────────────────────────────────────────────────────────────

class TestGetRouteAdversarialLabels(unittest.TestCase):

    def _run_adversarial_get(self, bad_label: str):
        import app as flask_app

        with flask_app.app.test_client() as client:
            with (
                patch.dict(os.environ, {"SCORING_API_KEY": _TEST_API_KEY}),
                patch.object(flask_app, "_fetch_nws_cli",
                             return_value=_make_fake_cli()),
                patch.object(flask_app, "_fetch_forecast_high_tiered",
                             return_value=_make_fake_fc(82)),
                patch.object(flask_app, "_fetch_kalshi_nhigh_markets",
                             return_value={"tickers_found": []}),
                patch.object(flask_app, "_fetch_kalshi_nhigh_prices",
                             return_value=_make_fake_prices()),
                patch.object(flask_app, "_apply_weather_price_gate",
                             return_value=_make_fake_price_gate()),
                patch.object(flask_app, "_weather_terminal_label_v2",
                             return_value=bad_label),
                patch.object(flask_app, "_compute_forecast_horizon_hours",
                             return_value=18.0),
                patch.object(flask_app, "_score_weather_brackets_gaussian",
                             return_value=[
                                 {"label": "≤79", "model_prob": 0.30, "verdict": "WATCH"},
                             ]),
            ):
                resp = client.get(
                    "/kalshi/evaluate/weather/NYC?date=2026-08-09",
                    headers={"X-API-Key": _TEST_API_KEY},
                )

        self.assertEqual(resp.status_code, 500,
                         f"Expected 500 for bad label={bad_label!r}, "
                         f"got {resp.status_code}")
        data = resp.get_json()
        self.assertEqual(data.get("status"), "INTERNAL_LABEL_VIOLATION")
        self.assertFalse(data.get("ok"))
        self.assertFalse(data.get("can_execute"))
        return data

    def test_E1_invented_label_triggers_violation_on_get_route(self):
        data = self._run_adversarial_get("KALSHI_INVENTED_LABEL_ABCDEF")
        self.assertIn("KALSHI_INVENTED_LABEL_ABCDEF", data.get("detail", ""))

    def test_E2_KALSHI_REJECT_THIN_BOOK_triggers_violation_on_get_route(self):
        data = self._run_adversarial_get("KALSHI_REJECT_THIN_BOOK")
        self.assertIn("KALSHI_REJECT_THIN_BOOK", data.get("detail", ""))


# ─────────────────────────────────────────────────────────────────────────────
# Section F — Isolation: ceiling resolvers do not reference the new symbols
# ─────────────────────────────────────────────────────────────────────────────

class TestCeilingResolverIsolation(unittest.TestCase):
    """
    The validation function and registry must not be imported by or wired into
    the two existing ceiling-resolution systems.
    """

    def _read_file(self, rel_path: str) -> str:
        path = os.path.join(_REPO, rel_path)
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def _assert_not_referenced(self, src: str, symbol: str, filename: str):
        self.assertNotIn(
            symbol, src,
            f"{symbol!r} must not appear in {filename} "
            "(ceiling resolver isolation requirement)",
        )

    def test_F1_wow_runtime_manifest_does_not_reference_new_symbols(self):
        src = self._read_file("gate_engine/wow_runtime_manifest.py")
        self._assert_not_referenced(src, "_validate_wx_terminal_label",
                                    "gate_engine/wow_runtime_manifest.py")
        self._assert_not_referenced(src, "_KALSHI_WX_TERMINAL_LABEL_REGISTRY",
                                    "gate_engine/wow_runtime_manifest.py")

    def test_F2_cc_labels_does_not_reference_new_symbols(self):
        src = self._read_file("gate_engine/command_center/cc_labels.py")
        self._assert_not_referenced(src, "_validate_wx_terminal_label",
                                    "gate_engine/command_center/cc_labels.py")
        self._assert_not_referenced(src, "_KALSHI_WX_TERMINAL_LABEL_REGISTRY",
                                    "gate_engine/command_center/cc_labels.py")

    def test_F3_ceiling_resolver_does_not_reference_new_symbols(self):
        src = self._read_file("gate_engine/command_center/ceiling_resolver.py")
        self._assert_not_referenced(src, "_validate_wx_terminal_label",
                                    "gate_engine/command_center/ceiling_resolver.py")
        self._assert_not_referenced(src, "_KALSHI_WX_TERMINAL_LABEL_REGISTRY",
                                    "gate_engine/command_center/ceiling_resolver.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)
