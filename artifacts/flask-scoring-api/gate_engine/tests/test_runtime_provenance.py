"""
gate_engine/tests/test_runtime_provenance.py
WOW Runtime Provenance & Routing Governance v1.0 — Regression Suite

Covers:
  P01  Fully backend-verified WOW_BETTING_ENGINE run
  P02  PROJECT_CHAT fallback is explicit BACKEND_NOT_VERIFIED / FALLBACK_RUN
  P03  Partial required-capability failure blocks production verification
  P04  Local/reconstructed probability can never be production verified
  P05  Caller-supplied booleans / forged records cannot elevate (fail-closed)
  P06  Unconditional invariants present in every record
  P07  Gatekeeper row-level downgrade protection (gate 21)
  P08  verify_v16_result downgrade protection
  P09  verify_cc_envelope downgrade protection
  P10  enforce_no_upgrade run-level envelope protection
"""
from __future__ import annotations

import unittest

from gate_engine import runtime_provenance as rp
from gate_engine.runtime_provenance import (
    BACKEND_NOT_VERIFIED, PRODUCTION_BACKEND_VERIFIED,
    FALLBACK_CEILING, FALLBACK_RUN, PREFERRED_PRODUCTION_RUN,
    PREFERRED_HOST, PROJECT_CHAT,
    build_runtime_provenance, enforce_no_upgrade,
    is_provenance_blocker, provenance_blocker,
)
from gate_engine import full_model_gatekeeper as fmcg
from gate_engine.full_model_gatekeeper import (
    FINAL_APPROVED, MODEL_QUALIFIED_HOLD, QUAL_PASS, STATUS_COMPLETE,
    apply_gatekeeper, verify_cc_envelope, verify_v16_result,
)
from gate_engine.tests.test_full_model_gatekeeper import _base_row


def _verified_evidence() -> dict:
    return {"status": "VERIFIED", "verification_source": "REPLIT_PRODUCTION_ACTION"}


def _server_evidence() -> dict:
    return {
        "odds_gateway":  _verified_evidence(),
        "engine_health": {"status": "VERIFIED",
                          "verification_source": "REPLIT_PRODUCTION_SERVICE"},
    }


def _verified_context(**overrides) -> dict:
    ctx = {
        "requested_host": PREFERRED_HOST,
        "required_capabilities": ["odds_gateway", "engine_health"],
    }
    ctx.update(overrides)
    return ctx


def _build_verified(**ctx_overrides) -> dict:
    return build_runtime_provenance(
        _verified_context(**ctx_overrides),
        capability_evidence=_server_evidence(),
    )


class TestP01_VerifiedPreferredRun(unittest.TestCase):

    def test_verified_run(self):
        prov = _build_verified()
        self.assertEqual(prov["requested_host"], PREFERRED_HOST)
        self.assertEqual(prov["actual_host"], PREFERRED_HOST)
        self.assertTrue(prov["production_probability_verified"])
        self.assertEqual(prov["backend_verification_status"], PRODUCTION_BACKEND_VERIFIED)
        self.assertEqual(prov["model_run_status"], PREFERRED_PRODUCTION_RUN)
        self.assertFalse(prov["fallback_run"])
        self.assertIsNone(prov["fallback_reason"])
        self.assertEqual(prov["required_capabilities_unavailable"], [])
        self.assertIsNone(provenance_blocker(prov))

    def test_verified_run_ceiling_passthrough_no_upgrade(self):
        prov = _build_verified(lowest_ceiling="WATCH")
        self.assertEqual(prov["lowest_ceiling"], "WATCH")

    def test_nested_runtime_context_key(self):
        prov = build_runtime_provenance(
            {"runtime_context": _verified_context()},
            capability_evidence=_server_evidence(),
        )
        self.assertTrue(prov["production_probability_verified"])


class TestP02_ProjectChatFallback(unittest.TestCase):

    def test_project_chat_requested(self):
        prov = build_runtime_provenance({"requested_host": PROJECT_CHAT})
        self.assertEqual(prov["actual_host"], PROJECT_CHAT)
        self.assertFalse(prov["production_probability_verified"])
        self.assertEqual(prov["backend_verification_status"], BACKEND_NOT_VERIFIED)
        self.assertEqual(prov["model_run_status"], FALLBACK_RUN)
        self.assertTrue(prov["fallback_run"])
        self.assertEqual(prov["lowest_ceiling"], FALLBACK_CEILING)
        self.assertIsNotNone(prov["fallback_reason"])

    def test_preferred_host_unavailable_routes_to_project_chat(self):
        prov = build_runtime_provenance({"preferred_host_available": False})
        self.assertEqual(prov["actual_host"], PROJECT_CHAT)
        self.assertEqual(prov["fallback_reason"], "PREFERRED_HOST_UNAVAILABLE")
        self.assertEqual(prov["model_run_status"], FALLBACK_RUN)

    def test_verified_capabilities_do_not_rescue_project_chat(self):
        prov = build_runtime_provenance(
            _verified_context(actual_host=PROJECT_CHAT),
            capability_evidence=_server_evidence(),
        )
        self.assertFalse(prov["production_probability_verified"])
        self.assertEqual(prov["backend_verification_status"], BACKEND_NOT_VERIFIED)

    def test_unknown_host_treated_as_non_preferred(self):
        prov = build_runtime_provenance({"actual_host": "SOME_OTHER_GPT"})
        self.assertEqual(prov["actual_host"], PROJECT_CHAT)
        self.assertFalse(prov["production_probability_verified"])


class TestP03_PartialCapabilityFailure(unittest.TestCase):

    def test_one_unverified_capability_blocks(self):
        ev = _server_evidence()
        del ev["engine_health"]
        prov = build_runtime_provenance(_verified_context(), capability_evidence=ev)
        self.assertFalse(prov["production_probability_verified"])
        self.assertEqual(prov["required_capabilities_unavailable"], ["engine_health"])
        self.assertEqual(prov["required_capabilities_satisfied"], ["odds_gateway"])
        self.assertEqual(prov["backend_verification_status"], BACKEND_NOT_VERIFIED)
        self.assertEqual(prov["model_run_status"], "BACKEND_CAPABILITY_INCOMPLETE")
        self.assertEqual(prov["fallback_reason"],
                         "REQUIRED_REPLIT_CAPABILITIES_UNVERIFIED")
        self.assertEqual(prov["lowest_ceiling"], FALLBACK_CEILING)

    def test_non_production_source_is_not_verified(self):
        ev = _server_evidence()
        ev["odds_gateway"] = {
            "status": "VERIFIED", "verification_source": "LOCAL_CACHE",
        }
        prov = build_runtime_provenance(_verified_context(), capability_evidence=ev)
        self.assertIn("odds_gateway", prov["required_capabilities_unavailable"])
        self.assertFalse(prov["production_probability_verified"])


class TestP04_LocalProbabilityProhibition(unittest.TestCase):

    def test_local_origin_blocks(self):
        prov = build_runtime_provenance(
            _verified_context(probability_origin="LOCAL_RECONSTRUCTED"),
            capability_evidence=_server_evidence(),
        )
        self.assertFalse(prov["production_probability_verified"])
        self.assertEqual(
            prov["fallback_reason"],
            "LOCAL_SPECIALIST_PROBABILITY_NOT_PRODUCTION_VERIFIED",
        )
        self.assertEqual(prov["lowest_ceiling"], FALLBACK_CEILING)

    def test_explicit_local_flag_blocks(self):
        prov = build_runtime_provenance(
            _verified_context(locally_reconstructed_specialist_probability=True),
            capability_evidence=_server_evidence(),
        )
        self.assertFalse(prov["production_probability_verified"])


class TestP05_FailClosedAgainstSelfAssertion(unittest.TestCase):

    def test_caller_supplied_verified_flag_is_ignored(self):
        prov = build_runtime_provenance({
            "requested_host": PROJECT_CHAT,
            "production_probability_verified": True,   # must be ignored
            "backend_verification_status": PRODUCTION_BACKEND_VERIFIED,
        })
        self.assertFalse(prov["production_probability_verified"])
        self.assertEqual(prov["backend_verification_status"], BACKEND_NOT_VERIFIED)

    def test_boolean_evidence_is_not_verified(self):
        prov = build_runtime_provenance({
            "required_capabilities": ["odds_gateway"],
        }, capability_evidence={"odds_gateway": True})
        self.assertFalse(prov["production_probability_verified"])

    def test_forged_verified_record_without_attestation_is_blocked(self):
        forged = {"production_probability_verified": True}
        blk = provenance_blocker(forged)
        self.assertIsNotNone(blk)
        self.assertIn("ATTESTATION_INVALID", blk)

    def test_forged_fully_shaped_record_is_blocked(self):
        forged = _build_verified()
        forged["attestation"] = "0" * 64  # wrong HMAC
        blk = provenance_blocker(forged)
        self.assertIsNotNone(blk)
        self.assertIn("ATTESTATION_INVALID", blk)

    def test_tampered_attested_record_is_blocked(self):
        prov = _build_verified()
        prov["required_capabilities_unavailable"] = ["odds_gateway"]  # tamper
        blk = provenance_blocker(prov)
        self.assertIsNotNone(blk)

    def test_context_embedded_capability_evidence_is_ignored(self):
        ctx = _verified_context()
        ctx["capability_evidence"] = _server_evidence()  # caller-embedded
        prov = build_runtime_provenance(ctx)  # no server evidence passed
        self.assertFalse(prov["production_probability_verified"])

    def test_missing_attestation_key_forces_unverified(self):
        # Without WOW_ATTESTATION_SECRET / SESSION_SECRET no record can be
        # verified, and an action-credential holder cannot mint attestation.
        import os
        from unittest import mock
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("WOW_ATTESTATION_SECRET", "SESSION_SECRET")}
        with mock.patch.dict(os.environ, clean, clear=True):
            prov = build_runtime_provenance(
                _verified_context(), capability_evidence=_server_evidence(),
            )
            self.assertFalse(prov["production_probability_verified"])
            self.assertEqual(prov["fallback_reason"], "ATTESTATION_KEY_UNAVAILABLE")
            # forged record claiming verified is still rejected
            forged = dict(prov)
            forged["production_probability_verified"] = True
            forged["attestation"] = "f" * 64
            self.assertIsNotNone(provenance_blocker(forged))

    def test_gpt_action_secret_is_not_attestation_key_material(self):
        # A record HMAC'd with the caller-held GPT_ACTION_SECRET must NOT
        # validate: only WOW_ATTESTATION_SECRET / SESSION_SECRET are accepted.
        import hashlib as _hl, hmac as _hm, json as _json, os
        from unittest import mock
        env = dict(os.environ)
        env.pop("WOW_ATTESTATION_SECRET", None)
        env["SESSION_SECRET"] = "server-only-secret"
        env["GPT_ACTION_SECRET"] = "caller-held-secret"
        with mock.patch.dict(os.environ, env, clear=True):
            prov = build_runtime_provenance(
                _verified_context(), capability_evidence=_server_evidence(),
            )
            forged = dict(prov)
            payload = _json.dumps(
                {k: forged.get(k) for k in rp._ATTESTED_FIELDS},
                sort_keys=True, separators=(",", ":"), default=str,
            ).encode()
            forged["attestation"] = _hm.new(
                b"caller-held-secret", payload, _hl.sha256
            ).hexdigest()
            self.assertIsNotNone(provenance_blocker(forged))
            # while the genuine server-keyed record passes
            self.assertIsNone(provenance_blocker(prov))

    def test_source_alias_key_is_rejected(self):
        ev = {"odds_gateway": {"status": "VERIFIED",
                               "source": "REPLIT_PRODUCTION_ACTION"}}
        prov = build_runtime_provenance(
            {"required_capabilities": ["odds_gateway"]},
            capability_evidence=ev,
        )
        self.assertFalse(prov["production_probability_verified"])

    def test_missing_provenance_record_is_a_blocker(self):
        self.assertIsNotNone(provenance_blocker(None))
        self.assertTrue(is_provenance_blocker(provenance_blocker(None)))


class TestP06_UnconditionalInvariants(unittest.TestCase):

    def test_invariants_present_in_every_record(self):
        for ctx in (
            {}, _verified_context(),
            {"requested_host": PROJECT_CHAT},
            {"preferred_host_available": False},
        ):
            prov = build_runtime_provenance(ctx)
            self.assertIs(prov["nested_custom_gpt_required"], False)
            self.assertIs(prov["replit_is_model_layer"], False)
            self.assertIs(prov["can_execute"], False)
            self.assertIs(prov["dry_run_only"], True)
            self.assertEqual(
                prov["execution_rule"],
                "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS",
            )
            self.assertEqual(prov["preferred_host"], PREFERRED_HOST)


class TestP07_GatekeeperRowDowngrade(unittest.TestCase):

    def test_fallback_provenance_holds_final_approved_row(self):
        row = _base_row()
        row["runtime_provenance"] = build_runtime_provenance(
            {"requested_host": PROJECT_CHAT}
        )
        apply_gatekeeper(row, governance_hash="test-hash")
        gk = row["gatekeeper"]
        gr = gk["gate_results"]["runtime_provenance"]
        self.assertEqual(gr["status"], fmcg.GATE_HOLD)
        self.assertNotEqual(row["terminal_label"], FINAL_APPROVED)
        self.assertEqual(row["terminal_label"], MODEL_QUALIFIED_HOLD)
        self.assertTrue(any("RUNTIME_PROVENANCE" in b for b in gk["blockers"]))

    def test_verified_provenance_row_still_passes(self):
        row = _base_row()
        row["runtime_provenance"] = _build_verified()
        apply_gatekeeper(row, governance_hash="test-hash")
        gk = row["gatekeeper"]
        self.assertEqual(gk["gate_results"]["runtime_provenance"]["status"],
                         fmcg.GATE_PASS)
        self.assertEqual(gk["qualification_result"], QUAL_PASS)
        self.assertEqual(row["terminal_label"], FINAL_APPROVED)

    def test_row_without_provenance_record_is_skip_not_blocked(self):
        row = _base_row()
        apply_gatekeeper(row, governance_hash="test-hash")
        gk = row["gatekeeper"]
        self.assertEqual(gk["gate_results"]["runtime_provenance"]["status"],
                         fmcg.GATE_SKIP)
        self.assertEqual(gk["qualification_result"], QUAL_PASS)


class TestP08_V16Downgrade(unittest.TestCase):

    def _passing_result(self) -> dict:
        return {
            "final_label": FINAL_APPROVED,
            "skill_results": [{
                "gatekeeper": {
                    "qualification_result": QUAL_PASS,
                    "full_model_status":    STATUS_COMPLETE,
                    "can_execute":          False,
                },
            }],
        }

    def test_fallback_provenance_downgrades_v16_final_approved(self):
        result = self._passing_result()
        result["runtime_provenance"] = build_runtime_provenance(
            {"requested_host": PROJECT_CHAT}
        )
        verify_v16_result(result)
        self.assertEqual(result["final_label"], MODEL_QUALIFIED_HOLD)
        self.assertTrue(any("RUNTIME_PROVENANCE" in b for b in result["blockers"]))
        self.assertEqual(
            result["gatekeeper_enforcement"]["reason"],
            "runtime_provenance_backend_not_verified",
        )

    def test_verified_provenance_preserves_v16_final_approved(self):
        result = self._passing_result()
        result["runtime_provenance"] = _build_verified()
        verify_v16_result(result)
        self.assertEqual(result["final_label"], FINAL_APPROVED)

    def test_absent_provenance_preserves_existing_v16_behavior(self):
        result = self._passing_result()
        verify_v16_result(result)
        self.assertEqual(result["final_label"], FINAL_APPROVED)


class TestP09_CCEnvelopeDowngrade(unittest.TestCase):

    def _passing_envelope(self) -> dict:
        return {
            "engine_label": FINAL_APPROVED,
            "engine_result": {
                "gatekeeper": {
                    "qualification_result": QUAL_PASS,
                    "full_model_status":    STATUS_COMPLETE,
                    "can_execute":          False,
                },
            },
            "cc_blockers": [],
        }

    def test_fallback_provenance_downgrades_envelope(self):
        env = self._passing_envelope()
        env["runtime_provenance"] = build_runtime_provenance(
            {"preferred_host_available": False}
        )
        ok = verify_cc_envelope(env)
        self.assertFalse(ok)
        self.assertEqual(env["engine_label"], MODEL_QUALIFIED_HOLD)
        self.assertTrue(any("RUNTIME_PROVENANCE" in b for b in env["cc_blockers"]))

    def test_verified_provenance_envelope_survives(self):
        env = self._passing_envelope()
        env["runtime_provenance"] = _build_verified()
        self.assertTrue(verify_cc_envelope(env))
        self.assertEqual(env["engine_label"], FINAL_APPROVED)


class TestP10_EnforceNoUpgrade(unittest.TestCase):

    def test_fallback_run_level_result_is_capped(self):
        prov = build_runtime_provenance({"requested_host": PROJECT_CHAT})
        result = {"final_label": FINAL_APPROVED}
        enforce_no_upgrade(result, prov)
        self.assertEqual(result["final_label"], MODEL_QUALIFIED_HOLD)
        self.assertTrue(any(is_provenance_blocker(b) for b in result["blockers"]))

    def test_verified_run_level_result_untouched(self):
        prov = _build_verified()
        result = {"final_label": FINAL_APPROVED}
        enforce_no_upgrade(result, prov)
        self.assertEqual(result["final_label"], FINAL_APPROVED)
        self.assertNotIn("blockers", result)

    def test_lower_labels_never_upgraded(self):
        prov = build_runtime_provenance({"requested_host": PROJECT_CHAT})
        result = {"final_label": "REJECT_NO_EDGE"}
        enforce_no_upgrade(result, prov)
        self.assertEqual(result["final_label"], "REJECT_NO_EDGE")

    def test_blocker_not_duplicated(self):
        prov = build_runtime_provenance({"requested_host": PROJECT_CHAT})
        result = {"final_label": FINAL_APPROVED}
        enforce_no_upgrade(result, prov)
        enforce_no_upgrade(result, prov)
        self.assertEqual(
            sum(1 for b in result["blockers"] if is_provenance_blocker(b)), 1
        )


class TestP11_ServerAuthoritativeRouteProvenance(unittest.TestCase):
    """Forged/omitted request fields cannot obtain verified status."""

    def _build(self, route="wow_daily_scan", principal="GPT_ACTION", ctx=None):
        from gate_engine.runtime_provenance import build_route_provenance
        return build_route_provenance(
            route, action_principal=principal, caller_context=ctx,
        )

    def test_omitted_required_capabilities_still_uses_route_registry(self):
        prov = self._build(ctx={})   # caller sends nothing
        self.assertEqual(prov["required_capabilities"],
                         sorted(rp.ROUTE_CAPABILITY_REGISTRY["wow_daily_scan"]))

    def test_caller_cannot_shrink_required_capabilities(self):
        prov = self._build(ctx={"required_capabilities": []})
        self.assertEqual(prov["required_capabilities"],
                         sorted(rp.ROUTE_CAPABILITY_REGISTRY["wow_daily_scan"]))

    def test_caller_extra_capability_is_added_and_fails_closed(self):
        prov = self._build(ctx={"required_capabilities": ["nonexistent_backend"]})
        self.assertIn("nonexistent_backend", prov["required_capabilities"])
        self.assertIn("nonexistent_backend",
                      prov["required_capabilities_unavailable"])
        self.assertFalse(prov["production_probability_verified"])

    def test_caller_supplied_capability_evidence_is_ignored(self):
        prov = self._build(ctx={
            "required_capabilities": ["nonexistent_backend"],
            "capability_evidence": {"nonexistent_backend": _verified_evidence()},
        })
        self.assertIn("nonexistent_backend",
                      prov["required_capabilities_unavailable"])

    def test_caller_cannot_assert_actual_host(self):
        prov = self._build(principal=None,
                           ctx={"actual_host": PREFERRED_HOST,
                                "requested_host": PREFERRED_HOST})
        self.assertEqual(prov["actual_host"], PROJECT_CHAT)
        self.assertFalse(prov["production_probability_verified"])
        self.assertIsNotNone(provenance_blocker(prov))

    def test_caller_verified_boolean_is_ignored_at_route_level(self):
        prov = self._build(principal=None,
                           ctx={"production_probability_verified": True})
        self.assertFalse(prov["production_probability_verified"])

    def test_general_scoring_api_principal_is_not_preferred_host(self):
        # A holder of the general SCORING_API_KEY authenticates, but is NOT
        # the designated Custom-GPT Action — must be fallback, never verified.
        prov = self._build(principal="SCORING_API", ctx={})
        self.assertEqual(prov["actual_host"], PROJECT_CHAT)
        self.assertFalse(prov["production_probability_verified"])
        self.assertIn("NON_ACTION_CREDENTIAL", prov["fallback_reason"] or "")
        self.assertIsNotNone(provenance_blocker(prov))

    def test_only_gpt_action_principal_can_reach_preferred_host(self):
        prov = self._build(principal="GPT_ACTION", ctx={})
        self.assertEqual(prov["actual_host"], PREFERRED_HOST)
        prov2 = self._build(principal="gpt_action", ctx={})  # exact match only
        self.assertEqual(prov2["actual_host"], PROJECT_CHAT)

    def test_ungoverned_route_fails_closed(self):
        prov = self._build(route="some_unregistered_route", ctx={})
        self.assertFalse(prov["production_probability_verified"])
        self.assertIn("UNGOVERNED_ROUTE", prov["fallback_reason"] or "")

    def test_caller_can_still_downgrade_to_project_chat(self):
        prov = self._build(ctx={"requested_host": PROJECT_CHAT})
        self.assertEqual(prov["actual_host"], PROJECT_CHAT)
        self.assertFalse(prov["production_probability_verified"])

    def test_authenticated_route_with_probeable_caps_verifies(self):
        # engine_health always probeable in-process; odds/database depend on env
        import os
        if not any((os.environ.get(k) or "").strip()
                   for k in ("ODDS_API_KEY_100K", "ODDS_API_PAID_KEY",
                             "ODDS_API_FREE_KEY")):
            self.skipTest("no odds api key configured in this environment")
        if not (os.environ.get("DATABASE_URL") or "").strip():
            self.skipTest("no DATABASE_URL configured in this environment")
        prov = self._build(ctx={})
        self.assertTrue(prov["production_probability_verified"])
        self.assertIsNone(provenance_blocker(prov))

    def test_route_record_is_attested(self):
        prov = self._build(ctx={})
        self.assertIn("attestation", prov)
        # tampering breaks it
        prov2 = dict(prov)
        prov2["production_probability_verified"] = True
        prov2["required_capabilities"] = ["forged_capability"]
        prov2["required_capabilities_satisfied"] = ["forged_capability"]
        prov2["required_capabilities_unavailable"] = []
        self.assertIsNotNone(provenance_blocker(prov2))


class TestP11b_OperationalProbes(unittest.TestCase):
    """Configuration presence alone must never verify a backend."""

    def setUp(self):
        from gate_engine import runtime_capability_probe as rcp
        self.rcp = rcp
        rcp._success_cache.clear()
        self.addCleanup(rcp._success_cache.clear)

    def test_database_configured_but_unreachable_is_unverified(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {
            "DATABASE_URL": "postgresql://u:p@192.0.2.1:5432/nope",  # TEST-NET
        }):
            with mock.patch("psycopg2.connect", side_effect=Exception("down")):
                ev = self.rcp.probe_required_capabilities(["database"])
        self.assertNotIn("database", ev)

    def test_odds_key_configured_but_gateway_rejects_is_unverified(self):
        import os
        from unittest import mock

        class _Resp:
            status_code = 401
        with mock.patch.dict(os.environ, {"ODDS_API_KEY_100K": "bad-key"}):
            with mock.patch("requests.get", return_value=_Resp()):
                ev = self.rcp.probe_required_capabilities(["odds_gateway"])
        self.assertNotIn("odds_gateway", ev)

    def test_odds_gateway_timeout_is_unverified_and_not_cached(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"ODDS_API_KEY_100K": "k"}):
            with mock.patch("requests.get", side_effect=Exception("timeout")):
                ev = self.rcp.probe_required_capabilities(["odds_gateway"])
        self.assertNotIn("odds_gateway", ev)
        self.assertNotIn("odds_gateway", self.rcp._success_cache)

    def test_live_gateway_200_is_verified(self):
        import os
        from unittest import mock

        class _Resp:
            status_code = 200
        with mock.patch.dict(os.environ, {"ODDS_API_KEY_100K": "k"}):
            with mock.patch("requests.get", return_value=_Resp()):
                ev = self.rcp.probe_required_capabilities(["odds_gateway"])
        self.assertIn("odds_gateway", ev)
        self.assertEqual(ev["odds_gateway"]["status"], "VERIFIED")

    def test_unknown_capability_has_no_probe(self):
        ev = self.rcp.probe_required_capabilities(["mystery_backend"])
        self.assertEqual(ev, {})


class TestP12_CCOrchestratorPropagation(unittest.TestCase):
    """The one run-level record reaches every CC envelope pre-gatekeeper."""

    def _run_cc(self, prov):
        from gate_engine.command_center.orchestrator import run_command_center
        cand = {
            "candidate_id": "c1", "player": "Test Player", "sport": "WNBA",
            "market_family": "WNBA_PROP", "prop": "points", "line": 15.5,
            "side": "MORE", "target_date": "2026-08-19",
        }
        eng = {"c1": {
            "final_label": FINAL_APPROVED,
            "engine_label": FINAL_APPROVED,
            "gatekeeper": {
                "qualification_result": QUAL_PASS,
                "full_model_status":    STATUS_COMPLETE,
                "can_execute":          False,
            },
        }}
        return run_command_center(
            raw_candidates=[cand], engine_results=eng,
            session_id="s1", run_id="r1", target_date="2026-08-19",
            runtime_provenance=prov,
        )

    def _envelopes(self, result):
        for key in ("results", "candidates", "envelopes", "rows"):
            if isinstance(result.get(key), list):
                return result[key]
        return []

    def test_fallback_provenance_downgrades_cc_final_approved(self):
        prov = build_runtime_provenance({"requested_host": PROJECT_CHAT})
        result = self._run_cc(prov)
        envs = self._envelopes(result)
        self.assertTrue(envs, f"no envelopes found in result keys={list(result)}")
        for env in envs:
            self.assertEqual(env.get("runtime_provenance"), prov)
            self.assertNotEqual(env.get("engine_label"), FINAL_APPROVED)

    def test_verified_provenance_preserves_cc_labels(self):
        prov = _build_verified()
        result = self._run_cc(prov)
        for env in self._envelopes(result):
            self.assertEqual(env.get("runtime_provenance"), prov)
            blockers = env.get("cc_blockers") or []
            self.assertFalse(any("RUNTIME_PROVENANCE" in str(b) for b in blockers))


if __name__ == "__main__":
    unittest.main()
