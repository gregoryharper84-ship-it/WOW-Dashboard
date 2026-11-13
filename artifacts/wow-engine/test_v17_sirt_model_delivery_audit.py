from datetime import datetime, timedelta, timezone
import json

import pytest

from v17.sirt_model_delivery_audit import (
    CANONICAL_SPORTS, MARKETS, PHASES, STAGES,
    assess_capability_cell, assess_forecast_quality, assess_semantic_review,
    build_model_delivery_audit, main,
)

NOW = datetime(2026, 11, 13, 16, 0, tzinfo=timezone.utc)
HEAD = "a" * 40
STALE = "b" * 40


def verified(**kw):
    row = dict(
        sport="NFL", market="PROPS", phase="PREGAME",
        stages={s: {"status": "PASS", "evidence_ref": "ref-" + s} for s in STAGES},
        candidate_count=12, event_count=3, model_artifact="nfl_receptions",
        model_version="v3", artifact_sha256="c" * 64, certification_id="CERT-1",
        action_receipt_id="ACT-1", prediction_id="PRED-1",
        source_timestamp=(NOW - timedelta(hours=1)).isoformat(),
        verified_environment="PRODUCTION", deployed_sha="d" * 40,
        owning_issue="#487", can_execute=False, terminal_authority="V17_TERMINAL_REDUCER",
    )
    row.update(kw)
    return row


def with_stage(stage, **entry):
    cell = verified()
    cell["stages"] = dict(cell["stages"])
    cell["stages"][stage] = entry
    return cell


def assess(cell):
    return assess_capability_cell(cell, sport="NFL", market="PROPS", phase="PREGAME", now=NOW)


def test_full_receipt_chain_is_verified():
    result = assess(verified())
    assert result["status"] == "VERIFIED"
    assert result["action_receipt_id"] == "ACT-1"


@pytest.mark.parametrize("drop", ["artifact_sha256", "certification_id", "action_receipt_id",
                                  "prediction_id", "deployed_sha", "verified_environment"])
def test_all_stage_pass_without_receipt_is_not_green(drop):
    result = assess(verified(**{drop: None}))
    assert result["status"] == "UNKNOWN_NOT_VERIFIED"
    assert result["canonical_blocker"] == "RECEIPT_INCOMPLETE"


def test_http_200_with_zero_denominator_is_not_green():
    result = assess(verified(candidate_count=0, event_count=0))
    assert result["status"] == "UNKNOWN_NOT_VERIFIED"


def test_staging_environment_and_stale_or_future_evidence_are_not_green():
    assert assess(verified(verified_environment="STAGING"))["status"] == "UNKNOWN_NOT_VERIFIED"
    old = assess(verified(source_timestamp=(NOW - timedelta(days=3)).isoformat()))
    assert old["canonical_blocker"] == "EVIDENCE_STALE"
    future = assess(verified(source_timestamp=(NOW + timedelta(hours=1)).isoformat()))
    assert future["canonical_blocker"] == "SOURCE_TIMESTAMP_UNVERIFIABLE"


def test_typed_failure_reports_first_failing_stage():
    cell = with_stage("FITTED_SPECIALIST", status="FAIL", evidence_ref="r", blocker_code="MODEL_SCORER_FAILED")
    cell["stages"]["ACTION"] = {"status": "FAIL", "evidence_ref": "r", "blocker_code": "LATER_BLOCKER"}
    result = assess(cell)
    assert (result["status"], result["first_failing_stage"], result["canonical_blocker"]) == (
        "BLOCKED_TYPED", "FITTED_SPECIALIST", "MODEL_SCORER_FAILED")


@pytest.mark.parametrize("code", ["", "error", "FAILED", "unknown", "oops"])
def test_untyped_generic_failure_is_never_blocked_typed(code):
    result = assess(with_stage("ACTION", status="FAIL", evidence_ref="r", blocker_code=code))
    assert result["status"] == "UNKNOWN_NOT_VERIFIED"
    assert result["canonical_blocker"] == "UNTYPED_FAILURE"


def test_pass_without_evidence_ref_is_unavailable():
    result = assess(with_stage("CURRENT_HYDRATION", status="PASS"))
    assert (result["status"], result["first_failing_stage"]) == ("UNKNOWN_NOT_VERIFIED", "CURRENT_HYDRATION")


def test_no_events_requires_authoritative_schedule_and_zero_denominator():
    cell = with_stage("DISCOVERY", status="NO_EVENTS", evidence_ref="espn")
    assert assess(cell)["canonical_blocker"] == "NO_EVENTS_SCHEDULE_UNVERIFIED"
    cell.update(authoritative_schedule_ref="espn-schedule", event_count=0)
    assert assess(cell)["status"] == "NO_EVENTS"
    cell["event_count"] = 4
    assert assess(cell)["status"] == "UNKNOWN_NOT_VERIFIED"


def test_shadow_requires_fitted_artifact_and_late_stage():
    assert assess(with_stage("CERTIFICATION", status="SHADOW", evidence_ref="r"))["status"] == "SHADOW"
    early = assess(with_stage("CURRENT_HYDRATION", status="SHADOW", evidence_ref="r"))
    assert early["canonical_blocker"] == "SHADOW_CLAIM_UNSUPPORTED"
    cell = with_stage("CERTIFICATION", status="SHADOW", evidence_ref="r")
    cell["artifact_sha256"] = None
    assert assess(cell)["status"] == "UNKNOWN_NOT_VERIFIED"


@pytest.mark.parametrize("bad", [{"can_execute": True}, {"can_execute": None},
                                 {"terminal_authority": "OTHER"}])
def test_governance_drift_blocks(bad):
    result = assess(verified(**bad))
    assert result["canonical_blocker"] == "GOVERNANCE_INVARIANT_UNVERIFIED"
    assert result["status"] != "VERIFIED"


def test_matrix_has_no_silent_sport_omission_and_flags_duplicates():
    cells = [verified(), verified(sport="NBA", market="ML"), verified(sport="NBA", market="ML"),
             verified(sport="F1", market="ML", phase="LIVE"), {"sport": "MLB", "market": "TOTALS", "phase": "PREGAME"}]
    audit = build_model_delivery_audit(cells, registered_sports=["darts"], now=NOW)
    assert audit["audit_type"] == "SIRT_MODEL_DELIVERY_AUDIT"
    assert set(CANONICAL_SPORTS) | {"F1", "DARTS"} == set(audit["sports"])
    assert len(audit["matrix"]) == len(audit["sports"]) * len(MARKETS) * len(PHASES)
    by_key = {(r["sport"], r["market"], r["phase"]): r for r in audit["matrix"]}
    assert by_key[("NFL", "PROPS", "PREGAME")]["status"] == "VERIFIED"
    assert by_key[("NFL", "PROPS", "LIVE")]["status"] == "UNKNOWN_NOT_VERIFIED"
    assert by_key[("NBA", "ML", "PREGAME")]["canonical_blocker"] == "DUPLICATE_CELL_EVIDENCE"
    assert by_key[("DARTS", "SPREAD", "LIVE")]["canonical_blocker"] == "EVIDENCE_UNAVAILABLE"
    assert audit["invalid_cell_records"] == 1
    assert audit["status_counts"]["VERIFIED"] == 2
    assert audit["can_execute"] is False and audit["self_approval"] is False


def test_empty_evidence_is_all_unknown():
    audit = build_model_delivery_audit([], now=NOW)
    assert audit["status_counts"]["UNKNOWN_NOT_VERIFIED"] == len(audit["matrix"]) == 13 * 3 * 2


# ---------------------------------------------------------------- quality

def forecast(i, **kw):
    row = dict(
        forecast_id=f"F{i}", sport="NBA", market="SPREAD", cohort="2026-11",
        immutable=True, prediction_ts="2026-11-01T18:00:00Z", event_start_ts="2026-11-01T23:00:00Z",
        settled_ts="2026-11-02T02:00:00Z", event_id="E", outcome_event_id="E",
        market_key="SPREAD", outcome_market_key="SPREAD", period="FG", outcome_period="FG",
        side="HOME", outcome_side="HOME", line=-3.5, outcome_line=-3.5,
        line_convention="SIGNED_SIDE_LINE", probability=0.6, lower_bound=0.5,
        outcome="WIN" if i % 5 < 3 else "LOSS",
    )
    row.update(kw)
    return row


def test_quality_metrics_and_coverage():
    rows = [forecast(i) for i in range(20)]
    result = assess_forecast_quality(rows, min_cohort=10)
    cohort = result["cohorts"][0]
    assert cohort["status"] == "ESTIMATED" and cohort["n"] == 20
    assert cohort["brier"] == pytest.approx(0.24)
    assert cohort["ece"] == pytest.approx(0.0)
    assert cohort["lower_bound"]["respected"] is True
    assert cohort["baseline_identical_cohort"] == "BASELINE_NOT_ON_IDENTICAL_COHORT"
    assert result["coverage"] == {"rows_in": 20, "eligible_scored": 20, "excluded": {}}


def test_small_cohort_is_not_yet_estimable():
    cohort = assess_forecast_quality([forecast(i) for i in range(5)], min_cohort=10)["cohorts"][0]
    assert cohort == {"sport": "NBA", "market": "SPREAD", "cohort": "2026-11", "n": 5,
                      "status": "NOT_YET_ESTIMABLE"}


@pytest.mark.parametrize("bad,reason", [
    ({"immutable": False}, "FORECAST_NOT_IMMUTABLE"),
    ({"prediction_ts": "2026-11-01T23:00:00Z"}, "POST_START_LEAKAGE"),
    ({"outcome_event_id": "OTHER"}, "IDENTITY_MISMATCH"),
    ({"outcome_side": "AWAY"}, "IDENTITY_MISMATCH"),
    ({"outcome_period": "1H"}, "IDENTITY_MISMATCH"),
    ({"outcome_line": -4.5}, "LINE_MISMATCH"),
    ({"line_convention": None}, "SPREAD_LINE_UNSIGNED"),
    ({"probability": float("nan")}, "MALFORMED_PROBABILITY"),
    ({"probability": 1.0}, "MALFORMED_PROBABILITY"),
    ({"probability": "0.6"}, "MALFORMED_PROBABILITY"),
    ({"outcome": "PUSH"}, "PUSH"),
    ({"outcome": "VOID"}, "VOID"),
    ({"outcome": None}, "UNSETTLED"),
    ({"settled_ts": None}, "SETTLEMENT_UNVERIFIABLE"),
    ({"sport": "SOCCER", "market": "ML", "line": None, "outcome_line": None,
      "outcome": "PUSH"}, "SOCCER_DRAW_NOT_A_PUSH"),
    ({"sport": "SOCCER", "market": "ML", "line": None, "outcome_line": None,
      "side": "HOME_DNB", "outcome_side": "HOME_DNB"}, "SOCCER_THREE_WAY_SIDE_INVALID"),
])
def test_invalid_rows_are_excluded_with_typed_reason(bad, reason):
    result = assess_forecast_quality([forecast(0, **bad)], min_cohort=2)
    assert result["coverage"]["excluded"] == {reason: 1}
    assert result["cohorts"] == []


def test_duplicates_and_baseline_only_on_identical_cohort():
    rows = [forecast(i, baseline_probability=0.55) for i in range(10)] + [forecast(0)]
    result = assess_forecast_quality(rows, min_cohort=10)
    assert result["coverage"]["excluded"] == {"DUPLICATE_FORECAST": 1}
    baseline = result["cohorts"][0]["baseline_identical_cohort"]
    assert baseline["n"] == 10
    assert result["probability_source"].endswith("NO_SUBSTITUTION")


def test_soccer_draw_is_scored_as_three_way_outcome():
    rows = [forecast(i, sport="SOCCER", market="ML", line=None, outcome_line=None, side="DRAW",
                     outcome_side="DRAW", probability=0.28) for i in range(10)]
    cohort = assess_forecast_quality(rows, min_cohort=10)["cohorts"][0]
    assert cohort["status"] == "ESTIMATED" and cohort["n"] == 10


# ---------------------------------------------------------------- semantic review

def review(**kw):
    row = dict(reviewer="independent-sirt", reviewer_type="User", source="PR_REVIEW",
               commit_sha=HEAD, verdict="REVIEW_PASS", full_diff_reviewed=True,
               evidence_refs=["diff-readback"], counterexample_tests=["stale-sha"])
    row.update(kw)
    return row


def decide(r, change_class="C", architect=None, author="pr-author", head=HEAD):
    return assess_semantic_review(pr_author=author, current_head_sha=head, change_class=change_class,
                                  review=r, architect_review=architect)


def test_attributable_exact_sha_pass():
    result = decide(review())
    assert result["verdict"] == "REVIEW_PASS" and result["hold"] is False
    assert result["authorizes_merge_or_deploy"] is False


@pytest.mark.parametrize("bad,reason", [
    ({"reviewer": "PR-Author"}, "SELF_OR_ANONYMOUS_REVIEW"),
    ({"reviewer": ""}, "SELF_OR_ANONYMOUS_REVIEW"),
    ({"reviewer": "coderabbitai[bot]"}, "BOT_REVIEW_NOT_SEMANTIC"),
    ({"reviewer_type": "Bot"}, "BOT_REVIEW_NOT_SEMANTIC"),
    ({"source": "CHECK_RUN"}, "NON_SEMANTIC_SOURCE"),
    ({"source": "INTAKE_ISSUE"}, "NON_SEMANTIC_SOURCE"),
    ({"commit_sha": STALE}, "STALE_OR_MISMATCHED_SHA"),
    ({"full_diff_reviewed": False}, "PASS_EVIDENCE_INCOMPLETE"),
    ({"counterexample_tests": []}, "PASS_EVIDENCE_INCOMPLETE"),
    ({"verdict": "APPROVED"}, "VERDICT_INVALID"),
])
def test_non_independent_or_stale_review_is_unverified_hold(bad, reason):
    result = decide(review(**bad))
    assert (result["verdict"], result["reason"], result["hold"]) == (
        "INDEPENDENT_SEMANTIC_UNVERIFIED", reason, True)
    assert result["escalation"].endswith("#2136")


def test_missing_review_is_unverified():
    assert decide(None)["reason"] == "NO_INDEPENDENT_REVIEW"


def test_reject_is_recorded_and_holds():
    result = decide(review(verdict="REVIEW_REJECT_ENGINEERING", full_diff_reviewed=False))
    assert (result["verdict"], result["hold"]) == ("REVIEW_REJECT_ENGINEERING", True)


def test_p_class_requires_distinct_independent_architect():
    assert decide(review(), "P")["reason"] == "INDEPENDENT_ARCHITECT_REVIEW_MISSING"
    assert decide(review(), "P", review())["reason"] == "ARCHITECT_NOT_DISTINCT"
    stale = review(reviewer="architect", commit_sha=STALE)
    assert decide(review(), "P", stale)["reason"] == "ARCHITECT_STALE_OR_MISMATCHED_SHA"
    assert decide(review(), "P", review(reviewer="architect"))["verdict"] == "REVIEW_PASS"


def test_invalid_head_is_unverified():
    assert decide(review(), head="short")["reason"] == "PR_IDENTITY_UNVERIFIED"


# ---------------------------------------------------------------- cli

def test_cli_fails_closed_on_unreadable_evidence(tmp_path, capsys):
    assert main(["census", str(tmp_path / "missing.json")]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "EVIDENCE_UNAVAILABLE"
    path = tmp_path / "cells.json"
    path.write_text(json.dumps([]), encoding="utf-8")
    assert main(["census", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["audit_type"] == "SIRT_MODEL_DELIVERY_AUDIT"
