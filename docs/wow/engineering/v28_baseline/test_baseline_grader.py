"""Fixture tests for the V28 baseline grader. Run before trusting any rate."""
from __future__ import annotations

import unittest

from baseline_grader import (
    build_records,
    caught_by_heuristic,
    classify_commit,
    classify_path,
    compute_metrics,
    has_mechanism,
    is_fix_title,
    is_revert,
    parse_metadata,
    parse_numstat,
    unsupported_claim,
    PROBABILITY_MODULES,
    FLD,
    REC,
)


class TestClassification(unittest.TestCase):
    def test_protected_paths(self):
        for p in (
            ".github/workflows/ci.yml",
            "nested/.github/CODEOWNERS",
            ".github/CODEOWNERS",
            "ops/credentials.json",
            "infra/trust_root.pem",
            "config/ruleset.json",
            "app/permissions/policy.py",
        ):
            self.assertEqual(classify_path(p), "P", p)

    def test_probability_code(self):
        for p in (
            "artifacts/wow-engine/v17/scorer.py",
            "artifacts/wow-engine/models/fit_nfl.py",
            "src/calibration.py",
            "src/probability_router.py",
            "artifacts/wow-engine/v17/specialists/mlb.py",
            "db/fitted_model.sql",
        ):
            self.assertEqual(classify_path(p), "C", p)

    def test_routine(self):
        for p in (
            "docs/README.md",
            "tests/test_scorer.py",
            "app/api/health.py",
            "scripts/backfill.py",
            "docs/model_card.md",
        ):
            self.assertEqual(classify_path(p), "R", p)

    def test_precedence_p_over_c(self):
        self.assertEqual(
            classify_commit([".github/workflows/x.yml", "artifacts/wow-engine/v17/scorer.py"]),
            "P",
        )
        self.assertEqual(
            classify_commit(["artifacts/wow-engine/v17/scorer.py", "docs/x.md"]), "C"
        )
        self.assertEqual(classify_commit([]), "R")


class TestV17Boundary(unittest.TestCase):
    """2026-11-24 audit: the `v17` boundary is a path segment, not a substring.

    `artifacts/wow-engine/v17/` was matched by the literal "/v17/"; the parallel
    top-level `v17/` tree was not, so its modules silently graded routine.
    """

    def test_v17_is_a_segment_at_any_depth(self):
        for p in (
            "v17/contracts/envelope.py",
            "artifacts/wow-engine/v17/scorer.py",
            "artifacts/wow-engine/v17/specialists/mlb.py",
        ):
            self.assertEqual(classify_path(p), "C", p)

    def test_v17_ish_names_that_are_not_the_tree(self):
        # `v17_*.py` siblings are schemas, probes and telemetry, not emitters.
        for p in (
            "artifacts/wow-engine/v17_data_contracts.py",
            "artifacts/wow-engine/v17_observability.py",
            "artifacts/wow-engine/v17_mlb_bridge_self_acceptance.py",
            "artifacts/wow-engine/sv17/scorer.py",
        ):
            self.assertEqual(classify_path(p), "R", p)

    def test_probability_modules_beside_the_v17_tree(self):
        for p in (
            "artifacts/wow-engine/engine.py",
            "artifacts/wow-engine/simulation.py",
            "artifacts/wow-engine/prop_discrete_engine.py",
            "artifacts/wow-engine/mlb_1ip_empirical_pmf.py",
            "artifacts/wow-engine/ncaaf_logistic_adapter.py",
            "artifacts/wow-engine/ncaaf_trainer.py",
            "artifacts/wow-engine/scripts/train_nfl_props.py",
            "artifacts/wow-engine/pick_request_runtime_core.py",
            "artifacts/wow-engine/api_prod.py",
            "artifacts/flask-scoring-api/gate_engine/pipeline.py",
            "artifacts/flask-scoring-api/gate_engine/cross_sport_ranker.py",
            "artifacts/flask-scoring-api/gate_engine/full_board_confidence.py",
            "artifacts/flask-scoring-api/app.py",
        ):
            self.assertEqual(classify_path(p), "C", p)

    def test_non_emitting_neighbours_stay_routine(self):
        # market evidence, settlement, ledger clients and non-probability
        # "*_engine" modules must not be swept into C by name shape alone.
        for p in (
            "artifacts/wow-engine/market.py",
            "artifacts/wow-engine/prop_settlement.py",
            "artifacts/wow-engine/ledger.py",
            "artifacts/wow-engine/deployment_gate_tests.py",
            "artifacts/flask-scoring-api/gate_engine/balldontlie/client.py",
            "artifacts/flask-scoring-api/gate_engine/moneyline/external_analyst/contradiction_engine.py",
            "artifacts/flask-scoring-api/gate_engine/source_provenance/freshness_engine.py",
            "artifacts/other-service/app.py",
        ):
            self.assertEqual(classify_path(p), "R", p)

    def test_kalshi_weather_probability_chain(self):
        # p = normal_cdf(threshold; central_estimate + bias, sigma). The first
        # three are matched by NAMES; the chain row only needs the last three.
        # Pinned as paths either way, so folding rules cannot lose the boundary.
        for p in (
            "artifacts/wow-engine/kalshi_weather_v2/models.py",
            "artifacts/wow-engine/kalshi_weather_v2/probability_core.py",
            "artifacts/wow-engine/kalshi_weather_v2/calibration_fit.py",
            "artifacts/wow-engine/kalshi_weather_v2/hourly_forecast_fusion.py",
            "artifacts/wow-engine/kalshi_weather_v2/daily_high_forecast_fusion.py",
            "artifacts/wow-engine/kalshi_weather_v2/runtime.py",
        ):
            self.assertEqual(classify_path(p), "C", p)

    def test_kalshi_weather_decision_and_io_stay_routine(self):
        # Consumers, auditors, transport and settlement are not producers.
        for p in (
            "artifacts/wow-engine/kalshi_weather_v2/orchestrator.py",
            "artifacts/wow-engine/kalshi_weather_v2/agents.py",
            "artifacts/wow-engine/kalshi_weather_v2/terminal_governor.py",
            "artifacts/wow-engine/kalshi_weather_v2/decision_policy.py",
            "artifacts/wow-engine/kalshi_weather_v2/uncertainty.py",
            "artifacts/wow-engine/kalshi_weather_v2/observation_reconstruction.py",
            "artifacts/wow-engine/kalshi_weather_v2/hourly_index.py",
            "artifacts/wow-engine/kalshi_weather_v2/kalshi_market_data.py",
            "artifacts/wow-engine/kalshi_weather_v2/persistence.py",
            "artifacts/wow-engine/kalshi_weather_v2/http_client.py",
            "artifacts/wow-engine/kalshi_weather_v2/routes.py",
        ):
            self.assertEqual(classify_path(p), "R", p)

    def test_module_table_has_no_redundant_leaves(self):
        """Every PROBABILITY_MODULES entry must earn its place.

        A row is dead weight two ways. It may already be C via TREES/NAMES, so it
        claims coverage nobody verified -- three such kalshi rows were removed on
        2026-11-24 (models.py, probability_core.py, calibration_fit.py). Or it may
        never reach C at all, e.g. a leaf the extension gate or the docs/test
        exclusion drops, which is a row that can never fire.
        """
        for scope, leaves in PROBABILITY_MODULES:
            for leaf in sorted(leaves):
                path = f"{scope}{leaf}"
                with self.subTest(path=path):
                    self.assertEqual(
                        classify_path(path),
                        "C",
                        f"dead PROBABILITY_MODULES entry {leaf!r} in scope "
                        f"{scope!r}: {path} never classifies as C",
                    )
                    caught = caught_by_heuristic(path)
                    self.assertIsNone(
                        caught,
                        f"redundant PROBABILITY_MODULES entry {leaf!r} in scope "
                        f"{scope!r}: {path} is already C via {caught}; drop it "
                        f"so the table stays a list of exceptions",
                    )

    def test_explicit_module_table_is_package_scoped(self):
        # PROBABILITY_MODULES is one scope->leaves table; a leaf must not carry C
        # into a package that merely happens to use the same file name.
        for p in (
            "services/api/domain.py",
            "artifacts/other-service/runtime.py",
            "artifacts/other-service/engine.py",
            "artifacts/other-service/pipeline.py",
            "artifacts/wow-engine/agent_runtime/state_machine.py",
            "artifacts/wow-engine/kalshi_weather_v2/voi_engine.py",
        ):
            self.assertEqual(classify_path(p), "R", p)
        # ...while the package-independent NAMES heuristic still applies.
        self.assertEqual(classify_path("services/api/models.py"), "C")
        self.assertEqual(classify_path("scripts/train_mlb_props.py"), "C")

    def test_emitter_only_commit_is_not_routine(self):
        # Regression: a commit touching only the probability engine plus its test
        # used to grade R because no path segment matched.
        self.assertEqual(
            classify_commit(
                ["artifacts/wow-engine/engine.py", "artifacts/wow-engine/deployment_gate_tests.py"]
            ),
            "C",
        )
        self.assertEqual(
            classify_commit(
                [
                    "artifacts/wow-engine/prop_discrete_engine.py",
                    "artifacts/wow-engine/test_prop_discrete_engine.py",
                ]
            ),
            "C",
        )


class TestLanguage(unittest.TestCase):
    def test_mechanism_positive(self):
        for s, b in (
            ("fix: pool exhausted", "scorer times out because client pool is shared"),
            ("gate", "root cause: lease renewal skipped"),
            ("repair", "this regressed from #2198, due to stale digest"),
        ):
            self.assertTrue(has_mechanism(s, b), (s, b))

    def test_mechanism_negative(self):
        self.assertFalse(has_mechanism("fix: update scorer", "bumped version"))
        self.assertFalse(has_mechanism("chore: tidy", ""))

    def test_fix_and_revert_titles(self):
        self.assertTrue(is_fix_title("fix: scorer timeout"))
        self.assertTrue(is_fix_title("hotfix writers"))
        self.assertTrue(is_fix_title("Repair lease recovery"))
        self.assertFalse(is_fix_title("prefix cache warming"))
        self.assertFalse(is_fix_title("add fixtures"))
        self.assertTrue(is_revert('Revert "add gate"'))
        self.assertTrue(is_revert("revert: bad merge"))
        self.assertFalse(is_revert("fixed revert handling"))

    def test_unsupported_claim(self):
        self.assertTrue(unsupported_claim("fix: done", "deployed", test_touched=False))
        self.assertFalse(unsupported_claim("fix: done", "deployed 19e59b0", test_touched=False))
        self.assertFalse(unsupported_claim("fix: done", "", test_touched=True))
        self.assertFalse(unsupported_claim("chore: bump", "", test_touched=False))


class TestParsing(unittest.TestCase):
    def test_metadata_does_not_leak_timestamp_into_subject(self):
        text = f"abc123{FLD}1000{FLD}fix: scorer{FLD}body line 1\nbody line 2{REC}"
        recs = parse_metadata(text)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["hash"], "abc123")
        self.assertEqual(recs[0]["ts"], 1000)
        self.assertEqual(recs[0]["subject"], "fix: scorer")  # regression: no ts prefix
        self.assertIn("body line 2", recs[0]["body"])

    def test_numstat_binary_and_normal(self):
        text = f"{REC}abc\n12\t3\tsrc/a.py\n-\t-\timg.png\n{REC}def\n1\t1\ttests/test_a.py\n"
        ns = parse_numstat(text)
        self.assertEqual(ns["abc"][0], ("src/a.py", 12, 3))
        self.assertEqual(ns["abc"][1], ("img.png", None, None))
        self.assertEqual(ns["def"], [("tests/test_a.py", 1, 1)])


class TestEndToEnd(unittest.TestCase):
    def _records(self):
        meta = [
            {"hash": "c1", "ts": 1000, "subject": "fix: scorer", "body": "because pool"},
            {"hash": "c2", "ts": 2000, "subject": "docs: tidy", "body": ""},
            {"hash": "c3", "ts": 3000, "subject": "fix: app", "body": "changed handler"},
            {"hash": "c4", "ts": 4000, "subject": "fix: app again", "body": "retry"},
        ]
        ns = {
            "c1": [("artifacts/wow-engine/v17/scorer.py", 10, 2), ("tests/test_scorer.py", 5, 0)],
            "c2": [("docs/x.md", 3, 1)],
            "c3": [("app/api/health.py", 4, 1)],
            "c4": [("app/api/health.py", 2, 2)],
        }
        return build_records(meta, ns)

    def test_records(self):
        recs = {r["hash"]: r for r in self._records()}
        self.assertEqual(recs["c1"]["class"], "C")
        self.assertTrue(recs["c1"]["red_first"])
        self.assertTrue(recs["c1"]["mechanism"])
        self.assertEqual(recs["c2"]["class"], "R")
        self.assertFalse(recs["c2"]["red_first"])
        self.assertEqual(recs["c3"]["class"], "R")

    def test_metrics_and_rework(self):
        m = compute_metrics(self._records())
        self.assertEqual(m["total"], 4)
        self.assertEqual(m["classes"]["C"]["commits"], 1)
        self.assertEqual(m["classes"]["R"]["commits"], 3)
        self.assertEqual(m["classes"]["P"]["commits"], 0)
        self.assertEqual(m["classes"]["C"]["red_first_rate"], 1.0)
        self.assertEqual(m["classes"]["C"]["mechanism_rate"], 1.0)
        # c4 re-fixes app/api/health.py 1000s after c3 -> one event
        self.assertEqual(m["classes"]["R"]["rework_events"], 1)
        self.assertEqual(m["classes"]["R"]["rework_per_fix"], 0.5)  # 1 event / 2 fixes
        # sums must reconcile with the graded population
        self.assertEqual(
            sum(m["classes"][c]["commits"] for c in "RPC"), m["total"]
        )


if __name__ == "__main__":
    unittest.main()
