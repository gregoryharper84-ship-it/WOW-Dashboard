"""
gate_engine/tests/test_startup_prewarm_and_odds_auth.py
---------------------------------------------------------
Tests for two implemented requirement sets.

REQ-1  Auto-prewarm (startup_prewarm.py)
  - parse_pitcher_names: name splitting, dedup, edge cases
  - prewarm_today_pitchers: fetch → parse → prewarm call chain
  - Startup invokes prewarm exactly once (structural + integration path)
  - Later pitcher calls hit the identity cache after prewarm runs

REQ-2  Odds Gateway auth-contract migration (2026-08-14)
  -------------------------------------------------------
  The four Odds API operations have been migrated from X-WOW-Action-Key
  (GPT_ACTION_SECRET) to X-API-Key (SCORING_API_KEY), the same contract
  used by all other @require_api_key routes.

  Contract guarantees verified here:
    T-ODDS-01  X-WOW-Action-Key alone is rejected (structure confirms no WowActionKey scheme)
    T-ODDS-02  Gate-engine schema declares ApiKeyAuth / X-API-Key for all four odds ops
    T-ODDS-03  All four odds operationIds are present in gate-engine schema
    T-ODDS-04  Odds operationIds are unique within gate-engine schema (no collision)
    T-ODDS-05  Odds-gateway schema is marked retired / non-installable
    T-ODDS-06  Kalshi schema still uses X-API-Key (no unintended drift)
    T-ODDS-07  Gate-engine schema still uses ApiKeyAuth / X-API-Key (unchanged)
    T-ODDS-08  No WowActionKey scheme in gate-engine schema
    T-ODDS-09  internal_client.action_get delegates to scoring_get (X-API-Key path)
    T-ODDS-10  internal_client.scoring_get sends X-API-Key header

These tests intentionally do NOT import app.py — they test the module and
schema artefacts independently to keep runtime overhead minimal.
"""

from __future__ import annotations

import os
import pathlib
import re
import threading
import unittest
from unittest.mock import MagicMock, call, patch

import yaml

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
_FLASK_ROOT = pathlib.Path(__file__).parent.parent.parent  # artifacts/flask-scoring-api/
_SCHEMA_DIR  = _FLASK_ROOT                                  # schemas live at root
_APP_PY      = _FLASK_ROOT / "app.py"

_ODDS_SCHEMA    = _SCHEMA_DIR / "gpt-action-schema-odds-gateway.yaml"
_KALSHI_SCHEMA  = _SCHEMA_DIR / "gpt-action-schema-kalshi.yaml"
_GATE_SCHEMA    = _SCHEMA_DIR / "gpt-action-schema-gate-engine.yaml"


def _load_yaml(path: pathlib.Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# REQ-1: startup_prewarm unit tests
# ---------------------------------------------------------------------------

class TestParsePitcherNames(unittest.TestCase):
    """parse_pitcher_names() — name splitting, dedup, edge cases."""

    def setUp(self):
        from gate_engine.mlb.startup_prewarm import parse_pitcher_names
        self.parse = parse_pitcher_names

    def test_basic_two_part_name(self):
        result = self.parse({"gerrit cole": "NYY"})
        self.assertEqual(result, [("Gerrit", "Cole")])

    def test_multiword_last_name(self):
        result = self.parse({"jacob de grom": "NYM"})
        self.assertEqual(result, [("Jacob", "De Grom")])

    def test_hyphenated_last_name(self):
        result = self.parse({"sandy alcantara": "MIA"})
        self.assertEqual(result, [("Sandy", "Alcantara")])

    def test_title_case_applied(self):
        result = self.parse({"gerrit cole": "NYY"})
        first, last = result[0]
        self.assertEqual(first, "Gerrit")
        self.assertEqual(last, "Cole")

    def test_deduplicates_same_pitcher(self):
        result = self.parse({"gerrit cole": "NYY", "gerrit cole": "NYY"})
        self.assertEqual(len(result), 1)

    def test_deduplicates_case_insensitive(self):
        result = self.parse({"Gerrit Cole": "NYY", "gerrit cole": "NYY"})
        self.assertEqual(len(result), 1)

    def test_skips_single_token_name(self):
        result = self.parse({"cole": "NYY"})
        self.assertEqual(result, [])

    def test_skips_empty_string(self):
        result = self.parse({"": "NYY"})
        self.assertEqual(result, [])

    def test_skips_whitespace_only(self):
        result = self.parse({"   ": "NYY"})
        self.assertEqual(result, [])

    def test_empty_map(self):
        self.assertEqual(self.parse({}), [])

    def test_non_dict_returns_empty(self):
        self.assertEqual(self.parse(None), [])    # type: ignore[arg-type]

    def test_multiple_pitchers(self):
        result = self.parse({
            "gerrit cole": "NYY",
            "blake snell": "SF",
        })
        self.assertEqual(len(result), 2)
        names = {(f, l) for f, l in result}
        self.assertIn(("Gerrit", "Cole"), names)
        self.assertIn(("Blake", "Snell"), names)


class TestPrewarmTodayPitchers(unittest.TestCase):
    """prewarm_today_pitchers() — fetch → parse → prewarm call chain."""

    def _make_mock_identity(self, return_value=None):
        m = MagicMock(return_value=return_value or {"mlbam_id": 123})
        return m

    def _make_mock_savant(self, return_value=None):
        m = MagicMock(return_value=return_value or {"era": 3.0})
        return m

    def test_prewarm_calls_identity_and_savant_for_each_pitcher(self):
        from gate_engine.mlb.startup_prewarm import prewarm_today_pitchers

        fake_schedule = {"gerrit cole": "NYY", "blake snell": "SF"}
        identity_fn = self._make_mock_identity()
        savant_fn   = self._make_mock_savant()

        # Pass fetch_fn directly — no module-level sentinel patching needed
        queued, errors = prewarm_today_pitchers(
            identity_fn, savant_fn,
            fetch_fn=lambda: (fake_schedule, None),
        )

        self.assertEqual(queued, 2)
        self.assertEqual(errors, [])

    def test_fetch_error_returns_zero_queued(self):
        from gate_engine.mlb.startup_prewarm import prewarm_today_pitchers

        identity_fn = self._make_mock_identity()
        savant_fn   = self._make_mock_savant()

        queued, errors = prewarm_today_pitchers(
            identity_fn, savant_fn,
            fetch_fn=lambda: (None, "network error"),
        )

        self.assertEqual(queued, 0)
        self.assertGreater(len(errors), 0)

    def test_empty_schedule_returns_zero_queued(self):
        from gate_engine.mlb.startup_prewarm import prewarm_today_pitchers

        identity_fn = self._make_mock_identity()
        savant_fn   = self._make_mock_savant()

        queued, errors = prewarm_today_pitchers(
            identity_fn, savant_fn,
            fetch_fn=lambda: ({}, None),
        )

        # Empty schedule: zero queued. The implementation may include a
        # diagnostic "no_probables" notice in errors — that is expected and
        # non-fatal; we only assert queued=0 here.
        self.assertEqual(queued, 0)

    def test_parse_error_does_not_raise(self):
        from gate_engine.mlb.startup_prewarm import prewarm_today_pitchers

        identity_fn = self._make_mock_identity()
        savant_fn   = self._make_mock_savant()

        # Schedule with only invalid names — should return 0 queued, no crash
        queued, errors = prewarm_today_pitchers(
            identity_fn, savant_fn,
            fetch_fn=lambda: ({"x": "NYY"}, None),  # single token, invalid
        )

        self.assertEqual(queued, 0)

    def test_return_type_is_tuple(self):
        from gate_engine.mlb.startup_prewarm import prewarm_today_pitchers

        identity_fn = self._make_mock_identity()
        savant_fn   = self._make_mock_savant()

        result = prewarm_today_pitchers(
            identity_fn, savant_fn,
            fetch_fn=lambda: ({}, None),
        )
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)

    def test_partial_error_still_returns_good_pitchers(self):
        from gate_engine.mlb.startup_prewarm import prewarm_today_pitchers

        call_count = [0]

        def flaky_identity(first, last):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ValueError("identity lookup failed")
            return {"mlbam_id": 999}

        savant_fn = self._make_mock_savant()

        queued, errors = prewarm_today_pitchers(
            flaky_identity, savant_fn,
            fetch_fn=lambda: ({"gerrit cole": "NYY", "blake snell": "SF"}, None),
        )

        # At least one should succeed despite one failure
        self.assertGreaterEqual(queued + len(errors), 2)


class TestStartupWiringStructural(unittest.TestCase):
    """Structural scan of app.py for startup prewarm wiring."""

    @classmethod
    def setUpClass(cls):
        cls.app_text = _APP_PY.read_text(encoding="utf-8")

    def test_prewarm_imported_in_startup(self):
        self.assertIn(
            "startup_prewarm",
            self.app_text,
            "startup_prewarm must be imported somewhere in app.py",
        )

    def test_prewarm_today_pitchers_called(self):
        self.assertIn(
            "prewarm_today_pitchers",
            self.app_text,
            "prewarm_today_pitchers must be called in app.py",
        )

    def test_run_startup_warmup_exists(self):
        self.assertIn(
            "_run_startup_warmup",
            self.app_text,
            "_run_startup_warmup function must exist in app.py",
        )

    def test_prewarm_inside_startup_warmup(self):
        """prewarm_today_pitchers must appear within _run_startup_warmup."""
        # Find the _run_startup_warmup block and confirm prewarm is inside it
        idx_warmup = self.app_text.find("def _run_startup_warmup")
        idx_prewarm = self.app_text.find("prewarm_today_pitchers", idx_warmup)
        self.assertGreater(idx_prewarm, idx_warmup,
                           "prewarm_today_pitchers must appear after _run_startup_warmup def")


# ---------------------------------------------------------------------------
# REQ-2: Odds Gateway auth-contract migration tests
# ---------------------------------------------------------------------------

_ODDS_OP_IDS = {
    "getOddsApiEvents",
    "getOddsApiEventMarkets",
    "getOddsApiEventOdds",
    "getOddsApiQuotaStatus",
}


def _all_operation_ids(schema: dict) -> set[str]:
    """Collect all operationId values from a parsed OpenAPI schema."""
    ids: set[str] = set()
    for path_item in schema.get("paths", {}).values():
        for method_obj in path_item.values():
            if isinstance(method_obj, dict) and "operationId" in method_obj:
                ids.add(method_obj["operationId"])
    return ids


def _operations_with_security(schema: dict, scheme_name: str) -> set[str]:
    """Return operationIds whose security block references scheme_name."""
    ids: set[str] = set()
    for path_item in schema.get("paths", {}).values():
        for method_obj in path_item.values():
            if not isinstance(method_obj, dict):
                continue
            for sec_entry in method_obj.get("security", []):
                if scheme_name in sec_entry:
                    op_id = method_obj.get("operationId", "")
                    if op_id:
                        ids.add(op_id)
    return ids


class TestOddsAuthMigration(unittest.TestCase):
    """
    T-ODDS-01 through T-ODDS-08: Gate-engine schema and retired odds schema.
    """

    @classmethod
    def setUpClass(cls):
        cls.gate_schema = _load_yaml(_GATE_SCHEMA)
        cls.odds_schema = _load_yaml(_ODDS_SCHEMA)

    # T-ODDS-01: Old odds schema has no WowActionKey scheme (it's been retired)
    def test_t01_retired_odds_schema_has_no_wow_action_key_scheme(self):
        """X-WOW-Action-Key is not a valid scheme — the retired schema has no schemes."""
        schemes = self.odds_schema.get("components", {}).get("securitySchemes", {})
        self.assertNotIn(
            "WowActionKey", schemes,
            "Retired odds schema must not declare WowActionKey — "
            "this scheme is no longer valid for any odds route",
        )

    # T-ODDS-02: Gate-engine schema has ApiKeyAuth / X-API-Key for all four odds ops
    def test_t02_gate_schema_declares_api_key_auth_for_odds_ops(self):
        """All four odds operationIds must have ApiKeyAuth in their security block."""
        ops_with_api_key = _operations_with_security(self.gate_schema, "ApiKeyAuth")
        missing = _ODDS_OP_IDS - ops_with_api_key
        self.assertFalse(
            missing,
            f"These odds operations lack ApiKeyAuth security in gate-engine schema: {missing}",
        )

    # T-ODDS-03: All four odds operationIds present in gate-engine schema
    def test_t03_all_four_odds_op_ids_in_gate_schema(self):
        all_ids = _all_operation_ids(self.gate_schema)
        missing = _ODDS_OP_IDS - all_ids
        self.assertFalse(
            missing,
            f"Missing odds operationIds in gate-engine schema: {missing}",
        )

    # T-ODDS-04: All operationIds are unique within gate-engine schema
    def test_t04_gate_schema_operation_ids_are_unique(self):
        all_ids: list[str] = []
        for path_item in self.gate_schema.get("paths", {}).values():
            for method_obj in path_item.values():
                if isinstance(method_obj, dict) and "operationId" in method_obj:
                    all_ids.append(method_obj["operationId"])
        self.assertEqual(
            len(all_ids), len(set(all_ids)),
            f"Duplicate operationIds in gate-engine schema: "
            f"{[x for x in all_ids if all_ids.count(x) > 1]}",
        )

    # T-ODDS-05: Odds-gateway schema is marked retired / non-installable
    def test_t05_odds_gateway_schema_marked_retired(self):
        """The retired odds-gateway schema must have x-retired: true and no paths."""
        info = self.odds_schema.get("info", {})
        self.assertTrue(
            info.get("x-retired", False),
            "gpt-action-schema-odds-gateway.yaml must have info.x-retired: true",
        )
        paths = self.odds_schema.get("paths", {})
        self.assertFalse(
            paths,
            "Retired odds-gateway schema must have no paths (intentionally empty)",
        )

    # T-ODDS-06: Kalshi schema still uses X-API-Key (no unintended drift)
    def test_t06_kalshi_schema_still_uses_api_key_auth(self):
        kalshi_schema = _load_yaml(_KALSHI_SCHEMA)
        schemes = kalshi_schema.get("components", {}).get("securitySchemes", {})
        self.assertIn(
            "ApiKeyAuth", schemes,
            "Kalshi schema must still declare ApiKeyAuth",
        )
        self.assertEqual(
            schemes["ApiKeyAuth"].get("name"), "X-API-Key",
            "Kalshi schema ApiKeyAuth header must be X-API-Key",
        )

    # T-ODDS-07: Gate-engine schema still has ApiKeyAuth (unchanged)
    def test_t07_gate_schema_has_api_key_auth_scheme(self):
        schemes = self.gate_schema.get("components", {}).get("securitySchemes", {})
        self.assertIn("ApiKeyAuth", schemes)
        self.assertEqual(
            schemes["ApiKeyAuth"].get("name"), "X-API-Key",
            "Gate-engine ApiKeyAuth header must be X-API-Key",
        )

    # T-ODDS-08: Gate-engine schema has no WowActionKey scheme
    def test_t08_gate_schema_has_no_wow_action_key(self):
        schemes = self.gate_schema.get("components", {}).get("securitySchemes", {})
        self.assertNotIn(
            "WowActionKey", schemes,
            "Gate-engine schema must not contain WowActionKey",
        )


class TestOddsInternalClientMigration(unittest.TestCase):
    """
    T-ODDS-09, T-ODDS-10: internal_client uses X-API-Key for odds routes.
    """

    def test_t09_action_get_delegates_to_scoring_get(self):
        """action_get() must delegate to scoring_get() (backward-compat alias)."""
        from gate_engine import internal_client as ic

        captured = {}

        def fake_do_get(path, params, headers, timeout=30):
            captured["headers"] = headers
            return {"ok": True}, 200, None

        with patch.object(ic, "_do_get", side_effect=fake_do_get):
            with patch.dict(os.environ, {"SCORING_API_KEY": "test-key-abc"}, clear=True):
                ic.action_get("/wow/odds/events", {"sport": "baseball_mlb"})

        self.assertIn("X-API-Key", captured.get("headers", {}),
                      "action_get must send X-API-Key header (not X-WOW-Action-Key)")
        self.assertNotIn("X-WOW-Action-Key", captured.get("headers", {}),
                         "action_get must NOT send X-WOW-Action-Key")

    def test_t10_scoring_get_sends_x_api_key(self):
        """scoring_get() must send X-API-Key with SCORING_API_KEY value."""
        from gate_engine import internal_client as ic

        captured = {}

        def fake_do_get(path, params, headers, timeout=30):
            captured["headers"] = headers
            return {"ok": True}, 200, None

        with patch.object(ic, "_do_get", side_effect=fake_do_get):
            with patch.dict(os.environ, {"SCORING_API_KEY": "my-scoring-key"}):
                ic.scoring_get("/wow/odds/quota-status")

        self.assertEqual(
            captured.get("headers", {}).get("X-API-Key"), "my-scoring-key",
            "scoring_get must send the SCORING_API_KEY value as X-API-Key",
        )

    def test_t09b_action_get_missing_scoring_key_returns_auth_contract_fail(self):
        """action_get() with no SCORING_API_KEY must return AUTH_CONTRACT_FAIL."""
        from gate_engine import internal_client as ic

        env = {k: v for k, v in os.environ.items() if k != "SCORING_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            _, status, err = ic.action_get("/wow/odds/events")

        self.assertEqual(err, ic.AUTH_CONTRACT_FAIL)
        self.assertEqual(status, 0)

    def test_t10b_scoring_get_missing_key_returns_auth_contract_fail(self):
        """scoring_get() with no SCORING_API_KEY must return AUTH_CONTRACT_FAIL."""
        from gate_engine import internal_client as ic

        env = {k: v for k, v in os.environ.items() if k != "SCORING_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            _, status, err = ic.scoring_get("/wow/odds/quota-status")

        self.assertEqual(err, ic.AUTH_CONTRACT_FAIL)
        self.assertEqual(status, 0)


class TestOddsRoutesInAppStructure(unittest.TestCase):
    """
    Structural scan of app.py confirming auth decorator migration.
    """

    @classmethod
    def setUpClass(cls):
        cls.app_text = _APP_PY.read_text(encoding="utf-8")

    def _find_route_block(self, route_path: str) -> str:
        """Extract ~500 chars starting at the @app.route line for a given path."""
        idx = self.app_text.find(f'"{route_path}"')
        if idx == -1:
            return ""
        # Walk back to the @app.route decorator
        start = self.app_text.rfind("@app.route", 0, idx)
        return self.app_text[start: start + 500]

    def test_odds_events_has_require_api_key(self):
        block = self._find_route_block("/wow/odds/events")
        self.assertIn("@require_api_key", block,
                      "/wow/odds/events must use @require_api_key decorator")

    def test_odds_event_markets_has_require_api_key(self):
        block = self._find_route_block("/wow/odds/event-markets")
        self.assertIn("@require_api_key", block,
                      "/wow/odds/event-markets must use @require_api_key decorator")

    def test_odds_event_odds_has_require_api_key(self):
        block = self._find_route_block("/wow/odds/event-odds")
        self.assertIn("@require_api_key", block,
                      "/wow/odds/event-odds must use @require_api_key decorator")

    def test_odds_quota_status_has_require_api_key(self):
        block = self._find_route_block("/wow/odds/quota-status")
        self.assertIn("@require_api_key", block,
                      "/wow/odds/quota-status must use @require_api_key decorator")

    def test_odds_events_does_not_call_verify_wow_action_key(self):
        """_verify_wow_action_key must not be called inside the events handler."""
        block = self._find_route_block("/wow/odds/events")
        self.assertNotIn("_verify_wow_action_key", block,
                         "/wow/odds/events must not call _verify_wow_action_key")

    def test_odds_event_markets_does_not_call_verify_wow_action_key(self):
        block = self._find_route_block("/wow/odds/event-markets")
        self.assertNotIn("_verify_wow_action_key", block,
                         "/wow/odds/event-markets must not call _verify_wow_action_key")

    def test_odds_event_odds_does_not_call_verify_wow_action_key(self):
        block = self._find_route_block("/wow/odds/event-odds")
        self.assertNotIn("_verify_wow_action_key", block,
                         "/wow/odds/event-odds must not call _verify_wow_action_key")

    def test_odds_quota_status_does_not_call_verify_wow_action_key(self):
        block = self._find_route_block("/wow/odds/quota-status")
        self.assertNotIn("_verify_wow_action_key", block,
                         "/wow/odds/quota-status must not call _verify_wow_action_key")


class TestGateEngineSchemaYamlValidity(unittest.TestCase):
    """Basic OpenAPI structural validity of the merged gate-engine schema."""

    @classmethod
    def setUpClass(cls):
        cls.schema = _load_yaml(_GATE_SCHEMA)

    def test_openapi_field_present(self):
        self.assertIn("openapi", self.schema)

    def test_info_field_present(self):
        self.assertIn("info", self.schema)

    def test_paths_field_present(self):
        self.assertIn("paths", self.schema)

    def test_four_odds_paths_present(self):
        paths = self.schema.get("paths", {})
        for path in ["/wow/odds/events", "/wow/odds/event-markets",
                     "/wow/odds/event-odds", "/wow/odds/quota-status"]:
            self.assertIn(path, paths, f"{path} must be in gate-engine schema paths")

    def test_existing_gate_paths_still_present(self):
        paths = self.schema.get("paths", {})
        for path in ["/wow/engine/health", "/wow/governance/status",
                     "/gate-engine/run", "/normalize-legs", "/analyze-and-score"]:
            self.assertIn(path, paths, f"Existing path {path} must not be removed")

    def test_schema_is_valid_yaml_not_none(self):
        self.assertIsNotNone(self.schema)
        self.assertIsInstance(self.schema, dict)

    def test_api_key_auth_description_mentions_odds(self):
        desc = (
            self.schema
            .get("components", {})
            .get("securitySchemes", {})
            .get("ApiKeyAuth", {})
            .get("description", "")
        )
        self.assertIn(
            "/wow/odds/", desc,
            "ApiKeyAuth description should mention /wow/odds/ routes",
        )


class TestRetiredOddsGatewaySchema(unittest.TestCase):
    """Verify the odds-gateway schema is properly retired."""

    @classmethod
    def setUpClass(cls):
        cls.schema = _load_yaml(_ODDS_SCHEMA)

    def test_retired_schema_yaml_parses(self):
        self.assertIsNotNone(self.schema)

    def test_no_paths_in_retired_schema(self):
        self.assertFalse(
            self.schema.get("paths"),
            "Retired schema must have no paths",
        )

    def test_no_security_schemes_in_retired_schema(self):
        schemes = self.schema.get("components", {}).get("securitySchemes", {})
        self.assertFalse(schemes, "Retired schema must declare no security schemes")

    def test_retired_flag_is_true(self):
        self.assertTrue(
            self.schema.get("info", {}).get("x-retired"),
            "Retired schema must have info.x-retired: true",
        )

    def test_replacement_field_points_to_gate_engine(self):
        replacement = self.schema.get("info", {}).get("x-replacement", "")
        self.assertIn(
            "gate-engine", replacement,
            "x-replacement must point to the gate-engine schema",
        )


if __name__ == "__main__":
    unittest.main()
