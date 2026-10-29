"""Tests for the read-only production acceptance matrix (#1935/#1896/#1950/#1951).

Fixture = a real read-only capture (2026-10-29). Mutation tests derive synthetic variants from it and
exercise the EVALUATOR only; they assert nothing about production facts.
"""
import copy
import json
from pathlib import Path

import pytest

from v17 import model_acceptance_matrix as mm

FIX = Path(__file__).parent / "tests/fixtures/model_acceptance_v17/production_evidence_20261029.json"


def ev():
    return json.loads(FIX.read_text())


def route(m, name):
    return next(r for r in m["routes"] if r["route"] == name)


def nfl(e):
    return next(r for r in e["routes"] if r["route"] == "NFL_MONEYLINE")


def build(e):
    return route(mm.build_matrix(e), "NFL_MONEYLINE")


def test_real_capture_first_failing_stage_per_sport():
    m = mm.build_matrix(ev())
    assert m["can_execute"] is False and m["dry_run_only"] is True and m["self_certification"] is False
    assert m["ready_routes"] == []
    n = route(m, "NFL_MONEYLINE")
    assert (n["first_failing_stage"], n["exact_blocker"]) == ("CALIBRATION", "FORWARD_CALIBRATION_INSUFFICIENT_N")
    assert n["probability_status"] == "REJECTED" and n["scorer_invocation"]["status"] == "INVOKED"
    # The registry flag is CHECK-constrained false by design: it must never be reported as a blocker/certification signal.
    assert not any("NOT_CERTIFIED_FOR_PUBLICATION" in b for b in n["blockers"])
    assert "PUBLICATION:CONSUMER_DISPLAYS_PROBABILITY_FOR_NON_PUBLISHED_TERMINAL" in n["blockers"]
    assert "ROW_FLAG_PUBLISHABLE_BUT_TERMINAL_NOT_PUBLISHED" in n["provenance_findings"]
    assert "FORWARD_LOWER_BOUND_OVERCONFIDENT_WOULD_STILL_BLOCK_AT_MIN_N" in n["provenance_findings"]
    b = route(m, "NBA_MONEYLINE")
    assert (b["first_failing_stage"], b["exact_blocker"]) == ("MODEL", "MODEL_UNAVAILABLE")
    assert b["scorer_invocation"]["status"] == "NOT_INVOKED" and "CALIBRATION:ACTIVE_CALIBRATOR_ABSENT" in b["blockers"]
    c = route(m, "NCAAF_MONEYLINE")
    assert (c["first_failing_stage"], c["exact_blocker"]) == ("MODEL", "MODEL_UNAVAILABLE")
    assert "FITTED_ARTIFACT_ABSENT" in c["provenance_findings"] and c["scorer_invocation"]["status"] == "NO_ROWS"
    bp = route(m, "NBA_PROPS")
    assert (bp["first_failing_stage"], bp["exact_blocker"]) == ("MODEL", "MODEL_UNAVAILABLE")
    assert "ARTIFACTS_PRESENT_NONE_ACTIVE_PROMOTED" in bp["provenance_findings"] and bp["scorer_invocation"]["status"] == "NO_ROWS"
    fp = route(m, "NFL_PROPS")
    assert (fp["first_failing_stage"], fp["exact_blocker"]) == ("CALIBRATION", "ROW_CALIBRATION_NOT_PASS")
    assert "PUBLICATION:FORWARD_CERTIFICATION_UNPROVEN" in fp["blockers"] and fp["probability_status"] == "HELD_TYPED"
    assert "ROW_CALIBRATION_STATUS:PRECALIBRATION_SHRINKAGE" in fp["provenance_findings"]
    assert fp["scorer_invocation"]["status"] == "INVOKED" and fp["readiness"] == "hold"


def test_environment_blockers_hold_every_route_and_are_not_a_model_stage():
    m = mm.build_matrix(ev())
    assert m["environment_blockers"] == ["RENDER_WEB_WORKER_SHA_MISMATCH", "PRODUCTION_DB_BINDING_UNPROVEN"]
    assert m["routes"][0]["render_engine_live_sha"] == m["routes"][0]["github_main_sha"]
    assert all(r["readiness"] == "hold" for r in m["routes"])
    assert all(r["first_failing_stage"] != "ENVIRONMENT" for r in m["routes"])
    e = ev(); e["render"]["engine"]["live_sha"] = "0" * 40
    assert "RENDER_ENGINE_SHA_NOT_GITHUB_MAIN" in mm.environment_blockers(e)
    e = ev(); del e["render"]["worker"]
    assert mm.environment_blockers(e)[0] == "ENVIRONMENT_SHA_EVIDENCE_MISSING"


def test_registry_publishable_flag_is_never_a_certification_signal():
    e = ev()
    for flag in (False, True):
        nfl(e)["registry"]["active_promoted"][0]["probability_publishable"] = flag
        assert not any("CERTIFIED_FOR_PUBLICATION" in b for b in build(e)["blockers"])


def test_rank_eligible_without_published_terminal_is_rejected():
    e = ev(); row = nfl(e)["latest_run"]["rows"][0]
    row.update(rank_eligible=True, final_terminal="HELD")
    r = build(e)
    assert "PUBLICATION:RANK_ELIGIBLE_WITHOUT_PUBLISHED_TERMINAL" in r["blockers"] and r["probability_status"] == "REJECTED"


def test_consumer_ignoring_terminal_hold_is_a_source_proven_blocker_and_clears_when_terminal_published_or_consumer_gated():
    e = ev()
    assert "PUBLICATION:CONSUMER_DISPLAYS_PROBABILITY_FOR_NON_PUBLISHED_TERMINAL" in build(e)["blockers"]
    nfl(e)["consumers"][0]["ignores_terminal"] = False
    assert not any("CONSUMER_DISPLAYS" in b for b in build(e)["blockers"])
    e = ev(); nfl(e)["consumers"][0].update(ignores_terminal=False, observed=True)
    assert "PUBLICATION:CONSUMER_PUBLISHED_FROM_NON_PUBLISHED_TERMINAL" in build(e)["blockers"]


def test_nonpublishing_lifecycle_champion_is_blocked_but_prospective_is_ok_only_where_route_declares_it():
    e = ev()
    nfl(e)["registry"]["active_promoted"][0]["lifecycle_state"] = "PROSPECTIVE_CERTIFIED"
    assert "PUBLICATION:ARTIFACT_LIFECYCLE_NOT_PUBLICATION_ELIGIBLE" in build(e)["blockers"]
    nfl(e)["registry"]["publication_lifecycles"] = ["PROSPECTIVE_CERTIFIED", "CHAMPION"]
    assert "PUBLICATION:ARTIFACT_LIFECYCLE_NOT_PUBLICATION_ELIGIBLE" not in build(e)["blockers"]


@pytest.mark.parametrize("mutate,code", [
    (lambda r: r.update(model_artifact_id="11111111-1111-1111-1111-111111111111"), "ROW_ARTIFACT_NOT_ACTIVE_CHAMPION"),
    (lambda r: r.update(artifact_checksum="bad"), "ROW_ARTIFACT_CHECKSUM_MISMATCH"),
    (lambda r: r.update(source_snapshot_timestamp="2026-10-27T00:00:00+00:00"), "SOURCE_SNAPSHOT_STALE"),
    (lambda r: r.update(model_timestamp="2026-11-01T18:00:01+00:00"), "MODEL_AFTER_EVENT_START"),
    (lambda r: r.update(official_event_id=None), "EVENT_IDENTITY_UNRESOLVED"),
    (lambda r: r.update(calibrated_lower_bound=0.95), "CALIBRATED_BOUND_INVALID"),
    (lambda r: r.update(calibrated_probability=1.0), "CALIBRATED_BOUND_INVALID"),
    (lambda r: r.update(calibrated_probability=float("nan")), "CALIBRATED_BOUND_INVALID"),
    (lambda r: r.update(prediction_receipt_found=False), "IMMUTABLE_PREDICTION_RECEIPT_MISSING"),
    (lambda r: r.update(prediction_id=None), "IMMUTABLE_PREDICTION_RECEIPT_MISSING"),
])
def test_stale_wrong_or_malformed_evidence_is_typed(mutate, code):
    e = ev(); mutate(nfl(e)["latest_run"]["rows"][0])
    assert any(b.endswith(":" + code) for b in build(e)["blockers"])


def test_scorer_failure_input_and_output_failures_are_never_collapsed_into_unavailable():
    for code in ("MODEL_SCORER_FAILED", "MODEL_INPUTS_INSUFFICIENT", "MODEL_OUTPUT_INVALID"):
        e = ev(); nfl(e)["latest_run"]["rows"][0].update(code=code, bucket=code, model_invoked=True)
        b = build(e)["blockers"]
        assert f"MODEL:{code}" in b and "MODEL:MODEL_UNAVAILABLE" not in b


def test_http200_style_model_unavailable_row_is_not_accepted():
    e = ev(); r = nfl(e)["latest_run"]["rows"][0]
    r.update(model_invoked=False, bucket="MODEL_UNAVAILABLE", row_status="COMPLETED")
    out = build(e)
    assert "MODEL:MODEL_UNAVAILABLE" in out["blockers"] and out["readiness"] == "hold"


def test_ambiguous_champion_and_unpromoted_registry_fail_closed():
    e = ev(); nfl(e)["registry"]["active_promoted"].append(copy.deepcopy(nfl(e)["registry"]["active_promoted"][0]))
    assert "CHAMPION_AMBIGUOUS" in build(e)["provenance_findings"]
    e = ev(); nfl(e)["registry"]["active_promoted"] = []
    assert "MODEL:MODEL_UNAVAILABLE" in build(e)["blockers"]


def test_forward_calibration_status_and_lower_bound_gap_rules():
    e = ev(); h = nfl(e)["calibration"]["forward_health"]; h.update(graded_n=100)
    r = build(e)
    assert "CALIBRATION:FORWARD_CALIBRATION_NOT_PASS" in r["blockers"]
    assert "FORWARD_LOWER_BOUND_OVERCONFIDENT_WOULD_STILL_BLOCK_AT_MIN_N" in r["provenance_findings"]


def test_evaluator_only_all_clear_variant_is_ready_and_requires_completed_row_and_proven_env():
    e = ev(); n = nfl(e)
    n["calibration"]["forward_health"].update(graded_n=120, status="PASS", lower_bound_reliability_gap=0.0)
    n["consumers"][0]["ignores_terminal"] = False
    row = n["latest_run"]["rows"][0]
    row.update(row_status="COMPLETED", rank_eligible=True, final_terminal="FINAL_APPROVED", forward_certification_status="PASS")
    e["render"]["worker"]["live_sha"] = e["render"]["engine"]["live_sha"]; e["database"]["production_binding"] = "PROVEN"
    out = mm.build_matrix(e)
    r = route(out, "NFL_MONEYLINE")
    assert r["blockers"] == [] and r["probability_status"] == "PROBABILITY_ACCEPTED"
    assert r["readiness"] == "ready" and out["ready_routes"] == ["NFL_MONEYLINE"]
    row["row_status"] = "HELD"
    assert route(mm.build_matrix(e), "NFL_MONEYLINE")["readiness"] == "hold"
    row["row_status"] = "COMPLETED"; e["database"]["production_binding"] = "UNPROVEN"
    assert route(mm.build_matrix(e), "NFL_MONEYLINE")["readiness"] == "hold"


def test_no_rows_with_working_champion_is_a_route_blocker_but_absent_model_is_only_a_note():
    e = ev(); nfl(e)["latest_run"]["rows"] = []
    assert "ROUTE:NO_ROWS_IN_LATEST_RUN" in build(e)["blockers"]
    c = route(mm.build_matrix(ev()), "NCAAF_MONEYLINE")
    assert not any(b.startswith("ROUTE:") for b in c["blockers"]) and c["notes"]


def test_row_count_mismatch_and_schema_and_redaction_and_cli(tmp_path):
    e = ev(); nfl(e)["latest_run"]["rows_total"] = 99
    assert "RECONCILIATION:ROW_COUNT_MISMATCH" in build(e)["blockers"]
    with pytest.raises(ValueError):
        mm.build_matrix({"schema": "x"})
    e = ev(); e["database"]["service_role_key"] = "SECRET"; e["routes"][0]["api_key"] = "SECRET"
    assert "SECRET" not in json.dumps(mm.build_matrix(e))
    out_j, out_m = tmp_path / "m.json", tmp_path / "m.md"
    assert mm.main(["--evidence", str(FIX), "--json-out", str(out_j), "--md-out", str(out_m)]) == 0
    assert json.loads(out_j.read_text())["can_execute"] is False and "NFL_MONEYLINE" in out_m.read_text()


def test_committed_sample_matrix_is_current():
    sample = Path(__file__).parent / "acceptance/v17-production-acceptance-matrix-20261029.json"
    assert json.loads(sample.read_text()) == mm.build_matrix(ev())


def _all_clear(e):
    e["render"]["worker"]["live_sha"] = e["render"]["engine"]["live_sha"]; e["database"]["production_binding"] = "PROVEN"


def test_prop_route_on_precalibration_shrinkage_is_never_ready_even_if_every_other_signal_is_clear():
    e = ev(); r = next(x for x in e["routes"] if x["route"] == "NFL_PROPS")["latest_run"]["rows"][0]
    r.update(row_status="COMPLETED", rank_eligible=True, final_terminal="FINAL_APPROVED", forward_certification_status="PASS", code=None, bucket="COMPLETED")
    _all_clear(e)
    out = route(mm.build_matrix(e), "NFL_PROPS")
    assert out["readiness"] == "hold" and "CALIBRATION:ROW_CALIBRATION_NOT_PASS" in out["blockers"]
    r["calibration_status"] = "PASS"          # only a fitted, passing calibration clears it
    assert route(mm.build_matrix(e), "NFL_PROPS")["readiness"] == "ready"


@pytest.mark.parametrize("name", ["NBA_PROPS", "NBA_MONEYLINE", "NCAAF_MONEYLINE"])
def test_fake_success_rows_on_routes_without_a_fitted_model_are_held(name):
    e = ev(); rt = next(x for x in e["routes"] if x["route"] == name)
    rt["latest_run"]["rows"] = [{"row_status": "COMPLETED", "rank_eligible": True, "final_terminal": "FINAL_APPROVED", "model_invoked": True,
                                 "row_probability_publishable": True, "calibrated_probability": 0.7, "calibrated_lower_bound": 0.65,
                                 "prediction_receipt_found": True, "prediction_id": "p", "official_event_id": "x",
                                 "forward_certification_status": "PASS", "calibration_status": "PASS"}]
    _all_clear(e)
    out = route(mm.build_matrix(e), name)
    assert out["readiness"] == "hold" and "MODEL:MODEL_UNAVAILABLE" in out["blockers"] and out["probability_status"] != "PROBABILITY_ACCEPTED"
