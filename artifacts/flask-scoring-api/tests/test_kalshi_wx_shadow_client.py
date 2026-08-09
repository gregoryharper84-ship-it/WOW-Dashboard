"""
tests/test_kalshi_wx_shadow_client.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — research client scaffold tests

Tests for gate_engine/kalshi_wx_shadow_client.py.

No live API calls are made.  Tests use mock clients or no client at all.
No network access, no API cost.

Test plan
─────────
Section S — Safety and runtime scaffolding

S1: Default-off behavior — with _SHADOW_ENABLED=False (the default), research()
    returns a closed SHADOW_AGENT_DISABLED failure immediately.

S2: No network call while disabled — strict mock client with side_effect=
    AssertionError is passed; flag=False means the function returns before the
    mock is ever touched; assert_not_called() confirms zero API calls.

S3: No route changes — structural grep confirming gate_engine/kalshi_wx_shadow_client
    is not imported by app.py or registered on any Flask route.

S4: No persistent writes — structural grep confirming the client module contains
    no database imports (psycopg2, sqlalchemy, pg8000, sqlite3, etc.).

S5: No activation from credential presence alone — even with ANTHROPIC_API_KEY
    and AI_INTEGRATIONS_ANTHROPIC_API_KEY set in the environment, flag=False
    means research() returns SHADOW_AGENT_DISABLED without touching the network.

S6: Authority constants all False — CAN_EXECUTE, PRODUCTION_AUTHORITY, and
    USER_OUTPUT_AUTHORITY are all False on the class; assert_inert() passes.

S7: Flag=True reaches SHADOW_CLIENT_NOT_WIRED — with the flag patched on and
    authority constants correct (False), research() returns NOT_WIRED failure
    (the scaffold exists but is not yet wired to an agent).

S8: Authority guard rejects subclass with True constant — a subclass with
    CAN_EXECUTE=True causes research() to return SHADOW_CLIENT_AUTHORITY_VIOLATION
    even when the feature flag is on.

S9: research() always returns ShadowValidationResult — in all tested scenarios,
    the return value is a ShadowValidationResult with shadow_failure_only=True;
    never a dict, never None.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# ── path setup ────────────────────────────────────────────────────────────────
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from gate_engine.kalshi_wx_shadow_client import (
    KalshiWxShadowResearchClient,
    _shadow_client_failure,
    _SHADOW_ENABLED,
)
from gate_engine.kalshi_wx_shadow_schema import (
    ShadowSchemaViolation,
    ShadowValidationResult,
)


# ─────────────────────────────────────────────────────────────────────────────
# S1 — Default-off: research() returns SHADOW_AGENT_DISABLED
# ─────────────────────────────────────────────────────────────────────────────

class TestS1DefaultOff(unittest.TestCase):

    def test_S1_flag_off_returns_shadow_agent_disabled(self):
        """
        With _SHADOW_ENABLED=False (the module default), research() must return
        a closed failure containing "SHADOW_AGENT_DISABLED" in the reason.

        Assertions:
          1. result.passed is False
          2. result.shadow_failure_only is True
          3. result.failure_reason contains "SHADOW_AGENT_DISABLED"
          4. result is a ShadowValidationResult (not None, not a dict)
        """
        with patch("gate_engine.kalshi_wx_shadow_client._SHADOW_ENABLED", False):
            client = KalshiWxShadowResearchClient()
            result = client.research(city="NYC", date="2026-08-08", run_id="run-s1")

        self.assertFalse(result.passed)
        self.assertTrue(result.shadow_failure_only)
        self.assertIn("SHADOW_AGENT_DISABLED", result.failure_reason,
                      f"Expected SHADOW_AGENT_DISABLED in reason; got {result.failure_reason!r}")
        self.assertIsInstance(result, ShadowValidationResult)
        self.assertIsNotNone(result)


# ─────────────────────────────────────────────────────────────────────────────
# S2 — No network call while disabled
# ─────────────────────────────────────────────────────────────────────────────

class TestS2NoNetworkCallWhileDisabled(unittest.TestCase):

    def test_S2_flag_off_strict_mock_never_called(self):
        """
        With flag=False, a strict mock client with side_effect=AssertionError
        is supplied.  The feature flag gate fires first and returns before the
        mock is ever reached.  messages.create.assert_not_called() confirms
        zero API calls.

        The test itself would fail loudly (AssertionError from the mock) if the
        code somehow bypassed the flag gate and attempted a network call.

        Assertions:
          1. research() does not raise
          2. result is a closed failure with SHADOW_AGENT_DISABLED
          3. strict_mock.messages.create was never called
        """
        strict_mock = MagicMock()
        strict_mock.messages.create.side_effect = AssertionError(
            "messages.create must NEVER be called when the feature flag is off"
        )

        with patch("gate_engine.kalshi_wx_shadow_client._SHADOW_ENABLED", False):
            client = KalshiWxShadowResearchClient(sdk_client=strict_mock)
            # Must not raise despite the side_effect on the mock
            result = client.research(city="CHI", date="2026-08-09", run_id="run-s2")

        self.assertFalse(result.passed)
        self.assertIn("SHADOW_AGENT_DISABLED", result.failure_reason)
        strict_mock.messages.create.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# S3 — No route changes (structural grep)
# ─────────────────────────────────────────────────────────────────────────────

class TestS3NoRouteChanges(unittest.TestCase):

    def test_S3_shadow_client_not_imported_by_app(self):
        """
        grep app.py for any reference to kalshi_wx_shadow_client or
        KalshiWxShadowResearchClient — must find zero matches.

        This confirms the scaffold is not wired into any Flask route,
        not imported at application startup, and not reachable from any
        existing endpoint.
        """
        import subprocess
        app_py = os.path.join(_REPO, "app.py")
        self.assertTrue(os.path.exists(app_py),
                        f"app.py not found at {app_py}")

        for pattern in ("kalshi_wx_shadow_client", "KalshiWxShadowResearchClient"):
            result = subprocess.run(
                ["grep", "-n", pattern, app_py],
                capture_output=True, text=True,
            )
            self.assertEqual(
                result.returncode, 1,
                f"Pattern {pattern!r} found in app.py — scaffold must not be "
                f"imported or routed:\n{result.stdout}",
            )


# ─────────────────────────────────────────────────────────────────────────────
# S4 — No persistent writes (structural: no DB imports in module)
# ─────────────────────────────────────────────────────────────────────────────

class TestS4NoPersistentWrites(unittest.TestCase):

    def test_S4_no_database_imports_in_shadow_client_module(self):
        """
        Read the source of gate_engine/kalshi_wx_shadow_client.py and assert
        it contains no database-layer *import statements*.

        Checked packages: psycopg2, sqlalchemy, pg8000, sqlite3,
        aiopg, asyncpg, tortoise, peewee, databases.

        This checks actual import/from-import lines only — not mentions in
        comments or docstrings — so the module can explain what it excludes
        without tripping the assertion.
        """
        import re

        module_path = os.path.join(
            _REPO, "gate_engine", "kalshi_wx_shadow_client.py"
        )
        self.assertTrue(os.path.exists(module_path),
                        f"Module not found at {module_path}")

        with open(module_path, "r") as f:
            source = f.read()

        forbidden_db_packages = [
            "psycopg2",
            "sqlalchemy",
            "pg8000",
            "sqlite3",
            "aiopg",
            "asyncpg",
            "tortoise",
            "peewee",
            "databases",
        ]
        for pkg in forbidden_db_packages:
            # Match actual import lines:  "import psycopg2" / "from psycopg2 ..."
            pattern = re.compile(
                rf"^\s*(import\s+{re.escape(pkg)}|from\s+{re.escape(pkg)})",
                re.MULTILINE,
            )
            matches = pattern.findall(source)
            self.assertFalse(
                matches,
                f"Database import for {pkg!r} found in kalshi_wx_shadow_client.py — "
                f"the scaffold must not contain any persistent-write capability. "
                f"Matching lines: {matches}",
            )


# ─────────────────────────────────────────────────────────────────────────────
# S5 — Credential presence alone does not activate
# ─────────────────────────────────────────────────────────────────────────────

class TestS5CredentialPresenceDoesNotActivate(unittest.TestCase):

    def test_S5_api_keys_present_but_flag_off_stays_disabled(self):
        """
        Even when ANTHROPIC_API_KEY and AI_INTEGRATIONS_ANTHROPIC_API_KEY are
        set in the environment (as they are in this Replit environment), the
        flag=False gate fires first and returns SHADOW_AGENT_DISABLED without
        any network activity.

        This test explicitly sets both key env vars to simulate a credentialed
        environment and confirms the flag is the only activation path.

        Assertions:
          1. result.passed is False
          2. "SHADOW_AGENT_DISABLED" in failure_reason
          3. No network call was made (strict mock confirms)
        """
        strict_mock = MagicMock()
        strict_mock.messages.create.side_effect = AssertionError(
            "Network call attempted despite flag being off and credentials present"
        )

        env_overrides = {
            "ANTHROPIC_API_KEY": "sk-fake-key-for-test",
            "AI_INTEGRATIONS_ANTHROPIC_API_KEY": "sk-ant-fake-key-for-test",
        }
        with patch("gate_engine.kalshi_wx_shadow_client._SHADOW_ENABLED", False):
            with patch.dict(os.environ, env_overrides):
                client = KalshiWxShadowResearchClient(sdk_client=strict_mock)
                result = client.research(
                    city="MIA", date="2026-08-10", run_id="run-s5"
                )

        self.assertFalse(result.passed)
        self.assertIn("SHADOW_AGENT_DISABLED", result.failure_reason,
                      f"Expected SHADOW_AGENT_DISABLED; got {result.failure_reason!r}")
        strict_mock.messages.create.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# S6 — Authority constants all False
# ─────────────────────────────────────────────────────────────────────────────

class TestS6AuthorityConstantsAllFalse(unittest.TestCase):

    def test_S6_can_execute_is_false(self):
        self.assertFalse(
            KalshiWxShadowResearchClient.CAN_EXECUTE,
            "CAN_EXECUTE must be False on KalshiWxShadowResearchClient",
        )

    def test_S6_production_authority_is_false(self):
        self.assertFalse(
            KalshiWxShadowResearchClient.PRODUCTION_AUTHORITY,
            "PRODUCTION_AUTHORITY must be False on KalshiWxShadowResearchClient",
        )

    def test_S6_user_output_authority_is_false(self):
        self.assertFalse(
            KalshiWxShadowResearchClient.USER_OUTPUT_AUTHORITY,
            "USER_OUTPUT_AUTHORITY must be False on KalshiWxShadowResearchClient",
        )

    def test_S6_assert_inert_passes_on_base_class(self):
        """assert_inert() must succeed on the base class with no arguments."""
        try:
            KalshiWxShadowResearchClient.assert_inert()
        except AssertionError as e:
            self.fail(f"assert_inert() raised AssertionError on base class: {e}")

    def test_S6_constants_are_bool_type(self):
        """Authority constants must be actual booleans, not truthy/falsy ints."""
        self.assertIs(type(KalshiWxShadowResearchClient.CAN_EXECUTE), bool)
        self.assertIs(type(KalshiWxShadowResearchClient.PRODUCTION_AUTHORITY), bool)
        self.assertIs(type(KalshiWxShadowResearchClient.USER_OUTPUT_AUTHORITY), bool)


# ─────────────────────────────────────────────────────────────────────────────
# S7 — Flag=True reaches SHADOW_CLIENT_NOT_WIRED
# ─────────────────────────────────────────────────────────────────────────────

class TestS7FlagOnReachesNotWired(unittest.TestCase):

    def test_S7_flag_on_returns_not_wired_failure(self):
        """
        With _SHADOW_ENABLED=True and the authority constants correctly False,
        research() passes gates 1 and 2 and hits gate 3: SHADOW_CLIENT_NOT_WIRED.

        This confirms:
          - The feature flag gate can be opened (it's not permanently locked)
          - The authority guard passes when constants are correctly False
          - The scaffold correctly reports it is not yet wired to agent behavior

        Assertions:
          1. result.passed is False
          2. result.shadow_failure_only is True
          3. "SHADOW_CLIENT_NOT_WIRED" in result.failure_reason
          4. "SHADOW_AGENT_DISABLED" is NOT in result.failure_reason (gate 1 passed)
          5. "AUTHORITY_VIOLATION" is NOT in result.failure_reason (gate 2 passed)
        """
        with patch("gate_engine.kalshi_wx_shadow_client._SHADOW_ENABLED", True):
            client = KalshiWxShadowResearchClient()
            result = client.research(city="LAX", date="2026-08-11", run_id="run-s7")

        self.assertFalse(result.passed)
        self.assertTrue(result.shadow_failure_only)
        self.assertIn(
            "SHADOW_CLIENT_NOT_WIRED", result.failure_reason,
            f"Expected SHADOW_CLIENT_NOT_WIRED; got {result.failure_reason!r}",
        )
        self.assertNotIn("SHADOW_AGENT_DISABLED", result.failure_reason)
        self.assertNotIn("AUTHORITY_VIOLATION", result.failure_reason)


# ─────────────────────────────────────────────────────────────────────────────
# S8 — Authority guard rejects subclass with True constant
# ─────────────────────────────────────────────────────────────────────────────

class TestS8AuthorityGuardRejectsSubclass(unittest.TestCase):

    def test_S8_subclass_with_can_execute_true_is_rejected(self):
        """
        A subclass that overrides CAN_EXECUTE=True must be rejected by the
        authority guard in research() even when the feature flag is on.

        This confirms the belt-and-suspenders authority check at call time
        catches subclasses that accidentally override a constant to True.

        Assertions:
          1. result.passed is False
          2. "SHADOW_CLIENT_AUTHORITY_VIOLATION" in result.failure_reason
          3. assert_inert() raises AssertionError on the subclass
        """
        class _BadSubclass(KalshiWxShadowResearchClient):
            CAN_EXECUTE = True  # Intentionally wrong — triggers the guard

        with patch("gate_engine.kalshi_wx_shadow_client._SHADOW_ENABLED", True):
            bad_client = _BadSubclass()
            result = bad_client.research(city="NYC", date="2026-08-08", run_id="run-s8")

        self.assertFalse(result.passed)
        self.assertIn(
            "SHADOW_CLIENT_AUTHORITY_VIOLATION", result.failure_reason,
            f"Expected AUTHORITY_VIOLATION; got {result.failure_reason!r}",
        )

        # assert_inert() must raise on the subclass
        with self.assertRaises(AssertionError):
            _BadSubclass.assert_inert()

    def test_S8_subclass_with_production_authority_true_is_rejected(self):
        """PRODUCTION_AUTHORITY=True on a subclass must also be caught."""
        class _BadSubclass(KalshiWxShadowResearchClient):
            PRODUCTION_AUTHORITY = True

        with patch("gate_engine.kalshi_wx_shadow_client._SHADOW_ENABLED", True):
            result = _BadSubclass().research(
                city="NYC", date="2026-08-08", run_id="run-s8b"
            )

        self.assertFalse(result.passed)
        self.assertIn("SHADOW_CLIENT_AUTHORITY_VIOLATION", result.failure_reason)


# ─────────────────────────────────────────────────────────────────────────────
# S9 — research() always returns ShadowValidationResult, never dict or None
# ─────────────────────────────────────────────────────────────────────────────

class TestS9ResearchAlwaysReturnsShadowValidationResult(unittest.TestCase):
    """
    Across every reachable scenario, research() must return a ShadowValidationResult
    with shadow_failure_only=True.  It must never return None, a dict, or any
    other type.
    """

    _SCENARIOS = [
        # (description, flag_value, subclass_override)
        ("flag=False, no sdk_client",       False, {}),
        ("flag=True, no sdk_client",         True,  {}),
        ("flag=False, sdk_client=strict",   False, {}),
    ]

    def _run_scenario(self, flag: bool, extra_class_attrs: dict) -> ShadowValidationResult:
        """Helper: patch flag, optionally build subclass, call research()."""
        if extra_class_attrs:
            klass = type(
                "_TestSubclass",
                (KalshiWxShadowResearchClient,),
                extra_class_attrs,
            )
        else:
            klass = KalshiWxShadowResearchClient

        strict_mock = MagicMock()
        strict_mock.messages.create.side_effect = AssertionError("should not be called")

        with patch("gate_engine.kalshi_wx_shadow_client._SHADOW_ENABLED", flag):
            return klass(sdk_client=strict_mock).research(
                city="NYC", date="2026-08-08", run_id="run-s9"
            )

    def test_S9_flag_false_returns_shadow_validation_result(self):
        result = self._run_scenario(flag=False, extra_class_attrs={})
        self.assertIsNotNone(result)
        self.assertIsInstance(result, ShadowValidationResult)
        self.assertNotIsInstance(result, dict)
        self.assertTrue(result.shadow_failure_only)

    def test_S9_flag_true_returns_shadow_validation_result(self):
        result = self._run_scenario(flag=True, extra_class_attrs={})
        self.assertIsNotNone(result)
        self.assertIsInstance(result, ShadowValidationResult)
        self.assertNotIsInstance(result, dict)
        self.assertTrue(result.shadow_failure_only)

    def test_S9_all_failures_have_shadow_failure_only_true(self):
        """Every return path currently produces shadow_failure_only=True."""
        for desc, flag, attrs in self._SCENARIOS:
            with self.subTest(desc=desc):
                result = self._run_scenario(flag=flag, extra_class_attrs=attrs)
                self.assertTrue(
                    result.shadow_failure_only,
                    f"Scenario {desc!r}: shadow_failure_only must be True, "
                    f"got {result.shadow_failure_only!r}",
                )


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main()
