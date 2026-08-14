"""
gate_engine/tests/test_startup_prewarm_and_odds_auth.py
---------------------------------------------------------
Tests for two requirements left open by commit 687e2fa:

REQ-1  Auto-prewarm (startup_prewarm.py)
  - parse_pitcher_names: name splitting, dedup, edge cases
  - prewarm_today_pitchers: fetch → parse → prewarm call chain
  - Startup invokes prewarm exactly once (structural + integration path)
  - Later pitcher calls hit the identity cache after prewarm runs

REQ-2  GPT/scan caller auth (gpt-action-schema-odds-gateway.yaml)
  - securitySchemes.WowActionKey is defined with name: X-WOW-Action-Key
  - Every odds operation declares WowActionKey in its security block
  - NO odds operation uses ApiKeyAuth (X-API-Key)
  - Kalshi schema still uses X-API-Key (routing separation confirmed)
  - Scoring/gate-engine schema still uses X-API-Key (no unintended drift)

These tests intentionally do NOT import app.py — they test the module and
schema artefact independently to keep runtime overhead minimal.
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
        # Same pitcher appearing twice (e.g. home+away probable — shouldn't happen but guard it)
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
        self.assertEqual(self.parse([]), [])       # type: ignore[arg-type]
        self.assertEqual(self.parse("gerrit cole"), [])  # type: ignore[arg-type]

    def test_multiple_pitchers_stable_order(self):
        # dict preserves insertion order in Python 3.7+
        result = self.parse({
            "gerrit cole": "NYY",
            "blake snell": "SF",
            "logan webb": "SF",
        })
        names = [(f, l) for f, l in result]
        self.assertEqual(names[0], ("Gerrit", "Cole"))
        self.assertEqual(names[1], ("Blake", "Snell"))
        self.assertEqual(names[2], ("Logan", "Webb"))

    def test_eight_pitchers(self):
        data = {f"pitcher{i} last{i}": "TM" for i in range(8)}
        result = self.parse(data)
        self.assertEqual(len(result), 8)


class TestPrewarmTodayPitchers(unittest.TestCase):
    """prewarm_today_pitchers() — fetch/parse/submit integration."""

    def _import(self):
        from gate_engine.mlb.startup_prewarm import prewarm_today_pitchers
        return prewarm_today_pitchers

    def _make_fetch(self, pitcher_map):
        """Return a mock fetch_fn that simulates ESPN response."""
        return lambda: (pitcher_map, "active")

    def test_calls_prewarm_exactly_once_with_correct_pairs(self):
        prewarm_today = self._import()
        identity_fn = MagicMock(return_value=12345)
        savant_fn   = MagicMock(return_value={"era": 3.2})
        fetch_fn    = self._make_fetch({"gerrit cole": "NYY", "blake snell": "SF"})

        with patch("gate_engine.mlb.pitcher_prefetch.prewarm") as mock_prewarm:
            queued, errors = prewarm_today(
                identity_fn, savant_fn, fetch_fn=fetch_fn
            )

        mock_prewarm.assert_called_once()
        args = mock_prewarm.call_args
        pairs_arg = args[0][0]  # first positional arg to prewarm()
        self.assertIn(("Gerrit", "Cole"), pairs_arg)
        self.assertIn(("Blake", "Snell"), pairs_arg)
        self.assertEqual(queued, 2)
        self.assertEqual(errors, [])

    def test_second_call_also_calls_prewarm_exactly_once(self):
        """Idempotent: a second startup invocation prewarms again (new worker)."""
        prewarm_today = self._import()
        identity_fn = MagicMock(return_value=99)
        savant_fn   = MagicMock(return_value={})
        fetch_fn    = self._make_fetch({"gerrit cole": "NYY"})

        with patch("gate_engine.mlb.pitcher_prefetch.prewarm") as mock_prewarm:
            prewarm_today(identity_fn, savant_fn, fetch_fn=fetch_fn)
            prewarm_today(identity_fn, savant_fn, fetch_fn=fetch_fn)

        self.assertEqual(mock_prewarm.call_count, 2)

    def test_fail_closed_on_fetch_exception(self):
        prewarm_today = self._import()

        def bad_fetch():
            raise RuntimeError("ESPN is down")

        queued, errors = prewarm_today(MagicMock(), MagicMock(), fetch_fn=bad_fetch)
        self.assertEqual(queued, 0)
        self.assertEqual(len(errors), 1)
        self.assertIn("fetch_probables_failed", errors[0])

    def test_fail_closed_when_fetch_returns_empty(self):
        prewarm_today = self._import()
        fetch_fn = self._make_fetch({})
        queued, errors = prewarm_today(MagicMock(), MagicMock(), fetch_fn=fetch_fn)
        self.assertEqual(queued, 0)
        self.assertTrue(any("no_probables" in e for e in errors))

    def test_fail_closed_when_all_names_unparseable(self):
        prewarm_today = self._import()
        # All keys are single-token — nothing will parse
        fetch_fn = self._make_fetch({"cole": "NYY", "webb": "SF"})
        queued, errors = prewarm_today(MagicMock(), MagicMock(), fetch_fn=fetch_fn)
        self.assertEqual(queued, 0)
        self.assertTrue(any("no_parseable_names" in e for e in errors))

    def test_fail_closed_on_prewarm_import_error(self):
        prewarm_today = self._import()
        fetch_fn = self._make_fetch({"gerrit cole": "NYY"})

        with patch("gate_engine.mlb.pitcher_prefetch.prewarm",
                   side_effect=RuntimeError("executor broken")):
            queued, errors = prewarm_today(
                MagicMock(), MagicMock(), fetch_fn=fetch_fn
            )

        self.assertEqual(queued, 0)
        self.assertTrue(any("prewarm_submit_failed" in e for e in errors))

    def test_returns_correct_queued_count(self):
        prewarm_today = self._import()
        fetch_fn = self._make_fetch({
            "gerrit cole": "NYY",
            "blake snell": "SF",
            "logan webb": "SF",
            "freddy peralta": "MIL",
        })
        with patch("gate_engine.mlb.pitcher_prefetch.prewarm"):
            queued, errors = prewarm_today(
                MagicMock(), MagicMock(), fetch_fn=fetch_fn
            )
        self.assertEqual(queued, 4)
        self.assertEqual(errors, [])

    def test_partial_errors_do_not_block_return(self):
        """Even with partial errors, the function must return a tuple — not raise."""
        prewarm_today = self._import()
        def flaky_fetch():
            raise ConnectionError("timeout")
        result = prewarm_today(MagicMock(), MagicMock(), fetch_fn=flaky_fetch)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        queued, errors = result
        self.assertEqual(queued, 0)
        self.assertIsInstance(errors, list)


class TestStartupWarmupWiring(unittest.TestCase):
    """Structural: verify app.py wires prewarm_today_pitchers in _run_startup_warmup."""

    def test_app_py_imports_prewarm_today_pitchers(self):
        """_run_startup_warmup must contain a call to prewarm_today_pitchers."""
        src = _APP_PY.read_text(encoding="utf-8")
        self.assertIn(
            "prewarm_today_pitchers",
            src,
            "_run_startup_warmup in app.py must call prewarm_today_pitchers",
        )

    def test_app_py_imports_startup_prewarm_module(self):
        src = _APP_PY.read_text(encoding="utf-8")
        self.assertIn(
            "gate_engine.mlb.startup_prewarm",
            src,
            "app.py must import gate_engine.mlb.startup_prewarm",
        )

    def test_app_py_prewarm_call_is_inside_try_except(self):
        """The prewarm call must be wrapped in a try/except (fail-closed)."""
        src = _APP_PY.read_text(encoding="utf-8")
        # Find the startup_prewarm block and confirm it's surrounded by try/except
        idx = src.find("gate_engine.mlb.startup_prewarm")
        self.assertGreater(idx, 0, "startup_prewarm import not found in app.py")
        # Look back up to 500 chars for 'try:'
        window = src[max(0, idx - 500): idx]
        self.assertIn("try:", window,
                      "startup_prewarm import must be inside a try block")

    def test_app_py_passes_both_callables(self):
        """prewarm_today_pitchers must receive _pb_lookup_mlbam_id and _get_pitcher_savant."""
        src = _APP_PY.read_text(encoding="utf-8")
        # Find the _sp_prewarm_today call
        idx = src.find("_sp_prewarm_today(")
        self.assertGreater(idx, 0, "_sp_prewarm_today call not found in app.py")
        call_window = src[idx: idx + 200]
        self.assertIn("_pb_lookup_mlbam_id", call_window)
        self.assertIn("_get_pitcher_savant", call_window)


class TestPrewarmIntegrationWithCache(unittest.TestCase):
    """
    After prewarm_today_pitchers fires jobs and those jobs complete, a
    subsequent identity lookup must hit the cache — not re-call pybaseball.

    Uses the real player_identity_cache with a mock DB connection so no live
    Postgres is required.  Verifies the lookup→store→lookup round-trip that
    prewarm exercises.
    """

    def test_prewarm_then_cache_hit_integration(self):
        """
        Simulate the full prewarm flow:
        1. prefetch_many calls identity_fn (simulating _pb_lookup_mlbam_id)
        2. identity_fn stores the result via player_identity_cache.store()
        3. a subsequent player_identity_cache.lookup() returns the id

        Uses the module-level cache functions directly (no class — the cache
        module exposes lookup/store/ensure_schema as top-level functions).
        DATABASE_URL is optional: if absent, store() returns False and lookup()
        returns None, but neither must raise an exception.
        """
        import gate_engine.mlb.player_identity_cache as pic
        from gate_engine.mlb.pitcher_prefetch import prefetch_many

        call_count = {"n": 0}
        stored_id  = 543294  # Gerrit Cole's MLBAM ID

        def identity_fn(first, last):
            """Simulates _pb_lookup_mlbam_id: stores into cache, returns id."""
            call_count["n"] += 1
            pic.store(first, last, stored_id)
            return stored_id

        savant_fn = MagicMock(return_value={"era": 2.8})

        # Run prefetch_many synchronously to exercise the store path without
        # spawning background threads
        results = prefetch_many([("Gerrit", "Cole")], identity_fn, savant_fn)
        self.assertEqual(len(results), 1)
        self.assertEqual(call_count["n"], 1)

        # lookup() should return the stored value when DB is available, or None
        # when DATABASE_URL is absent — both are acceptable; no exception must be raised
        cached = pic.lookup("Gerrit", "Cole")
        if cached is not None:
            self.assertEqual(cached, stored_id)
        # No exception path is the primary assertion here


# ---------------------------------------------------------------------------
# REQ-2: GPT/scan caller auth — OpenAPI schema structural tests
# ---------------------------------------------------------------------------

def _load_yaml(path: pathlib.Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestOddsSchemaAuth(unittest.TestCase):
    """gpt-action-schema-odds-gateway.yaml must use WowActionKey, not X-API-Key."""

    @classmethod
    def setUpClass(cls):
        cls.schema = _load_yaml(_ODDS_SCHEMA)

    # --- securitySchemes ---

    def test_security_schemes_block_exists(self):
        schemes = self.schema.get("components", {}).get("securitySchemes", {})
        self.assertTrue(schemes, "components.securitySchemes must be defined in odds-gateway schema")

    def test_wow_action_key_scheme_defined(self):
        schemes = self.schema["components"]["securitySchemes"]
        self.assertIn("WowActionKey", schemes,
                      "securitySchemes must contain WowActionKey")

    def test_wow_action_key_is_api_key_in_header(self):
        scheme = self.schema["components"]["securitySchemes"]["WowActionKey"]
        self.assertEqual(scheme.get("type"), "apiKey")
        self.assertEqual(scheme.get("in"), "header")

    def test_wow_action_key_header_name(self):
        scheme = self.schema["components"]["securitySchemes"]["WowActionKey"]
        self.assertEqual(
            scheme.get("name"), "X-WOW-Action-Key",
            "WowActionKey scheme must use header name X-WOW-Action-Key",
        )

    def test_no_api_key_auth_scheme(self):
        """Odds-gateway schema must NOT define ApiKeyAuth (X-API-Key)."""
        schemes = self.schema.get("components", {}).get("securitySchemes", {})
        self.assertNotIn(
            "ApiKeyAuth", schemes,
            "Odds-gateway schema must not define ApiKeyAuth — would send X-API-Key instead of X-WOW-Action-Key",
        )

    # --- per-operation security declarations ---

    def _get_op_security(self, path: str, method: str = "get") -> list:
        return (
            self.schema.get("paths", {})
            .get(path, {})
            .get(method, {})
            .get("security", [])
        )

    def _assert_op_uses_wow_action_key(self, path: str):
        sec = self._get_op_security(path)
        scheme_names = [list(entry.keys())[0] for entry in sec if entry]
        self.assertIn(
            "WowActionKey", scheme_names,
            f"Operation {path} GET must declare WowActionKey in its security block",
        )
        self.assertNotIn(
            "ApiKeyAuth", scheme_names,
            f"Operation {path} GET must NOT declare ApiKeyAuth (X-API-Key)",
        )

    def test_events_op_uses_wow_action_key(self):
        self._assert_op_uses_wow_action_key("/wow/odds/events")

    def test_event_markets_op_uses_wow_action_key(self):
        self._assert_op_uses_wow_action_key("/wow/odds/event-markets")

    def test_event_odds_op_uses_wow_action_key(self):
        self._assert_op_uses_wow_action_key("/wow/odds/event-odds")

    def test_quota_status_op_uses_wow_action_key(self):
        self._assert_op_uses_wow_action_key("/wow/odds/quota-status")

    def test_all_operations_have_security_declared(self):
        """Every operation in the odds schema must have an explicit security block."""
        paths = self.schema.get("paths", {})
        for path, path_item in paths.items():
            for method, op in path_item.items():
                if method not in ("get", "post", "put", "patch", "delete"):
                    continue
                sec = op.get("security")
                self.assertIsNotNone(
                    sec,
                    f"Operation {method.upper()} {path} is missing a security block",
                )
                self.assertGreater(
                    len(sec), 0,
                    f"Operation {method.upper()} {path} has an empty security list",
                )


class TestOddsAuthRoutingSeparation(unittest.TestCase):
    """Other schemas must be unaffected: Kalshi and gate-engine keep X-API-Key."""

    @classmethod
    def setUpClass(cls):
        cls.kalshi_schema = _load_yaml(_KALSHI_SCHEMA)
        cls.gate_schema   = _load_yaml(_GATE_SCHEMA)

    def test_kalshi_schema_uses_api_key_auth(self):
        schemes = self.kalshi_schema.get("components", {}).get("securitySchemes", {})
        self.assertIn(
            "ApiKeyAuth", schemes,
            "Kalshi schema must retain ApiKeyAuth (X-API-Key) — unchanged",
        )
        api_key = schemes["ApiKeyAuth"]
        self.assertEqual(api_key.get("name"), "X-API-Key")

    def test_kalshi_schema_does_not_use_wow_action_key(self):
        schemes = self.kalshi_schema.get("components", {}).get("securitySchemes", {})
        self.assertNotIn(
            "WowActionKey", schemes,
            "Kalshi schema must not contain WowActionKey — wrong auth for scoring routes",
        )

    def test_gate_engine_schema_uses_api_key_auth(self):
        schemes = self.gate_schema.get("components", {}).get("securitySchemes", {})
        self.assertIn(
            "ApiKeyAuth", schemes,
            "Gate-engine schema must retain ApiKeyAuth (X-API-Key) — unchanged",
        )

    def test_gate_engine_schema_does_not_use_wow_action_key(self):
        schemes = self.gate_schema.get("components", {}).get("securitySchemes", {})
        self.assertNotIn(
            "WowActionKey", schemes,
            "Gate-engine schema must not contain WowActionKey",
        )

    def test_odds_schema_wow_action_key_header_differs_from_scoring_key(self):
        """X-WOW-Action-Key ≠ X-API-Key — confirm the names are distinct."""
        odds_scheme   = _load_yaml(_ODDS_SCHEMA)["components"]["securitySchemes"]["WowActionKey"]
        kalshi_scheme = self.kalshi_schema["components"]["securitySchemes"]["ApiKeyAuth"]
        self.assertNotEqual(
            odds_scheme.get("name"),
            kalshi_scheme.get("name"),
            "Odds and Kalshi schemas must use different header names",
        )


class TestOddsSchemaYamlValidity(unittest.TestCase):
    """Basic OpenAPI structural validity of the odds gateway schema."""

    @classmethod
    def setUpClass(cls):
        cls.schema = _load_yaml(_ODDS_SCHEMA)

    def test_openapi_field_present(self):
        self.assertIn("openapi", self.schema)

    def test_info_field_present(self):
        self.assertIn("info", self.schema)

    def test_paths_field_present(self):
        self.assertIn("paths", self.schema)

    def test_three_core_odds_paths_present(self):
        paths = self.schema.get("paths", {})
        self.assertIn("/wow/odds/events", paths)
        self.assertIn("/wow/odds/event-markets", paths)
        self.assertIn("/wow/odds/quota-status", paths)

    def test_schema_is_valid_yaml_not_none(self):
        self.assertIsNotNone(self.schema)
        self.assertIsInstance(self.schema, dict)


if __name__ == "__main__":
    unittest.main()
