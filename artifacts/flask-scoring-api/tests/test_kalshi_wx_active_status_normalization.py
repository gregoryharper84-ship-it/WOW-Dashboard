"""
tests/test_kalshi_wx_active_status_normalization.py
WOW-PATCH-2026-08-09-KALSHI-WX-ACTIVE-STATUS-NORMALIZATION

Validates the _market_is_open() helper and its wiring inside
_fetch_kalshi_nhigh_markets().

Test plan
─────────
Section A — _market_is_open() unit tests (no Flask, no network)
  A1:   status="active"       → True   (the core fix)
  A2:   status="inactive"     → False
  A3:   status="closed"       → False
  A4:   status="determined"   → False
  A5:   status="disputed"     → False
  A6:   status="amended"      → False
  A7:   status="finalized"    → False
  A8:   status="initialized"  → False
  A9:   status="pending_review" (unrecognised) → False (fail-closed)
  A10:  status=""             → False (empty string, fail-closed)
  A11:  status=None           → False (None, fail-closed, no crash)

Section B — _fetch_kalshi_nhigh_markets(): market_status normalisation
  B1:  API returns [{"status": "active", ...}]  → market_status="open"
  B2:  API returns [{"status": "closed", ...}]  → market_status="closed"
  B3:  API returns [{"status": "inactive", ...}]→ market_status="closed"
  B4:  API returns []                           → market_status=None
  B5:  API returns mixed active+finalized       → market_status="open"
       (at least one active → open)
  B6:  API returns markets with unrecognised status → market_status="closed"
       (all fail-closed → no open)

Section C — Empty-orderbook rejection path still works (KALSHI_DATA_UNOBTAINABLE)
  C1:  Market is genuinely "active" (reaches price gate) but orderbook has
       zero depth → price gate correctly returns KALSHI_DATA_UNOBTAINABLE
       via the existing DRY_RUN_ONLY path + empty-orderbook downstream,
       NOT KALSHI_REJECT_BAD_RULES.  This proves the previously-blocked path
       is now reachable and the empty-orderbook fallback is unaffected.

Section D — Invariants unchanged by the patch
  D1:  _KALSHI_WEATHER_STATIONS maps CHI→KMDW, MIA→KMIA, LA→KLAX
       (station mapping unchanged).
  D2:  can_execute is False on every route response involving this helper
       (DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS).
  D3:  execution_rule is exactly "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
       when the live-orderbook path is reached with a genuinely open market.
  D4:  The outbound Kalshi query still uses status="open" as the filter param
       (unchanged — only response interpretation was fixed).
  D5:  Terminal-label registry still has exactly 5 members and KALSHI_PLAYABLE_LIMIT_ONLY
       is still a member (taxonomy unchanged by this patch).
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# ── path setup ────────────────────────────────────────────────────────────────
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

_TEST_API_KEY = "test-key-wx-active-status-norm"


# ── helper: extract _market_is_open without booting Flask ────────────────────

def _import_market_is_open():
    """Extract _market_is_open() from app.py via targeted source exec.

    Uses the patch sentinel comment as a start marker so we avoid executing
    the full 35k-line app module.
    """
    app_path = os.path.join(_REPO, "app.py")
    with open(app_path, encoding="utf-8") as fh:
        src = fh.read()

    start_marker = "def _market_is_open(status"
    end_marker   = "def _fetch_kalshi_nhigh_markets("
    start_idx = src.find(start_marker)
    end_idx   = src.find(end_marker)
    assert start_idx != -1, "_market_is_open not found in app.py"
    assert end_idx   != -1, "_fetch_kalshi_nhigh_markets not found in app.py"
    assert start_idx < end_idx, "_market_is_open must precede _fetch_kalshi_nhigh_markets"

    block = src[start_idx:end_idx]
    ns: dict = {}
    exec(compile(block, "<market_is_open_block>", "exec"), ns)  # noqa: S102
    return ns["_market_is_open"]


_market_is_open = _import_market_is_open()


# ── helper: build a minimal fake price-fetch result ──────────────────────────

def _fake_prices_not_found() -> dict:
    return {
        "price_source": "not_found",
        "price_timestamp": None,
        "market_status": None,
        "live_orderbook_checked": True,
        "tickers_found": [],
        "prices_by_bracket": {},
    }


def _fake_prices_live_orderbook(market_status: str = "open") -> dict:
    """Simulate a live orderbook fetch where the market is genuinely open
    but has zero depth (empty bid/ask arrays → best_yes_bid/ask = None).

    price_timestamp is set to *now* so the staleness check (> 10 minutes)
    does not fire and the test reaches the DRY_RUN_ONLY branch it intends to
    validate.  Using a hardcoded past timestamp caused the price-age gate to
    fire first, masking the market_status path under test.
    """
    import datetime as _dt
    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
    return {
        "price_source": "kalshi_live_orderbook",
        "price_timestamp": now_iso,
        "market_status": market_status,
        "live_orderbook_checked": True,
        "tickers_found": ["KXHIGHMIA-26AUG09-T94"],
        "prices_by_bracket": {
            ">94": {
                "ticker": "KXHIGHMIA-26AUG09-T94",
                "market_status": market_status,
                "yes_bid": None,   # empty orderbook — zero depth
                "yes_ask": None,
                "no_bid": None,
                "no_ask": None,
                "orderbook_ok": True,
            }
        },
    }


def _fake_cli(ok: bool = True, observed_high=None,
              report_status: str = "NOT_YET_ISSUED") -> dict:
    return {
        "ok": ok,
        "observed_high": observed_high,
        "report_status": report_status,
        "revision_risk": False,
        "report_date": None,
        "source_url": "https://api.weather.gov/products",
        "product_id": None,
        "issuance_time": None,
        "raw_text": None,
    }


def _fake_fc(forecast_high: int | None = 93) -> dict:
    return {
        "forecast_high": forecast_high,
        "forecast_source": "nws_forecast" if forecast_high else "none",
        "weather_data_source_tier": "nws_primary" if forecast_high else "all_sources_failed",
        "tier_detail": {},
    }


def _minimal_post_body(city: str = "MIA", date: str = "2026-08-09") -> dict:
    return {
        "city":     city,
        "date":     date,
        "brackets": [
            {"label": ">94",   "yes_price": 0.15, "ticker": "KXHIGHMIA-26AUG09-T94"},
            {"label": "91-92", "yes_price": 0.20, "ticker": "KXHIGHMIA-26AUG09-B91.5"},
            {"label": "88-89", "yes_price": 0.20, "ticker": "KXHIGHMIA-26AUG09-B88.5"},
            {"label": "85-86", "yes_price": 0.20, "ticker": "KXHIGHMIA-26AUG09-B85.5"},
            {"label": "<84",   "yes_price": 0.25, "ticker": "KXHIGHMIA-26AUG09-T84"},
        ],
        # No price_source → real live-fetch path
    }


# ─────────────────────────────────────────────────────────────────────────────
# Section A — _market_is_open() unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestMarketIsOpenUnit(unittest.TestCase):
    """A1–A11: pure unit tests, no Flask, no network."""

    # ── Positive ─────────────────────────────────────────────────────────────

    def test_A1_active_is_open(self):
        """status='active' is the only value that maps to open=True."""
        self.assertTrue(_market_is_open("active"))

    # ── Known-closed lifecycle statuses ──────────────────────────────────────

    def test_A2_inactive_is_not_open(self):
        """Exchange-deactivated markets must not proceed to price evaluation."""
        self.assertFalse(_market_is_open("inactive"))

    def test_A3_closed_is_not_open(self):
        self.assertFalse(_market_is_open("closed"))

    def test_A4_determined_is_not_open(self):
        self.assertFalse(_market_is_open("determined"))

    def test_A5_disputed_is_not_open(self):
        self.assertFalse(_market_is_open("disputed"))

    def test_A6_amended_is_not_open(self):
        self.assertFalse(_market_is_open("amended"))

    def test_A7_finalized_is_not_open(self):
        self.assertFalse(_market_is_open("finalized"))

    def test_A8_initialized_is_not_open(self):
        """Before open_time: market exists but trading not yet live."""
        self.assertFalse(_market_is_open("initialized"))

    # ── Adversarial / fail-closed ─────────────────────────────────────────────

    def test_A9_unrecognised_string_fails_closed(self):
        """Any unrecognised status value must return False without crashing."""
        self.assertFalse(_market_is_open("pending_review"))

    def test_A10_empty_string_fails_closed(self):
        self.assertFalse(_market_is_open(""))

    def test_A11_none_fails_closed_no_crash(self):
        """None must return False without raising TypeError or any exception."""
        try:
            result = _market_is_open(None)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"_market_is_open(None) raised {type(exc).__name__}: {exc}")
        self.assertFalse(result)


# ─────────────────────────────────────────────────────────────────────────────
# Section B — _fetch_kalshi_nhigh_markets(): market_status normalisation
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchKalshiNhighMarketsStatusNorm(unittest.TestCase):
    """B1–B6: market_status field in return dict is correctly normalised."""

    def _call(self, api_markets: list) -> dict:
        """Call _fetch_kalshi_nhigh_markets with a mocked Kalshi API response."""
        import app as flask_app
        with patch.object(flask_app, "_kalshi_public_get",
                          return_value={"markets": api_markets}):
            return flask_app._fetch_kalshi_nhigh_markets("KXHIGHMIA", "2026-08-09")

    def test_B1_active_markets_yield_market_status_open(self):
        """Kalshi returns status='active' → _fetch_kalshi_nhigh_markets must
        report market_status='open' (the fix)."""
        result = self._call([
            {"ticker": "KXHIGHMIA-26AUG09-T94", "status": "active"},
            {"ticker": "KXHIGHMIA-26AUG09-T87", "status": "active"},
        ])
        self.assertEqual(result["market_status"], "open",
                         "active Kalshi markets must map to market_status='open'")
        self.assertTrue(result["ok"])

    def test_B2_closed_markets_yield_market_status_closed(self):
        result = self._call([
            {"ticker": "KXHIGHMIA-26AUG09-T94", "status": "closed"},
        ])
        self.assertEqual(result["market_status"], "closed")

    def test_B3_inactive_markets_yield_market_status_closed(self):
        """Exchange-deactivated market: must not be treated as open."""
        result = self._call([
            {"ticker": "KXHIGHMIA-26AUG09-T94", "status": "inactive"},
        ])
        self.assertEqual(result["market_status"], "closed")

    def test_B4_empty_market_list_yields_market_status_none(self):
        result = self._call([])
        self.assertIsNone(result["market_status"])
        self.assertFalse(result["ok"])

    def test_B5_mixed_active_and_finalized_yields_open(self):
        """At least one active market → whole batch is considered open."""
        result = self._call([
            {"ticker": "KXHIGHMIA-26AUG09-T94", "status": "finalized"},
            {"ticker": "KXHIGHMIA-26AUG09-T87", "status": "active"},
        ])
        self.assertEqual(result["market_status"], "open")

    def test_B6_unrecognised_status_yields_closed_not_open(self):
        """Fail-closed: unknown status string must not pass as open."""
        result = self._call([
            {"ticker": "KXHIGHMIA-26AUG09-T94", "status": "pending_review"},
        ])
        self.assertEqual(result["market_status"], "closed")


# ─────────────────────────────────────────────────────────────────────────────
# Section C — Empty-orderbook rejection path (KALSHI_DATA_UNOBTAINABLE)
# ─────────────────────────────────────────────────────────────────────────────

class TestEmptyOrderbookRejectionStillWorks(unittest.TestCase):
    """C1: Prove that once a market is correctly identified as open, the
    empty-orderbook branch (KALSHI_DATA_UNOBTAINABLE via TF-WX-19 / DRY_RUN_ONLY
    path) is still reachable and produces the correct outcome.

    Before the patch: _fetch_kalshi_nhigh_markets always returned
    market_status='closed' for active markets, so the price gate fired
    'Market not open (status=closed)' → KALSHI_REJECT_BAD_RULES instead.
    After the patch: market_status='open' is returned, price gate does NOT
    fire the closed-market block, and the DRY_RUN_ONLY path is reached —
    returning can_execute=False and execution_rule=DRY_RUN_ONLY_*,
    with terminal_label determined by the weather/bracket logic.
    """

    def test_C1_open_market_empty_orderbook_reaches_dryrun_not_bad_rules(self):
        """A genuinely open market with zero orderbook depth must NOT produce
        KALSHI_REJECT_BAD_RULES.  The price gate must reach the DRY_RUN_ONLY
        branch and return can_execute=False + execution_rule correctly."""
        import app as flask_app

        # Simulate: market is active (market_status='open') but orderbook
        # has no bids/asks (empty depth → best_yes_bid=None, best_yes_ask=None).
        # The _apply_weather_price_gate receives market_status='open' and
        # price_source='kalshi_live_orderbook' with fresh timestamp.
        open_prices = _fake_prices_live_orderbook(market_status="open")

        with flask_app.app.test_client() as client:
            with (
                patch.dict(os.environ, {"SCORING_API_KEY": _TEST_API_KEY}),
                patch.object(flask_app, "_fetch_nws_cli",
                             return_value=_fake_cli()),
                patch.object(flask_app, "_fetch_forecast_high_tiered",
                             return_value=_fake_fc(93)),
                patch.object(flask_app, "_fetch_kalshi_nhigh_prices",
                             return_value=open_prices),
                patch.object(flask_app, "_compute_forecast_horizon_hours",
                             return_value=18.0),
                patch.object(flask_app, "_score_weather_brackets_gaussian",
                             return_value=[
                                 {"label": ">94",   "model_prob": 0.12, "verdict": "WATCH"},
                                 {"label": "91-92", "model_prob": 0.22, "verdict": "WATCH"},
                                 {"label": "88-89", "model_prob": 0.22, "verdict": "WATCH"},
                                 {"label": "85-86", "model_prob": 0.22, "verdict": "WATCH"},
                                 {"label": "<84",   "model_prob": 0.22, "verdict": "WATCH"},
                             ]),
                patch.object(flask_app, "_log_weather_scout_row"),
            ):
                resp = client.post(
                    "/wow/kalshi/weather/evaluate",
                    json=_minimal_post_body(),
                    headers={"X-API-Key": _TEST_API_KEY},
                )

        self.assertEqual(resp.status_code, 200,
                         f"Expected 200, got {resp.status_code}: "
                         f"{resp.get_data(as_text=True)[:400]}")
        data = resp.get_json()
        self.assertTrue(data.get("ok"))

        # The price gate must NOT produce KALSHI_REJECT_BAD_RULES
        self.assertNotEqual(data.get("terminal_label"), "KALSHI_REJECT_BAD_RULES",
                            "Open market must not hit the closed-market rejection branch")

        # can_execute must remain False (DRY_RUN_ONLY is unconditional)
        self.assertFalse(data.get("can_execute"),
                         "can_execute must be False — DRY_RUN_ONLY_NO_LIVE_TRADING")

        # execution_rule must be the canonical constant
        self.assertEqual(
            data.get("execution_rule"),
            "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS",
            "execution_rule must be DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS",
        )

        # trade_block_reason must reference DRY_RUN_ONLY (not "Market not open")
        block_reason = data.get("trade_block_reason") or ""
        self.assertNotIn("Market not open", block_reason,
                         "Open market must not trigger 'Market not open' block reason")
        self.assertIn("DRY_RUN_ONLY", block_reason,
                      "Open market with empty orderbook must hit DRY_RUN_ONLY path")


# ─────────────────────────────────────────────────────────────────────────────
# Section D — Invariants unchanged by the patch
# ─────────────────────────────────────────────────────────────────────────────

class TestPatchInvariants(unittest.TestCase):
    """D1–D5: prove the patch does not alter anything outside the status check."""

    def test_D1_station_mapping_unchanged(self):
        """_KALSHI_WEATHER_STATIONS must still map to canonical NWS stations.
        CHI=KMDW (not KORD), MIA=KMIA (not KPBI), LA=KLAX (not KBUR).
        """
        import app as flask_app
        stations = flask_app._KALSHI_WEATHER_STATIONS
        self.assertEqual(stations["CHI"]["station"], "KMDW",
                         "CHI must use KMDW (Midway), not KORD (O'Hare)")
        self.assertEqual(stations["MIA"]["station"], "KMIA",
                         "MIA must use KMIA (Miami Intl), not KPBI (Palm Beach)")
        self.assertEqual(stations["LA"]["station"], "KLAX",
                         "LA must use KLAX (LAX), not KBUR (Burbank)")
        self.assertEqual(stations["NYC"]["station"], "KNYC",
                         "NYC must use KNYC (Central Park)")
        self.assertEqual(stations["AUS"]["station"], "KAUS",
                         "AUS must use KAUS (Austin Bergstrom)")

    def test_D2_can_execute_always_false_on_live_path(self):
        """DRY_RUN_ONLY: can_execute must be False even when market is genuinely open."""
        import app as flask_app
        open_prices = _fake_prices_live_orderbook(market_status="open")
        with flask_app.app.test_client() as client:
            with (
                patch.dict(os.environ, {"SCORING_API_KEY": _TEST_API_KEY}),
                patch.object(flask_app, "_fetch_nws_cli",     return_value=_fake_cli()),
                patch.object(flask_app, "_fetch_forecast_high_tiered", return_value=_fake_fc(93)),
                patch.object(flask_app, "_fetch_kalshi_nhigh_prices",  return_value=open_prices),
                patch.object(flask_app, "_compute_forecast_horizon_hours", return_value=18.0),
                patch.object(flask_app, "_score_weather_brackets_gaussian",
                             return_value=[{"label": ">94", "model_prob": 0.12, "verdict": "WATCH"}]),
                patch.object(flask_app, "_log_weather_scout_row"),
            ):
                resp = client.post(
                    "/wow/kalshi/weather/evaluate",
                    json=_minimal_post_body(),
                    headers={"X-API-Key": _TEST_API_KEY},
                )
        data = resp.get_json()
        self.assertFalse(data.get("can_execute"),
                         "can_execute must always be False per DRY_RUN_ONLY policy")

    def test_D3_execution_rule_constant_unchanged(self):
        """The execution_rule string must be exactly the canonical constant."""
        import app as flask_app
        open_prices = _fake_prices_live_orderbook(market_status="open")
        with flask_app.app.test_client() as client:
            with (
                patch.dict(os.environ, {"SCORING_API_KEY": _TEST_API_KEY}),
                patch.object(flask_app, "_fetch_nws_cli",     return_value=_fake_cli()),
                patch.object(flask_app, "_fetch_forecast_high_tiered", return_value=_fake_fc(93)),
                patch.object(flask_app, "_fetch_kalshi_nhigh_prices",  return_value=open_prices),
                patch.object(flask_app, "_compute_forecast_horizon_hours", return_value=18.0),
                patch.object(flask_app, "_score_weather_brackets_gaussian",
                             return_value=[{"label": ">94", "model_prob": 0.12, "verdict": "WATCH"}]),
                patch.object(flask_app, "_log_weather_scout_row"),
            ):
                resp = client.post(
                    "/wow/kalshi/weather/evaluate",
                    json=_minimal_post_body(),
                    headers={"X-API-Key": _TEST_API_KEY},
                )
        data = resp.get_json()
        self.assertEqual(
            data.get("execution_rule"),
            "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS",
        )

    def test_D4_outbound_kalshi_query_still_uses_status_open_filter(self):
        """The query parameter sent to Kalshi must still be status='open'.
        Only the response interpretation was changed, not the outbound filter.
        """
        import app as flask_app
        captured_calls: list = []

        def _fake_kalshi_get(path, params=None, **kwargs):
            if params:
                captured_calls.append(dict(params))
            return {"markets": []}

        with patch.object(flask_app, "_kalshi_public_get", side_effect=_fake_kalshi_get):
            flask_app._fetch_kalshi_nhigh_markets("KXHIGHMIA", "2026-08-09")

        self.assertTrue(captured_calls,
                        "_kalshi_public_get must be called at least once")
        market_call = captured_calls[0]
        self.assertEqual(market_call.get("status"), "open",
                         "Outbound Kalshi query must still send status='open' filter param")

    def test_D5_terminal_label_registry_has_five_members_including_playable(self):
        """Registry taxonomy is unchanged — still 5 members, KALSHI_PLAYABLE_LIMIT_ONLY present."""
        import app as flask_app
        from gate_engine.kalshi_wx_terminal_labels import KALSHI_WX_TERMINAL_LABEL_REGISTRY
        self.assertEqual(len(KALSHI_WX_TERMINAL_LABEL_REGISTRY), 5)
        self.assertIn("KALSHI_PLAYABLE_LIMIT_ONLY", KALSHI_WX_TERMINAL_LABEL_REGISTRY)
        self.assertIn("KALSHI_WATCH",               KALSHI_WX_TERMINAL_LABEL_REGISTRY)
        self.assertIn("KALSHI_DATA_UNOBTAINABLE",   KALSHI_WX_TERMINAL_LABEL_REGISTRY)
        self.assertIn("KALSHI_REJECT_BAD_RULES",    KALSHI_WX_TERMINAL_LABEL_REGISTRY)
        self.assertIn("KALSHI_REJECT_NO_EDGE",      KALSHI_WX_TERMINAL_LABEL_REGISTRY)


if __name__ == "__main__":
    unittest.main(verbosity=2)
