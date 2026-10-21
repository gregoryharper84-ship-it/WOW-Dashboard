"""Fixture-only tests for the V17 model acceptance harness (#1935).

Deterministic: no network, credentials, database or production writes.
"""
import copy
import json
from pathlib import Path

import pytest

from v17 import model_acceptance_harness as h

HERE = Path(__file__).parent
BUNDLE_PATH = HERE / "tests" / "fixtures" / "model_acceptance_v17" / "bundle.json"
SAMPLE_JSON = HERE / "acceptance" / "v17-model-acceptance-harness-sample.json"
SAMPLE_MD = HERE / "acceptance" / "v17-model-acceptance-harness-sample.md"


@pytest.fixture()
def bundle():
    return json.loads(BUNDLE_PATH.read_text())


def _case(bundle, case_id):
    return next(c for c in bundle["cases"] if c["case_id"] == case_id)


def _row(report, case_id):
    return next(r for r in report["routes"] if r["case_id"] == case_id)


def _run_one(bundle, case):
    return h.run_bundle({**bundle, "cases": [case]})["routes"][0]


def test_fixture_suite_is_consistent_and_never_self_certifies(bundle):
    report = h.run_bundle(bundle)
    assert report["harness_status"] == "HARNESS_FIXTURES_CONSISTENT", report["expectation_mismatches"]
    assert report["can_execute"] is False and report["dry_run_only"] is True
    assert report["self_certified"] is False
    assert report["production_acceptance"] == "NOT_PERFORMED_REQUIRES_INDEPENDENT_SIRT_QA"
    assert all(r["can_execute"] is False for r in report["routes"])


def test_coverage_and_reconciliation(bundle):
    report = h.run_bundle(bundle)
    assert report["coverage"]["complete"] is True
    t = report["totals"]
    assert t["cases"] == t["probability_accepted"] + t["held_typed"] + t["rejected"]
    assert report["receipt_reconciliation"]["duplicates"] == []


def test_missing_route_breaks_coverage(bundle):
    bundle["cases"] = [c for c in bundle["cases"] if c["route"] != "NCAAF_SPREAD"]
    report = h.run_bundle(bundle)
    assert "NCAAF_SPREAD" in report["coverage"]["missing"]
    assert report["harness_status"] == "HARNESS_FAILED"


def test_duplicate_receipt_ids_fail_harness(bundle):
    a = _case(bundle, "nfl-prop-rushing-accept")
    a["receipt"]["receipt_id"] = _case(bundle, "nfl-prop-passing-accept")["receipt"]["receipt_id"]
    report = h.run_bundle(bundle)
    assert report["receipt_reconciliation"]["duplicates"]
    assert report["harness_status"] == "HARNESS_FAILED"


def test_wrong_expectation_is_reported_not_hidden(bundle):
    _case(bundle, "nba-points-200-model-unavailable")["expect"]["probability_status"] = "PROBABILITY_ACCEPTED"
    report = h.run_bundle(bundle)
    assert report["harness_status"] == "HARNESS_FAILED"
    assert report["expectation_mismatches"][0]["actual"] == "HELD_TYPED"


@pytest.mark.parametrize(
    "http_status,response,expected",
    [
        (200, {"model_status": "MODEL_UNAVAILABLE"}, "MODEL_UNAVAILABLE"),
        (200, {"model_status": "MODEL_SCORER_FAILED"}, "MODEL_SCORER_FAILED"),
        (200, {"model_status": "MODEL_INPUTS_INSUFFICIENT"}, "MODEL_INPUTS_INSUFFICIENT"),
        (200, {"model_status": "MODEL_OUTPUT_INVALID"}, "MODEL_OUTPUT_INVALID"),
        (503, {}, "MODEL_SCORER_FAILED"),
        (200, {"probability": "0.6"}, "MODEL_OUTPUT_INVALID"),
        (200, {"probability": 1.0}, "MODEL_OUTPUT_INVALID"),
        (200, {"probability": 0.0}, "MODEL_OUTPUT_INVALID"),
        (200, {"probability": float("nan")}, "MODEL_OUTPUT_INVALID"),
        (200, {"probability": True}, "MODEL_OUTPUT_INVALID"),
        (200, {"model_status": "SUCCESS_MAYBE", "probability": 0.6}, "MODEL_OUTPUT_INVALID"),
        (200, {"model_status": "OK", "probability": 0.6}, None),
    ],
)
def test_typed_model_failures_are_never_collapsed(http_status, response, expected):
    assert h.classify_model_outcome(http_status, response) == expected


def test_http_200_with_model_unavailable_is_not_success(bundle):
    for case_id in ("nba-points-200-model-unavailable", "nba-pra-200-model-unavailable"):
        case = _case(bundle, case_id)
        assert case["http_status"] == 200
        row = _run_one(bundle, case)
        assert row["probability_status"] == "HELD_TYPED"
        assert row["exact_blocker"] == "MODEL_UNAVAILABLE"
        assert row["probability_publishable"] is False


def test_nba_unavailable_that_claims_publication_is_rejected(bundle):
    case = _case(bundle, "nba-points-200-model-unavailable")
    case["response"]["probability_publishable"] = True
    row = _run_one(bundle, case)
    assert row["probability_status"] == "REJECTED"
    assert "PUBLICATION:PUBLISHED_WITHOUT_BOUND_SUPPORT" in row["blockers"]


def test_money_audit_failure_preserves_probability_pass(bundle):
    row = _run_one(bundle, _case(bundle, "nfl-prop-receiving-money-absent"))
    assert row["probability_status"] == "PROBABILITY_ACCEPTED"
    assert row["money_status"] == "MONEY_AUDIT_FAILED"
    assert row["blockers"] == []
    assert row["first_failing_stage"] == "MONEY_AUDIT"


def test_wager_evidence_is_flagged(bundle):
    case = _case(bundle, "nfl-ml-accept")
    case["money"]["wager_placed"] = True
    row = _run_one(bundle, case)
    assert row["money_blockers"] == ["LIVE_EXECUTION_EVIDENCE_FORBIDDEN"]


def test_unsupported_spread_route_holds_and_never_borrows(bundle):
    case = _case(bundle, "ncaaf-1h-spread-unsupported-hold")
    assert _run_one(bundle, case)["probability_status"] == "HELD_TYPED"
    case["response"]["probability"] = 0.61  # e.g. borrowed moneyline probability
    row = _run_one(bundle, case)
    assert row["probability_status"] == "REJECTED"
    assert "MODEL:PROBABILITY_BORROWED" in row["blockers"]


def test_bound_above_point_estimate_rejected(bundle):
    case = _case(bundle, "nfl-spread-accept-integer-push")
    case["response"]["lower_bound"] = 0.70
    row = _run_one(bundle, case)
    assert row["exact_blocker"] == "CALIBRATED_BOUND_INVALID"


def test_lower_bound_below_declared_threshold_cannot_publish(bundle):
    case = _case(bundle, "nfl-ml-accept")
    case["response"]["lower_bound"] = 0.52
    row = _run_one(bundle, case)
    assert "PUBLICATION:PUBLISHED_WITHOUT_BOUND_SUPPORT" in row["blockers"]


def test_receipt_persistence_must_match_prediction(bundle):
    case = _case(bundle, "nfl-ml-accept")
    case["receipt"]["probability"] = 0.70
    assert _run_one(bundle, case)["exact_blocker"] == "RECEIPT_PERSISTENCE_MISMATCH"


def test_canary_mode_requires_live_sha_and_binding(bundle):
    bundle["environment"].update(mode="CANARY", render_live_sha="9" * 40, render_deploy_id="dep-x",
                                 supabase_project_ref="ref-x", production_binding_evidence="binding-x")
    row = _run_one(bundle, _case(bundle, "nfl-ml-accept"))
    assert row["exact_blocker"] == "RUN_SHA_MISMATCH"
    bundle["environment"].update(render_live_sha=bundle["environment"]["github_sha"], render_deploy_id=None)
    row = _run_one(bundle, _case(bundle, "nfl-ml-accept"))
    assert row["exact_blocker"] == "PRODUCTION_BINDING_EVIDENCE_MISSING"


def test_bundle_must_declare_dry_run(bundle):
    bundle["can_execute"] = True
    with pytest.raises(ValueError):
        h.run_bundle(bundle)
    bundle["can_execute"] = False
    bundle["environment"]["dry_run"] = False
    assert _run_one(bundle, _case(bundle, "nfl-ml-accept"))["exact_blocker"] == "DRY_RUN_CONTRACT_VIOLATED"


def test_secrets_are_redacted_from_report(bundle):
    bundle["environment"]["service_role_key"] = "sk-should-not-leak"
    bundle["environment"]["production_binding_evidence"] = {"api_key": "leak", "project": "ref"}
    report = h.run_bundle(bundle)
    assert "leak" not in json.dumps(report)


def test_reconciliation_rejects_non_terminal_rows():
    rec = h.reconcile_rows({"rows_in": 2, "terminal_states": [{"row_id": "a", "state": "COMPLETED"},
                                                              {"row_id": "b", "state": "PENDING"}]})
    assert rec["balanced"] is False
    assert "ROW_NOT_TERMINAL" in rec["blockers"]


def test_canary_is_planned_not_run():
    plan = h.plan_post_deploy_canary()
    assert plan["status"] == "PLANNED_NOT_RUN"
    assert plan["can_execute"] is False


def test_harness_has_no_network_or_write_imports():
    source = Path(h.__file__).read_text()
    for forbidden in ("requests", "httpx", "urllib", "psycopg", "supabase", "socket"):
        assert f"import {forbidden}" not in source


def test_committed_sample_report_is_reproducible(bundle):
    report = h.run_bundle(bundle)
    assert json.loads(SAMPLE_JSON.read_text()) == json.loads(json.dumps(report, sort_keys=True))
    assert SAMPLE_MD.read_text() == h.render_markdown(report)


def test_cli_writes_reports_and_exit_code_tracks_harness(tmp_path, bundle):
    out_json, out_md = tmp_path / "r.json", tmp_path / "r.md"
    assert h.main([str(BUNDLE_PATH), "--json-out", str(out_json), "--md-out", str(out_md)]) == 0
    assert json.loads(out_json.read_text())["harness_status"] == "HARNESS_FIXTURES_CONSISTENT"
    bundle["cases"] = bundle["cases"][:1]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(bundle))
    assert h.main([str(bad), "--json-out", str(out_json)]) == 1
