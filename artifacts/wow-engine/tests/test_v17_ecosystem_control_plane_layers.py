from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
V17 = ROOT / "v17"
MIGRATION = ROOT / "migrations" / "20261007181000_wow_ecosystem_control_plane.sql"
sys.path.insert(0, str(V17))


def load_module(name):
    path = V17 / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


conductor = load_module("ecosystem_conductor")
capability = load_module("ecosystem_capability_matrix")
incident = load_module("ecosystem_incident_router")
ledger = load_module("ecosystem_ledger")
control = load_module("ecosystem_reliability_control_plane")


REGISTRY = json.loads((V17 / "ecosystem-registry.json").read_text())


class FakeQuery:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.mode = None
        self.payload = None
        self.on_conflict = None

    def insert(self, payload):
        self.mode = "insert"
        self.payload = payload
        return self

    def upsert(self, payload, on_conflict=None):
        self.mode = "upsert"
        self.payload = payload
        self.on_conflict = on_conflict
        return self

    def execute(self):
        self.db.calls.append(
            {
                "table": self.table,
                "mode": self.mode,
                "payload": self.payload,
                "on_conflict": self.on_conflict,
            }
        )
        return type("Result", (), {"data": self.payload})()


class FakeDB:
    def __init__(self):
        self.calls = []

    def table(self, name):
        return FakeQuery(self, name)


def green_evaluation():
    return {
        "ecosystem_status": "READY",
        "safe_hold_required": False,
        "safe_hold_authority": "SYSTEMS_INTELLIGENCE_RELIABILITY",
        "false_green_detected": False,
        "component_states": {name: "PASS" for name in REGISTRY["components"]},
        "handoff_states": {name: "PASS" for name in REGISTRY["handoffs"]},
        "golden_paths": {
            name: {"status": "READY"}
            for name in REGISTRY["golden_paths"]
        },
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }


def probe_receipt(
    probe_id="P1",
    *,
    status="PASS",
    boundary=None,
    failure=None,
    details=None,
):
    return {
        "probe_id": probe_id,
        "probe_type": "receipt_file",
        "observed_at": "2026-10-07T18:00:00Z",
        "status": status,
        "evidence_refs": [f"receipt://{probe_id.lower()}"],
        "first_failing_boundary": boundary,
        "typed_failure": failure,
        "source_version": "v1",
        "deployed_sha": None,
        "details": details or {},
        "can_execute": False,
    }


def ready_capability(product, domain, route, specialist, suffix):
    required = capability.PRODUCT_REQUIRED_DIMENSIONS[product]
    return capability.CapabilityInput(
        capability_id=f"{product}-{suffix}",
        product=product,
        domain=domain,
        route=route,
        controlling_specialist=specialist,
        dimensions={name: "PASS" for name in required},
    )


class MigrationContractTests(unittest.TestCase):
    def test_migration_is_service_role_only_and_non_executable(self):
        sql = MIGRATION.read_text().lower()
        for table in (
            "wow_ecosystem_probe_receipts",
            "wow_ecosystem_handoff_receipts",
            "wow_ecosystem_work_items",
            "wow_ecosystem_readiness_snapshots",
            "wow_ecosystem_findings",
        ):
            self.assertIn(f"create table if not exists public.{table}", sql)
            self.assertIn(f"alter table public.{table} enable row level security", sql)
            self.assertIn(
                f"revoke all on table public.{table} from public, anon, authenticated",
                sql,
            )
            self.assertIn("service_role", sql)
        self.assertGreaterEqual(sql.count("check (can_execute = false)"), 5)
        self.assertIn("v17_terminal_reducer", sql)

    def test_expression_identity_is_a_unique_index_not_invalid_constraint(self):
        sql = MIGRATION.read_text().lower()
        self.assertIn(
            "create unique index if not exists wow_ecosystem_probe_receipts_identity_uq",
            sql,
        )
        self.assertNotIn(
            "constraint wow_ecosystem_probe_receipts_identity_uq",
            sql,
        )


class LedgerTests(unittest.TestCase):
    def test_sanitize_redacts_nested_secret_fields(self):
        payload = ledger.sanitize(
            {
                "token": "abc",
                "nested": {
                    "authorization": "Bearer nope",
                    "safe": "yes",
                },
                "list": [{"service_key": "secret"}, {"ok": 1}],
            }
        )
        self.assertEqual("[REDACTED]", payload["token"])
        self.assertEqual("[REDACTED]", payload["nested"]["authorization"])
        self.assertEqual("yes", payload["nested"]["safe"])
        self.assertEqual("[REDACTED]", payload["list"][0]["service_key"])

    def test_probe_row_rejects_execution_authority(self):
        bad = probe_receipt()
        bad["can_execute"] = True
        with self.assertRaisesRegex(ValueError, "can_execute"):
            ledger.probe_row(bad)

    def test_handoff_rows_preserve_first_failing_boundary(self):
        registry = {
            "handoffs": {
                "A_TO_B": {
                    "source": "A",
                    "target": "B",
                }
            }
        }
        config = {"handoff_bindings": {"A_TO_B": ["ONE", "TWO"]}}
        probes = {
            "ONE": probe_receipt("ONE"),
            "TWO": probe_receipt(
                "TWO",
                status="FAIL",
                boundary="PERSISTENCE_WRITE",
                failure="DURABLE_RECEIPT_MISSING",
            ),
        }
        rows = ledger.handoff_rows(
            registry,
            config,
            probes,
            {"handoffs": {"A_TO_B": "FAIL"}},
            observed_at="2026-10-07T18:00:00Z",
        )
        self.assertEqual(1, len(rows))
        self.assertEqual("PERSISTENCE_WRITE", rows[0]["first_failing_boundary"])
        self.assertEqual("DURABLE_RECEIPT_MISSING", rows[0]["typed_failure"])
        self.assertFalse(rows[0]["can_execute"])

    def test_readiness_row_preserves_terminal_authority(self):
        row = ledger.readiness_row(green_evaluation())
        self.assertEqual("V17_TERMINAL_REDUCER", row["terminal_authority"])
        self.assertFalse(row["can_execute"])

    def test_persist_control_plane_run_uses_expected_tables(self):
        db = FakeDB()
        config = {
            "handoff_bindings": {
                name: ["P1"] for name in REGISTRY["handoffs"]
            }
        }
        observed = {
            "handoffs": {name: "PASS" for name in REGISTRY["handoffs"]},
            "work_items": [],
        }
        receipt = probe_receipt()
        result = ledger.persist_control_plane_run(
            db,
            registry=REGISTRY,
            config=config,
            probe_results={"P1": receipt},
            observed_state=observed,
            evaluation=green_evaluation(),
        )
        self.assertEqual(1, result["probe_receipts_written"])
        self.assertEqual(len(REGISTRY["handoffs"]), result["handoff_receipts_written"])
        self.assertEqual(1, result["readiness_snapshots_written"])
        self.assertFalse(result["can_execute"])
        self.assertEqual(
            {
                ledger.PROBE_TABLE,
                ledger.HANDOFF_TABLE,
                ledger.READINESS_TABLE,
            },
            {call["table"] for call in db.calls},
        )


class CapabilityMatrixTests(unittest.TestCase):
    def test_ready_prop_requires_all_dimensions(self):
        item = ready_capability(
            "WOW_PROP",
            "MLB",
            "PITCHER_STRIKEOUTS",
            "WOW_MLB_K_SPECIALIST",
            "mlb-k",
        )
        result = capability.evaluate_capability(item)
        self.assertEqual("READY", result.status)
        self.assertTrue(result.product_ready)
        self.assertFalse(result.can_execute)

    def test_missing_kalshi_settlement_dimension_fails_closed(self):
        item = ready_capability(
            "KALSHI_WEATHER",
            "KDAL",
            "DAILY_HIGH",
            "KALSHI_WEATHER_EXPERT",
            "kdal-high",
        )
        dimensions = dict(item.dimensions)
        dimensions.pop("settlement")
        result = capability.evaluate_capability(
            capability.CapabilityInput(
                capability_id=item.capability_id,
                product=item.product,
                domain=item.domain,
                route=item.route,
                controlling_specialist=item.controlling_specialist,
                dimensions=dimensions,
            )
        )
        self.assertEqual("UNKNOWN", result.status)
        self.assertFalse(result.product_ready)
        self.assertEqual("settlement", result.first_failing_dimension)
        self.assertIn("DIMENSION_UNKNOWN:settlement", result.blockers)

    def test_explicit_model_failure_is_not_ready(self):
        item = ready_capability(
            "LLP_TEAM_EVENT",
            "NBA",
            "MONEYLINE",
            "NBA_TEAM_EVENT_SPECIALIST",
            "nba-ml",
        )
        dimensions = dict(item.dimensions)
        dimensions["model"] = "FAIL"
        result = capability.evaluate_capability(
            capability.CapabilityInput(
                capability_id=item.capability_id,
                product=item.product,
                domain=item.domain,
                route=item.route,
                controlling_specialist=item.controlling_specialist,
                dimensions=dimensions,
            )
        )
        self.assertEqual("NOT_READY", result.status)
        self.assertEqual("model", result.first_failing_dimension)

    def test_zero_capability_denominator_is_unknown(self):
        summary = capability.matrix_summary([])
        self.assertIsNone(summary["reliable_decision_availability"])
        self.assertEqual("UNKNOWN", summary["metric_status"])

    def test_duplicate_capability_id_is_rejected(self):
        item = ready_capability(
            "WOW_PROP",
            "NFL",
            "RECEIVING_YARDS",
            "NFL_PROP_SPECIALIST",
            "same",
        )
        with self.assertRaisesRegex(ValueError, "duplicate capability_id"):
            capability.build_matrix([item, item])


class IncidentRouterTests(unittest.TestCase):
    def broken_receipt(self):
        return {
            "handoff_id": "PERSISTENCE_TO_PRODUCT_ORCHESTRATION",
            "source": "PERSISTENCE_PUBLICATION",
            "target": "WOW_BETTING_INTELLIGENCE",
            "status": "FAIL",
            "evidence_refs": ["receipt://persist"],
            "first_failing_boundary": "PERSISTENCE_HANDOFF",
            "typed_failure": "DURABLE_RECEIPT_MISSING",
            "can_execute": False,
        }

    def test_critical_broken_handoff_routes_p0_to_engineering(self):
        result = incident.route_findings(
            registry=REGISTRY,
            handoff_receipts=[self.broken_receipt()],
            observed_at="2026-10-07T18:00:00Z",
        )
        self.assertEqual(1, result["incident_count"])
        finding = result["incidents"][0]
        self.assertEqual("P0", finding["severity"])
        self.assertEqual(
            "SYSTEMS_INTELLIGENCE_RELIABILITY",
            finding["diagnostic_owner"],
        )
        self.assertEqual("ENGINEERING_CLOSURE", finding["closure_owner"])
        self.assertEqual("A", finding["change_class"])
        self.assertTrue(
            any("Independent Verification" in item for item in finding["acceptance_criteria"])
        )

    def test_identity_failure_is_preclassified_class_b(self):
        receipt = dict(self.broken_receipt())
        receipt["first_failing_boundary"] = "CANONICAL_IDENTITY_ROUTING"
        receipt["typed_failure"] = "EVENT_IDENTITY_UNRESOLVED"
        result = incident.route_findings(
            registry=REGISTRY,
            handoff_receipts=[receipt],
        )
        self.assertEqual("B", result["incidents"][0]["change_class"])

    def test_probability_math_failure_is_preclassified_class_c(self):
        receipt = dict(self.broken_receipt())
        receipt["first_failing_boundary"] = "CALIBRATION_LOWER_BOUND"
        receipt["typed_failure"] = "CALIBRATION_ARTIFACT_INVALID"
        result = incident.route_findings(
            registry=REGISTRY,
            handoff_receipts=[receipt],
        )
        self.assertEqual("C", result["incidents"][0]["change_class"])

    def test_third_recurrence_creates_engineering_opportunity(self):
        first = incident.route_findings(
            registry=REGISTRY,
            handoff_receipts=[self.broken_receipt()],
            observed_at="2026-10-07T18:00:00Z",
        )["incidents"][0]
        first["occurrence_count"] = 2
        result = incident.route_findings(
            registry=REGISTRY,
            handoff_receipts=[self.broken_receipt()],
            prior_findings=[first],
            observed_at="2026-10-07T19:00:00Z",
        )
        self.assertEqual(1, result["opportunity_count"])
        opportunity = result["opportunities"][0]
        self.assertEqual("OPPORTUNITY", opportunity["finding_type"])
        self.assertEqual("ENGINEERING_CLOSURE", opportunity["closure_owner"])

    def test_verified_close_requires_independent_proof(self):
        finding = incident.route_findings(
            registry=REGISTRY,
            handoff_receipts=[self.broken_receipt()],
        )["incidents"][0]
        with self.assertRaisesRegex(ValueError, "VERIFIED_CLOSED"):
            incident.close_finding(
                finding,
                independent_verification=False,
                exact_head_regression_pass=True,
                boundary_cleared=True,
            )
        closed = incident.close_finding(
            finding,
            independent_verification=True,
            exact_head_regression_pass=True,
            boundary_cleared=True,
        )
        self.assertEqual("VERIFIED_CLOSED", closed["status"])
        self.assertFalse(closed["can_execute"])


class ReliabilityControlPlaneTests(unittest.TestCase):
    def matrix(self):
        return capability.build_matrix(
            [
                ready_capability(
                    "WOW_PROP",
                    "MLB",
                    "PITCHER_STRIKEOUTS",
                    "WOW_MLB_K_SPECIALIST",
                    "mlb-k",
                ),
                ready_capability(
                    "LLP_TEAM_EVENT",
                    "NFL",
                    "MONEYLINE",
                    "NFL_TEAM_EVENT_SPECIALIST",
                    "nfl-ml",
                ),
                ready_capability(
                    "KALSHI_WEATHER",
                    "KDAL",
                    "DAILY_HIGH",
                    "KALSHI_WEATHER_EXPERT",
                    "kdal-high",
                ),
            ]
        )

    def counts(self):
        return {
            "completed_supported_requests": 8,
            "supported_requests": 10,
            "terminal_candidates": 19,
            "admitted_candidates": 20,
            "first_pass_requests": 7,
            "integrity_complete_decisions": 9,
            "published_decisions": 10,
            "eliminated_repeat_fingerprints": 3,
            "repeat_failure_fingerprints": 4,
        }

    def test_metric_family_calculates_requested_north_stars(self):
        metrics = control.build_metric_family(
            matrix=self.matrix(),
            metric_counts=self.counts(),
            findings=[],
        )
        self.assertEqual(1.0, metrics["RELIABLE_DECISION_AVAILABILITY"]["value"])
        self.assertEqual(0.8, metrics["PRODUCT_OUTCOME_COMPLETION_RATE"]["value"])
        self.assertEqual(0.95, metrics["CANDIDATE_CONSERVATION_RATE"]["value"])
        self.assertEqual(0.7, metrics["FIRST_PASS_COMPLETION_RATE"]["value"])
        self.assertEqual(0.9, metrics["DECISION_INTEGRITY_RATE"]["value"])
        self.assertEqual(0.75, metrics["REPEAT_FAILURE_ELIMINATION_RATE"]["value"])
        self.assertEqual(
            "UNKNOWN",
            metrics["DETECTION_TO_PROVEN_ROOT_CAUSE_TIME"]["status"],
        )

    def test_invalid_metric_counts_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "numerator cannot exceed"):
            control.product_outcome_completion_rate(
                completed_supported_requests=11,
                supported_requests=10,
            )

    def test_zero_denominator_is_unknown_not_perfect(self):
        metric = control.decision_integrity_rate(
            integrity_complete_decisions=0,
            published_decisions=0,
        )
        self.assertEqual("UNKNOWN", metric.status)
        self.assertIsNone(metric.value)

    def test_detection_to_root_cause_requires_complete_proof(self):
        findings = [
            {
                "first_detected_at": "2026-10-07T18:00:00Z",
                "root_cause_proven_at": "2026-10-07T18:10:00Z",
            },
            {
                "first_detected_at": "2026-10-07T18:01:00Z",
                "root_cause_proven_at": None,
            },
        ]
        metric = control.detection_to_proven_root_cause_time(findings)
        self.assertEqual("UNKNOWN", metric.status)
        self.assertEqual("ROOT_CAUSE_PROOF_INCOMPLETE", metric.reason)

    def test_full_snapshot_preserves_authority_and_route_truth(self):
        handoffs = [
            {
                "handoff_id": name,
                "source": spec["source"],
                "target": spec["target"],
                "status": "PASS",
                "evidence_refs": [f"receipt://{name.lower()}"],
                "first_failing_boundary": None,
                "typed_failure": None,
                "can_execute": False,
            }
            for name, spec in REGISTRY["handoffs"].items()
        ]
        items = [
            ready_capability(
                "WOW_PROP",
                "MLB",
                "PITCHER_STRIKEOUTS",
                "WOW_MLB_K_SPECIALIST",
                "mlb-k",
            ),
            ready_capability(
                "LLP_TEAM_EVENT",
                "NFL",
                "MONEYLINE",
                "NFL_TEAM_EVENT_SPECIALIST",
                "nfl-ml",
            ),
            ready_capability(
                "KALSHI_WEATHER",
                "KDAL",
                "DAILY_HIGH",
                "KALSHI_WEATHER_EXPERT",
                "kdal-high",
            ),
        ]
        snapshot = control.build_control_plane_snapshot(
            registry=REGISTRY,
            live_result={"evaluation": green_evaluation()},
            handoff_receipts=handoffs,
            capability_items=items,
            metric_counts=self.counts(),
            observed_at="2026-10-07T18:30:00Z",
        )
        self.assertEqual("READY", snapshot["control_plane_status"])
        self.assertFalse(snapshot["can_execute"])
        self.assertEqual(
            "V17_TERMINAL_REDUCER",
            snapshot["terminal_authority"],
        )
        self.assertEqual(
            {"WOW_PROP", "LLP_TEAM_EVENT", "KALSHI_WEATHER"},
            set(snapshot["route_truth"]),
        )
        self.assertEqual(0, snapshot["new_findings"]["incident_count"])

    def test_unknown_critical_metric_degrades_control_plane_not_model(self):
        counts = self.counts()
        counts["published_decisions"] = 0
        counts["integrity_complete_decisions"] = 0
        handoffs = [
            {
                "handoff_id": name,
                "source": spec["source"],
                "target": spec["target"],
                "status": "PASS",
                "evidence_refs": [],
                "first_failing_boundary": None,
                "typed_failure": None,
                "can_execute": False,
            }
            for name, spec in REGISTRY["handoffs"].items()
        ]
        snapshot = control.build_control_plane_snapshot(
            registry=REGISTRY,
            live_result={"evaluation": green_evaluation()},
            handoff_receipts=handoffs,
            capability_items=[
                ready_capability(
                    "WOW_PROP",
                    "MLB",
                    "PITCHER_STRIKEOUTS",
                    "WOW_MLB_K_SPECIALIST",
                    "mlb-k",
                ),
                ready_capability(
                    "LLP_TEAM_EVENT",
                    "NFL",
                    "MONEYLINE",
                    "NFL_TEAM_EVENT_SPECIALIST",
                    "nfl-ml",
                ),
                ready_capability(
                    "KALSHI_WEATHER",
                    "KDAL",
                    "DAILY_HIGH",
                    "KALSHI_WEATHER_EXPERT",
                    "kdal-high",
                ),
            ],
            metric_counts=counts,
        )
        self.assertEqual("DEGRADED", snapshot["control_plane_status"])
        self.assertEqual(
            "UNKNOWN",
            snapshot["metrics"]["DECISION_INTEGRITY_RATE"]["status"],
        )
        self.assertFalse(snapshot["safe_hold_required"])


if __name__ == "__main__":
    unittest.main()
