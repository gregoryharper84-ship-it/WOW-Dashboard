from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
V17 = ROOT / "v17"
sys.path.insert(0, str(V17))

CONDUCTOR_PATH = V17 / "ecosystem_conductor.py"
PROBES_PATH = V17 / "ecosystem_live_probes.py"
REGISTRY_PATH = V17 / "ecosystem-registry.json"
CONFIG_PATH = V17 / "ecosystem-live-probes.json"

conductor_spec = importlib.util.spec_from_file_location(
    "ecosystem_conductor", CONDUCTOR_PATH
)
assert conductor_spec and conductor_spec.loader
conductor = importlib.util.module_from_spec(conductor_spec)
conductor_spec.loader.exec_module(conductor)
sys.modules["ecosystem_conductor"] = conductor

probe_spec = importlib.util.spec_from_file_location(
    "ecosystem_live_probes", PROBES_PATH
)
assert probe_spec and probe_spec.loader
probes = importlib.util.module_from_spec(probe_spec)
sys.modules["ecosystem_live_probes"] = probes
probe_spec.loader.exec_module(probes)


def load_registry():
    return json.loads(REGISTRY_PATH.read_text())


def load_config():
    return json.loads(CONFIG_PATH.read_text())


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def base_env():
    return {
        "GITHUB_REPOSITORY": "gregoryharper84-ship-it/WOW-Dashboard",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_TOKEN": "github-token",
        "WOW_BACKEND_ORIGIN": "https://example.test",
        "WOW_ACTION_API_KEY": "action-key",
        "RENDER_GIT_COMMIT": "b" * 40,
        "SUPABASE_URL": "https://supabase.test",
        "SUPABASE_SERVICE_KEY": "supabase-key",
        "WOW_USER_REQUEST_RECEIPT": "request.json",
        "WOW_CONDUCTOR_RECEIPT": "conductor.json",
        "WOW_PRODUCT_ORCHESTRATION_RECEIPT": "product.json",
        "WOW_SCOUT_RECEIPT": "scout.json",
        "WOW_LLP_ROUTE_RECEIPT": "llp.json",
        "WOW_KALSHI_WEATHER_RECEIPT": "kalshi.json",
        "WOW_PERSISTENCE_RECEIPT": "persistence.json",
        "WOW_SYSTEMS_INTELLIGENCE_RECEIPT": "systems.json",
        "WOW_ENGINEERING_HANDOFF_RECEIPT": "engineering.json",
        "WOW_INDEPENDENT_VERIFICATION_RECEIPT": "independent.json",
        "WOW_USER_RESPONSE_RECEIPT": "response.json",
    }


def receipt_payloads():
    return {
        "request.json": {"accepted": True},
        "conductor.json": {
            "can_execute": False,
            "terminal_authority": "V17_TERMINAL_REDUCER",
        },
        "product.json": {"reconciliation_pass": True},
        "scout.json": {"completion_status": "COMPLETE", "can_execute": False},
        "llp.json": {"reconciliation_pass": True, "can_execute": False},
        "kalshi.json": {"can_execute": False},
        "persistence.json": {"durable_state_present": True, "can_execute": False},
        "systems.json": {
            "connection_reconciliation_complete": True,
            "can_execute": False,
            "work_items": [],
        },
        "engineering.json": {
            "implementation_receipt_present": True,
            "can_execute": False,
        },
        "independent.json": {
            "verification_state": "VERIFIED",
            "can_execute": False,
        },
        "response.json": {
            "product_reconciliation_pass": True,
            "can_execute": False,
        },
    }


def make_reader(payloads):
    def read_text(path):
        if path not in payloads:
            raise FileNotFoundError(path)
        return json.dumps(payloads[path])

    return read_text


def success_opener(required_workflows):
    def opener(req, timeout=0):
        url = req.full_url
        if "api.github.com" in url and "/actions/runs" in url:
            return FakeResponse(
                {
                    "workflow_runs": [
                        {
                            "name": name,
                            "status": "completed",
                            "conclusion": "success",
                            "run_number": index + 1,
                        }
                        for index, name in enumerate(required_workflows)
                    ]
                }
            )
        if "/rest/v1/wow_action_invocation_receipts" in url:
            return FakeResponse(
                [
                    {
                        "invocation_id": "inv-1",
                        "route": "/score-prop",
                        "operation_id": "scoreWowPickRequest",
                        "http_status": 200,
                        "caller_class": "CHATGPT_ACTION",
                        "occurred_at": "2026-10-07T18:00:00Z",
                        "can_execute": False,
                    }
                ]
            )
        if "/rest/v1/wow_prop_action_canary_receipts" in url:
            return FakeResponse(
                [
                    {
                        "receipt_id": "canary-1",
                        "prediction_id": "prediction-1",
                        "sport": "MLB",
                        "stat_type": "PITCHER_STRIKEOUTS",
                        "reconciliation_status": "PASS",
                        "created_at": "2026-10-07T18:00:00Z",
                        "can_execute": False,
                    }
                ]
            )
        if url.endswith("/health"):
            return FakeResponse(
                {
                    "status": "ok",
                    "runtime_generation": "V17_ACTIVE",
                    "can_execute": False,
                }
            )
        if url.endswith("/governance"):
            return FakeResponse(
                {
                    "runtime_generation": "V17_ACTIVE",
                    "terminal_authority": "V17_TERMINAL_REDUCER",
                    "can_execute": False,
                }
            )
        if url.endswith("/v17/host-contract"):
            return FakeResponse(
                {
                    "runtime_generation": "V17_ACTIVE",
                    "terminal_authority": "V17_TERMINAL_REDUCER",
                    "can_execute": False,
                }
            )
        raise AssertionError(url)

    return opener


class EcosystemLiveProbeTests(unittest.TestCase):
    def test_probe_config_covers_every_registry_component_and_handoff(self):
        registry = load_registry()
        config = load_config()

        self.assertEqual([], probes.validate_probe_config(registry, config))
        self.assertEqual(
            set(registry["components"]),
            set(config["component_bindings"]),
        )
        self.assertEqual(
            set(registry["handoffs"]),
            set(config["handoff_bindings"]),
        )

    def test_aggregate_status_is_fail_closed(self):
        self.assertEqual(
            "FAIL",
            probes._aggregate_status(["PASS", "DEGRADED", "FAIL", "UNKNOWN"]),
        )
        self.assertEqual(
            "BLOCKED",
            probes._aggregate_status(["PASS", "UNKNOWN", "BLOCKED"]),
        )
        self.assertEqual("UNKNOWN", probes._aggregate_status([]))

    def test_receipt_file_missing_fails_closed_as_unknown(self):
        receipt = probes.probe_receipt_file(
            "MISSING",
            {"type": "receipt_file", "path_env": "MISSING_PATH"},
            env={},
        )

        self.assertEqual("UNKNOWN", receipt.status)
        self.assertIn("PROBE_RECEIPT_PATH_MISSING", receipt.typed_failure)
        self.assertFalse(receipt.can_execute)

    def test_receipt_file_contract_mismatch_is_fail(self):
        receipt = probes.probe_receipt_file(
            "BAD",
            {
                "type": "receipt_file",
                "path": "bad.json",
                "predicates": {"can_execute": False},
            },
            env={},
            read_text=lambda _: json.dumps({"can_execute": True}),
        )

        self.assertEqual("FAIL", receipt.status)
        self.assertEqual("PROBE_RECEIPT_CONTRACT_MISMATCH", receipt.typed_failure)

    def test_http_probe_missing_bearer_is_blocked(self):
        receipt = probes.probe_http_json(
            "HOST",
            {
                "type": "http_json",
                "url": "https://example.test/health",
                "bearer_env": "MISSING_TOKEN",
            },
            env={},
        )

        self.assertEqual("BLOCKED", receipt.status)
        self.assertEqual("PROBE_AUTHENTICATION", receipt.first_failing_boundary)

    def test_http_probe_contract_predicate_is_enforced(self):
        receipt = probes.probe_http_json(
            "HOST",
            {
                "type": "http_json",
                "url": "https://example.test/health",
                "predicates": {"can_execute": False},
            },
            env={},
            opener=lambda req, timeout=0: FakeResponse({"can_execute": True}),
        )

        self.assertEqual("FAIL", receipt.status)
        self.assertEqual("PROBE_CONTRACT_MISMATCH", receipt.typed_failure)

    def test_github_exact_head_requires_all_workflows(self):
        spec = {
            "type": "github_exact_head",
            "repository": "gregoryharper84-ship-it/WOW-Dashboard",
            "sha": "a" * 40,
            "bearer_env": "GITHUB_TOKEN",
            "required_workflows": ["one", "two"],
        }

        receipt = probes.probe_github_exact_head(
            "GITHUB",
            spec,
            env={"GITHUB_TOKEN": "token"},
            opener=lambda req, timeout=0: FakeResponse(
                {
                    "workflow_runs": [
                        {
                            "name": "one",
                            "status": "completed",
                            "conclusion": "success",
                            "run_number": 1,
                        }
                    ]
                }
            ),
        )

        self.assertEqual("UNKNOWN", receipt.status)
        self.assertEqual(
            ["two"],
            receipt.details["missing_workflows"],
        )
        self.assertEqual("GITHUB_EXACT_HEAD_PROOF_INCOMPLETE", receipt.typed_failure)

    def test_github_exact_head_failed_required_workflow_is_fail(self):
        spec = {
            "type": "github_exact_head",
            "repository": "gregoryharper84-ship-it/WOW-Dashboard",
            "sha": "a" * 40,
            "bearer_env": "GITHUB_TOKEN",
            "required_workflows": ["one"],
        }

        receipt = probes.probe_github_exact_head(
            "GITHUB",
            spec,
            env={"GITHUB_TOKEN": "token"},
            opener=lambda req, timeout=0: FakeResponse(
                {
                    "workflow_runs": [
                        {
                            "name": "one",
                            "status": "completed",
                            "conclusion": "failure",
                            "run_number": 1,
                        }
                    ]
                }
            ),
        )

        self.assertEqual("FAIL", receipt.status)
        self.assertEqual("GITHUB_REQUIRED_WORKFLOW_FAILED", receipt.typed_failure)

    def test_supabase_empty_result_is_unknown_not_pass(self):
        receipt = probes.probe_supabase_receipt(
            "DB",
            {
                "type": "supabase_receipt",
                "table": "wow_predictions",
            },
            env={
                "SUPABASE_URL": "https://supabase.test",
                "SUPABASE_SERVICE_KEY": "key",
            },
            opener=lambda req, timeout=0: FakeResponse([]),
        )

        self.assertEqual("UNKNOWN", receipt.status)
        self.assertEqual("DURABLE_RECEIPT_NOT_FOUND", receipt.typed_failure)

    def test_render_runtime_requires_deployed_sha_identity(self):
        receipt = probes.probe_render_runtime(
            "RENDER",
            {
                "type": "render_runtime",
                "origin_env": "WOW_BACKEND_ORIGIN",
                "bearer_env": "WOW_ACTION_API_KEY",
                "require_deployed_sha": True,
            },
            env={
                "WOW_BACKEND_ORIGIN": "https://example.test",
                "WOW_ACTION_API_KEY": "key",
            },
            opener=lambda req, timeout=0: FakeResponse(
                {
                    "runtime_generation": "V17_ACTIVE",
                    "terminal_authority": "V17_TERMINAL_REDUCER",
                    "can_execute": False,
                }
            ),
        )

        self.assertEqual("UNKNOWN", receipt.status)
        self.assertIn("PROBE_DEPLOYED_SHA_MISSING", receipt.typed_failure)

    def test_live_evaluation_can_prove_all_three_golden_paths(self):
        registry = load_registry()
        config = load_config()
        env = base_env()
        required = config["probes"]["GITHUB_EXACT_HEAD"]["required_workflows"]

        result = probes.evaluate_live(
            registry,
            config,
            env=env,
            opener=success_opener(required),
            read_text=make_reader(receipt_payloads()),
        )

        self.assertEqual("READY", result["ecosystem_status"])
        self.assertFalse(result["safe_hold_required"])
        self.assertFalse(result["can_execute"])
        self.assertEqual("V17_TERMINAL_REDUCER", result["terminal_authority"])
        self.assertEqual([], result["probe_failures"])
        for path in result["evaluation"]["golden_paths"].values():
            self.assertEqual("READY", path["status"])

    def test_missing_persistence_proof_prevents_false_green(self):
        registry = load_registry()
        config = load_config()
        env = base_env()
        env.pop("WOW_PERSISTENCE_RECEIPT")
        required = config["probes"]["GITHUB_EXACT_HEAD"]["required_workflows"]

        result = probes.evaluate_live(
            registry,
            config,
            env=env,
            opener=success_opener(required),
            read_text=make_reader(receipt_payloads()),
        )

        self.assertEqual("SAFE_HOLD", result["ecosystem_status"])
        self.assertTrue(result["safe_hold_required"])
        self.assertEqual(
            "UNKNOWN",
            result["observed_state"]["handoffs"][
                "TERMINAL_REDUCER_TO_PERSISTENCE"
            ],
        )
        self.assertEqual(
            "NOT_READY",
            result["evaluation"]["golden_paths"]["WOW_PROP"]["status"],
        )
        self.assertEqual(
            "NOT_READY",
            result["evaluation"]["golden_paths"]["LLP_TEAM_EVENT"]["status"],
        )

    def test_work_items_are_loaded_from_systems_intelligence_receipt(self):
        registry = load_registry()
        config = load_config()
        env = base_env()
        payloads = receipt_payloads()
        payloads["systems.json"]["work_items"] = [
            {
                "work_item_id": "work-1",
                "request_id": "request-1",
                "objective_id": "objective-1",
                "candidate_id": "candidate-1",
                "source": "USER",
                "current_owner": "WOW_BETTING_INTELLIGENCE",
                "next_owner": "WOW_BETTING_ENGINE",
                "state": "ROUTED",
                "blocking_reason": None,
                "evidence_refs": ["receipt://one"],
                "specialist_route": "WOW_PROP",
                "verification_state": "NOT_REQUIRED",
                "terminal_state": None,
                "authority_domain": "PRODUCT_INTELLIGENCE",
                "change_class": "NONE",
                "decision_right": "ROUTE_CANDIDATE",
                "required_verifier": "NONE",
                "promotion_state": "NOT_APPLICABLE",
                "updated_at": "2099-01-01T00:00:00Z",
                "lease_expires_at": "2099-01-01T01:00:00Z",
            }
        ]
        required = config["probes"]["GITHUB_EXACT_HEAD"]["required_workflows"]

        result = probes.evaluate_live(
            registry,
            config,
            env=env,
            opener=success_opener(required),
            read_text=make_reader(payloads),
        )

        self.assertEqual("READY", result["ecosystem_status"])
        work = result["evaluation"]["work_conservation"]
        self.assertEqual(1, work["items_in"])
        self.assertEqual(1, work["items_active"])

    def test_invalid_config_fails_before_any_probe_runs(self):
        registry = load_registry()
        config = load_config()
        config["handoff_bindings"].pop("PRODUCT_TO_LLP")

        result = probes.evaluate_live(
            registry,
            config,
            env={},
            opener=lambda req, timeout=0: (_ for _ in ()).throw(
                AssertionError("probe should not run")
            ),
        )

        self.assertEqual("SAFE_HOLD", result["ecosystem_status"])
        self.assertEqual("LIVE_PROBE_CONFIG_INVALID", result["reason"])
        self.assertTrue(result["config_errors"])


if __name__ == "__main__":
    unittest.main()
