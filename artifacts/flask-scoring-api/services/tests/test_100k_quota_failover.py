"""
test_100k_quota_failover.py — Regression tests for ODDS_API_KEY_100K as
automatic quota failover, including proactive skip and isolated endpoint fixes.

Verified behaviors
------------------
T1  _odds_api_request proactive skip: when paid tier has remaining==0 in
    store, the 100K (high) key is used without an HTTP attempt against paid.
T2  _odds_api_request reactive fallback: when paid returns 429, high key
    is tried automatically.
T3  _odds_api_request: 'high' tier quota is tracked in _ODDS_QUOTA_STORE
    when the 100K key is the one that succeeds.
T4  _odds_api_request: all three keys exhausted/failing → 502 error.
T5  _get() proactive skip: when pg_odds_quota reports tier exhausted
    (remaining==0), that tier is skipped without an HTTP call.
T6  _get() reactive fallback: 429 from paid → high key tried.
T7  resolve_odds_api_key_with_source() returns 100K key when paid is absent.
T8  _resolve_key() returns 100K key when paid is absent.
T9  No key configured → NOT_CALLED (not an exception).
T10 _ODDS_QUOTA_STORE 'high' tier updated correctly (same semantics as
    'paid' and 'free').
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Isolate import — avoid full Flask startup side-effects
# ---------------------------------------------------------------------------

def _get_app_symbols():
    if "app" in sys.modules:
        mod = sys.modules["app"]
    else:
        import app as mod  # noqa: F401
    with mod._ODDS_QUOTA_LOCK:
        mod._ODDS_QUOTA_STORE.clear()
    return mod

from services import odds_api as _odds_mod


# ---------------------------------------------------------------------------
# T1 — _odds_api_request proactive skip when paid quota is zero
# ---------------------------------------------------------------------------

class TestProactiveSkipInOddsApiRequest(unittest.TestCase):

    def _build_mock_response(self, status_code=200, json_data=None):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_data or {"events": []}
        resp.headers = {
            "x-requests-remaining": "5000",
            "x-requests-used":      "100",
            "x-requests-last":      "1",
        }
        resp.text = ""
        return resp

    def test_t1_proactive_skip_uses_100k_when_paid_exhausted(self):
        """
        When _ODDS_QUOTA_STORE['paid']['requests_remaining'] == 0,
        the HTTP call for the paid key must be skipped. The 100K (high)
        key is used without any HTTP attempt against the paid key.
        """
        mod = _get_app_symbols()

        # Pre-seed paid tier as exhausted in the in-process store
        with mod._ODDS_QUOTA_LOCK:
            mod._ODDS_QUOTA_STORE["paid"] = {
                "requests_remaining": 0,
                "requests_used":      10000,
                "quota_warning":      True,
                "updated_at":         "2026-08-17T10:00:00Z",
            }

        mock_resp = self._build_mock_response(200, {"events": ["game1"]})

        import requests as _req
        with patch.dict(os.environ, {
            "ODDS_API_PAID_KEY": "paid-key-abc",
            "ODDS_API_KEY_100K": "100k-key-xyz",
            "ODDS_API_FREE_KEY": "",
            "ODDS_API_KEY":      "",
        }), patch.object(_req, "get", return_value=mock_resp) as mock_get:
            result, err = mod._odds_api_request("sports/basketball_nba/odds", {})

        self.assertIsNotNone(result)
        self.assertIsNone(err)
        self.assertEqual(result["source_key_tier"], "high")

        # requests.get must have been called exactly once, with the 100K key
        self.assertEqual(mock_get.call_count, 1)
        call_params = mock_get.call_args[1]["params"]
        self.assertEqual(call_params["apiKey"], "100k-key-xyz")

    def test_t1b_proactive_skip_not_triggered_when_remaining_is_none(self):
        """remaining=None means 'unknown, not zero' — must not proactively skip."""
        mod = _get_app_symbols()

        with mod._ODDS_QUOTA_LOCK:
            mod._ODDS_QUOTA_STORE["paid"] = {
                "requests_remaining": None,
                "requests_used":      100,
                "quota_warning":      False,
                "updated_at":         "2026-08-17T10:00:00Z",
            }

        mock_resp = self._build_mock_response(200, {"events": []})
        import requests as _req
        with patch.dict(os.environ, {
            "ODDS_API_PAID_KEY": "paid-key-abc",
            "ODDS_API_KEY_100K": "100k-key-xyz",
            "ODDS_API_FREE_KEY": "",
            "ODDS_API_KEY":      "",
        }), patch.object(_req, "get", return_value=mock_resp) as mock_get:
            result, err = mod._odds_api_request("sports/basketball_nba/odds", {})

        self.assertIsNotNone(result)
        # Paid key was tried first (remaining=None → no proactive skip)
        self.assertEqual(result["source_key_tier"], "paid")
        call_params = mock_get.call_args[1]["params"]
        self.assertEqual(call_params["apiKey"], "paid-key-abc")


# ---------------------------------------------------------------------------
# T2 — _odds_api_request reactive fallback on 429
# ---------------------------------------------------------------------------

class TestReactiveFallbackIn_OddsApiRequest(unittest.TestCase):

    def test_t2_reactive_fallback_paid_429_tries_high(self):
        """
        When the paid key returns 429, _odds_api_request must try the 100K
        (high) key next.
        """
        mod = _get_app_symbols()

        paid_resp = MagicMock()
        paid_resp.status_code = 429
        paid_resp.text = "quota exceeded"

        high_resp = MagicMock()
        high_resp.status_code = 200
        high_resp.json.return_value = {"events": ["game1"]}
        high_resp.headers = {
            "x-requests-remaining": "95000",
            "x-requests-used":      "5000",
            "x-requests-last":      "1",
        }

        import requests as _req
        with patch.dict(os.environ, {
            "ODDS_API_PAID_KEY": "paid-key",
            "ODDS_API_KEY_100K": "100k-key",
            "ODDS_API_FREE_KEY": "",
            "ODDS_API_KEY":      "",
        }), patch.object(_req, "get", side_effect=[paid_resp, high_resp]) as mock_get:
            result, err = mod._odds_api_request("sports/baseball_mlb/odds", {})

        self.assertIsNotNone(result)
        self.assertIsNone(err)
        self.assertEqual(result["source_key_tier"], "high")
        self.assertEqual(mock_get.call_count, 2)


# ---------------------------------------------------------------------------
# T3 — 'high' tier tracked in _ODDS_QUOTA_STORE when 100K key succeeds
# ---------------------------------------------------------------------------

class TestHighTierQuotaTracking(unittest.TestCase):

    def test_t3_high_tier_quota_written_to_store(self):
        """
        When _odds_api_request succeeds with the 100K (high) key, the
        quota headers must be tracked under the 'high' tier in _ODDS_QUOTA_STORE.
        """
        mod = _get_app_symbols()

        paid_resp = MagicMock()
        paid_resp.status_code = 429
        paid_resp.text = ""

        high_resp = MagicMock()
        high_resp.status_code = 200
        high_resp.json.return_value = {}
        high_resp.headers = {
            "x-requests-remaining": "87500",
            "x-requests-used":      "12500",
            "x-requests-last":      "2",
        }

        import requests as _req
        with patch.dict(os.environ, {
            "ODDS_API_PAID_KEY": "paid-key",
            "ODDS_API_KEY_100K": "100k-key",
            "ODDS_API_FREE_KEY": "",
            "ODDS_API_KEY":      "",
        }), patch.object(_req, "get", side_effect=[paid_resp, high_resp]):
            result, _ = mod._odds_api_request("sports/basketball_wnba/odds", {})

        with mod._ODDS_QUOTA_LOCK:
            high_state = mod._ODDS_QUOTA_STORE.get("high", {})

        self.assertEqual(high_state.get("requests_remaining"), 87500)
        self.assertEqual(high_state.get("requests_used"), 12500)
        self.assertFalse(high_state.get("quota_warning"))  # 87500 > threshold


# ---------------------------------------------------------------------------
# T4 — All keys exhausted → 502
# ---------------------------------------------------------------------------

class TestAllKeysExhausted(unittest.TestCase):

    def test_t4_all_keys_fail_returns_502(self):
        """When all keys return 429, _odds_api_request returns (None, flask_error_tuple)."""
        mod = _get_app_symbols()

        fail_resp = MagicMock()
        fail_resp.status_code = 429
        fail_resp.text = "quota exceeded"

        import requests as _req
        # _odds_api_request calls jsonify() internally, which requires Flask app context.
        with mod.app.app_context(), patch.dict(os.environ, {
            "ODDS_API_PAID_KEY": "paid-key",
            "ODDS_API_KEY_100K": "100k-key",
            "ODDS_API_FREE_KEY": "free-key",
            "ODDS_API_KEY":      "",
        }), patch.object(_req, "get", return_value=fail_resp):
            result, err_resp = mod._odds_api_request("sports/baseball_mlb/odds", {})

        self.assertIsNone(result)
        self.assertIsNotNone(err_resp)


# ---------------------------------------------------------------------------
# T5 — _get() proactive skip via pg_odds_quota snapshot
# ---------------------------------------------------------------------------

class TestGetProactiveSkip(unittest.TestCase):

    def test_t5_proactive_skip_in_get_when_quota_exhausted(self):
        """
        When pg_odds_quota.fetch_quota_snapshot() reports 'paid' remaining==0,
        _get() must skip the paid key without an HTTP call and use the 100K key.
        """
        quota_snapshot = {
            "paid": {
                "requests_remaining": 0,
                "quota_warning":      True,
                "source":             "postgres_cross_worker",
            }
        }

        high_resp = MagicMock()
        high_resp.status_code = 200
        high_resp.json.return_value = [{"id": "event1"}]
        high_resp.headers = {"x-requests-remaining": "90000"}

        import requests as _rq
        with patch.dict(os.environ, {
            "ODDS_API_PAID_KEY": "paid-key",
            "ODDS_API_KEY_100K": "100k-key",
            "ODDS_API_FREE_KEY": "",
            "ODDS_API_KEY":      "",
        }), patch(
            "gate_engine.pg_odds_quota.fetch_quota_snapshot",
            return_value=quota_snapshot,
        ), patch.object(_rq, "get", return_value=high_resp) as mock_get:
            data, status = _odds_mod._get("/sports/basketball_wnba/odds")

        self.assertIsNotNone(data)
        self.assertIn("AVAILABLE", status)
        self.assertEqual(mock_get.call_count, 1)
        call_params = mock_get.call_args[1]["params"]
        self.assertEqual(call_params["apiKey"], "100k-key")

    def test_t5b_get_not_skipped_when_remaining_none(self):
        """remaining=None in pg_odds_quota → no proactive skip."""
        quota_snapshot = {
            "paid": {
                "requests_remaining": None,
                "quota_warning":      False,
                "source":             "postgres_cross_worker",
            }
        }

        paid_resp = MagicMock()
        paid_resp.status_code = 200
        paid_resp.json.return_value = [{"id": "event2"}]
        paid_resp.headers = {"x-requests-remaining": "500"}

        import requests as _rq
        with patch.dict(os.environ, {
            "ODDS_API_PAID_KEY": "paid-key",
            "ODDS_API_KEY_100K": "100k-key",
            "ODDS_API_FREE_KEY": "",
            "ODDS_API_KEY":      "",
        }), patch(
            "gate_engine.pg_odds_quota.fetch_quota_snapshot",
            return_value=quota_snapshot,
        ), patch.object(_rq, "get", return_value=paid_resp) as mock_get:
            data, status = _odds_mod._get("/sports/basketball_nba/odds")

        self.assertIsNotNone(data)
        call_params = mock_get.call_args[1]["params"]
        self.assertEqual(call_params["apiKey"], "paid-key")

    def test_t5c_pg_error_fails_open_and_proceeds_normally(self):
        """pg_odds_quota failure → fail-open; _get() proceeds without proactive skip."""
        paid_resp = MagicMock()
        paid_resp.status_code = 200
        paid_resp.json.return_value = [{"id": "event3"}]
        paid_resp.headers = {"x-requests-remaining": "200"}

        import requests as _rq
        with patch.dict(os.environ, {
            "ODDS_API_PAID_KEY": "paid-key",
            "ODDS_API_KEY_100K": "100k-key",
            "ODDS_API_FREE_KEY": "",
            "ODDS_API_KEY":      "",
        }), patch(
            "gate_engine.pg_odds_quota.fetch_quota_snapshot",
            side_effect=RuntimeError("DB down"),
        ), patch.object(_rq, "get", return_value=paid_resp) as mock_get:
            data, status = _odds_mod._get("/sports/baseball_mlb/odds")

        self.assertIsNotNone(data)
        self.assertIn("AVAILABLE", status)
        # paid key was used (fail-open — no proactive skip when DB errors)
        call_params = mock_get.call_args[1]["params"]
        self.assertEqual(call_params["apiKey"], "paid-key")


# ---------------------------------------------------------------------------
# T6 — _get() reactive fallback on 429
# ---------------------------------------------------------------------------

class TestGetReactiveFallback(unittest.TestCase):

    def test_t6_reactive_fallback_on_429(self):
        """_get(): paid returns 429 → 100K key tried."""
        paid_resp = MagicMock()
        paid_resp.status_code = 429
        paid_resp.text = ""

        high_resp = MagicMock()
        high_resp.status_code = 200
        high_resp.json.return_value = [{"id": "event_h"}]
        high_resp.headers = {"x-requests-remaining": "80000"}

        import requests as _rq
        with patch.dict(os.environ, {
            "ODDS_API_PAID_KEY": "paid-key",
            "ODDS_API_KEY_100K": "100k-key",
            "ODDS_API_FREE_KEY": "",
            "ODDS_API_KEY":      "",
        }), patch(
            "gate_engine.pg_odds_quota.fetch_quota_snapshot",
            return_value={},  # no pre-seed
        ), patch.object(_rq, "get", side_effect=[paid_resp, high_resp]) as mock_get:
            data, status = _odds_mod._get("/sports/basketball_nba/odds")

        self.assertIsNotNone(data)
        self.assertIn("AVAILABLE", status)
        self.assertEqual(mock_get.call_count, 2)
        # Second call was with 100K key
        second_call_params = mock_get.call_args_list[1][1]["params"]
        self.assertEqual(second_call_params["apiKey"], "100k-key")


# ---------------------------------------------------------------------------
# T7 / T8 — resolve helpers use 100K key when paid absent
# ---------------------------------------------------------------------------

class TestKeyResolutionLadder(unittest.TestCase):

    def test_t7_resolve_with_source_uses_100k_when_paid_absent(self):
        with patch.dict(os.environ, {
            "ODDS_API_PAID_KEY": "",
            "ODDS_API_KEY_100K": "100k-key-xyz",
            "ODDS_API_FREE_KEY": "",
            "ODDS_API_KEY":      "",
        }):
            key, src = _odds_mod.resolve_odds_api_key_with_source()
        self.assertEqual(key, "100k-key-xyz")
        self.assertEqual(src, "ODDS_API_KEY_100K")

    def test_t7b_resolve_with_source_prefers_paid_over_100k(self):
        with patch.dict(os.environ, {
            "ODDS_API_PAID_KEY": "paid-key",
            "ODDS_API_KEY_100K": "100k-key",
            "ODDS_API_FREE_KEY": "",
            "ODDS_API_KEY":      "",
        }):
            key, src = _odds_mod.resolve_odds_api_key_with_source()
        self.assertEqual(src, "ODDS_API_PAID_KEY")

    def test_t7c_resolve_with_source_falls_through_to_free(self):
        with patch.dict(os.environ, {
            "ODDS_API_PAID_KEY": "",
            "ODDS_API_KEY_100K": "",
            "ODDS_API_FREE_KEY": "free-key-xyz",
            "ODDS_API_KEY":      "",
        }):
            key, src = _odds_mod.resolve_odds_api_key_with_source()
        self.assertEqual(key, "free-key-xyz")
        self.assertEqual(src, "ODDS_API_FREE_KEY")

    def test_t8_resolve_key_uses_100k_when_paid_absent(self):
        with patch.dict(os.environ, {
            "ODDS_API_PAID_KEY": "",
            "ODDS_API_KEY_100K": "100k-key-xyz",
            "ODDS_API_FREE_KEY": "",
            "ODDS_API_KEY":      "",
        }):
            key = _odds_mod._resolve_key()
        self.assertEqual(key, "100k-key-xyz")


# ---------------------------------------------------------------------------
# T9 — No key configured → NOT_CALLED
# ---------------------------------------------------------------------------

class TestNoKeyConfigured(unittest.TestCase):

    def test_t9_no_key_returns_not_called(self):
        with patch.dict(os.environ, {
            "ODDS_API_PAID_KEY": "",
            "ODDS_API_KEY_100K": "",
            "ODDS_API_FREE_KEY": "",
            "ODDS_API_KEY":      "",
        }), patch(
            "gate_engine.pg_odds_quota.fetch_quota_snapshot",
            return_value={},
        ):
            data, status = _odds_mod._get("/sports/basketball_nba/odds")
        self.assertIsNone(data)
        self.assertIn("NOT_CALLED", status)

    def test_t9b_resolve_with_source_returns_none_when_no_key(self):
        with patch.dict(os.environ, {
            "ODDS_API_PAID_KEY": "",
            "ODDS_API_KEY_100K": "",
            "ODDS_API_FREE_KEY": "",
            "ODDS_API_KEY":      "",
        }):
            key, src = _odds_mod.resolve_odds_api_key_with_source()
        self.assertEqual(key, "")
        self.assertEqual(src, "NONE")


# ---------------------------------------------------------------------------
# T10 — 'high' tier semantics identical to 'paid' and 'free'
# ---------------------------------------------------------------------------

class TestHighTierSemantics(unittest.TestCase):

    def test_t10_high_tier_quota_update_semantics(self):
        """
        _odds_quota_update('high', ...) must behave identically to 'paid'
        and 'free': store quota state, fire warning below threshold, be
        returned in _odds_quota_snapshot().
        """
        mod = _get_app_symbols()

        # Above threshold — no warning
        warning = mod._odds_quota_update("high", "5000", "95000")
        self.assertFalse(warning)
        with mod._ODDS_QUOTA_LOCK:
            self.assertEqual(mod._ODDS_QUOTA_STORE["high"]["requests_remaining"], 5000)
            self.assertFalse(mod._ODDS_QUOTA_STORE["high"]["quota_warning"])

        # Below threshold — warning
        warning = mod._odds_quota_update("high", "10", "99990")
        self.assertTrue(warning)
        with mod._ODDS_QUOTA_LOCK:
            self.assertTrue(mod._ODDS_QUOTA_STORE["high"]["quota_warning"])

        # Snapshot contains 'high'
        snapshot = mod._odds_quota_snapshot()
        self.assertIn("high", snapshot)
        self.assertEqual(snapshot["high"]["requests_remaining"], 10)

    def test_t10b_paid_high_free_tracked_independently(self):
        """Three tiers are stored and retrieved independently."""
        mod = _get_app_symbols()
        mod._odds_quota_update("paid",   "1000", "9000")
        mod._odds_quota_update("high",   "50000", "50000")
        mod._odds_quota_update("free",   "30", "470")
        snap = mod._odds_quota_snapshot()
        self.assertEqual(snap["paid"]["requests_remaining"], 1000)
        self.assertEqual(snap["high"]["requests_remaining"], 50000)
        self.assertEqual(snap["free"]["requests_remaining"], 30)
        # only 'free' should trigger warning (30 < 50 threshold)
        self.assertFalse(snap["paid"]["quota_warning"])
        self.assertFalse(snap["high"]["quota_warning"])
        self.assertTrue(snap["free"]["quota_warning"])


if __name__ == "__main__":
    unittest.main()
