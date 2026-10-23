"""Strict publication-chain ceiling on the full-board terminal reducer.

Regression for the NFL contradiction: a row labelled FINAL_APPROVED and
rank_eligible by the upstream governor, while the separate
publication_chain_audit proved a mandatory stage (final refresh) unproven.

Invariant under test:  audit BLOCKED  =>  never RANK_ELIGIBLE.
The ceiling only lowers a row. It never changes numerical probabilities,
never upgrades a row, and never changes can_execute.
"""
from __future__ import annotations

import copy

from v17.full_board_overlay import enrich_full_board_result
from v17.full_board_stabilization import (
    EVENT_ALREADY_STARTED,
    HELD,
    MODEL_OUTPUT_INVALID,
    RANK_ELIGIBLE,
    classify_terminal_disposition,
)

NFL_P = 0.6386
NFL_LB = 0.5827


def _nfl_row(*, with_final_refresh: bool = False, **overrides):
    """Row shaped like cross_sport_winner_discovery output for a governed NFL win.

    Mirrors the NFL governance bridge: status/mutex/audit/final_gates PASS and
    FINAL_APPROVED, but NO ``final_refresh`` evidence object.
    """
    governance = {
        "status": "PASS",
        "probability_audit_result": "PASS_PROBABILITY_AUDIT",
        "event_mutex_status": "PASS",
        "postmodel_gates_status": "PASS",
        "final_gates_status": "PASS",
        "global_terminal_reducer": "V17_TERMINAL_REDUCER",
        "probability_publishable": True,
        "rank_eligible": True,
        "terminal_label": "FINAL_APPROVED",
        "can_execute": False,
    }
    if with_final_refresh:
        governance["final_refresh"] = {
            "final_refresh_status": "PASS",
            "can_execute": False,
            "reasons": [],
            "probability_invalidated": False,
            "rerun_required": False,
        }
    row = {
        "event_key": "NFL:2026_07_CHI_X",
        "official_event_id": "2026_07_CHI_X",
        "sport": "NFL",
        "league": "NFL",
        "event_status": "PREGAME",
        "bucket": "MODEL_COMPLETED",
        "model_status": "SPORTING_PROBABILITY_COMPLETED",
        "rank_eligible": True,
        "probability_publishable": True,
        "detail": {
            "code": "GOVERNED_PROBABILITY_PUBLISHED",
            "terminal_label": "FINAL_APPROVED",
            "calibrated_probability": NFL_P,
            "calibrated_lower_bound": NFL_LB,
            "probability_package_valid": True,
            "dynamic_calibration_complete": True,
            "llp_probability_audit_result": "PASS_PROBABILITY_AUDIT",
            "event_mutex_status": "PASS",
            "llp_event_decision": "PASS",
            "llp_governance": governance,
            "global_terminal_authority": "V17_TERMINAL_REDUCER",
            "model_invoked": True,
            "can_execute": False,
        },
    }
    row.update(overrides)
    return row


def _mlb_passing_row():
    return {
        "event_key": "MLB:1",
        "official_event_id": "1",
        "sport": "MLB",
        "league": "MLB",
        "event_status": "PREGAME",
        "bucket": "MODEL_COMPLETED",
        "model_status": "SPORTING_PROBABILITY_COMPLETED",
        "rank_eligible": True,
        "probability_publishable": True,
        "detail": {
            "calibrated_probability": 0.70,
            "calibrated_lower_bound": 0.64,
            "probability_package_valid": True,
            "dynamic_calibration_complete": True,
            "probability_audit_passed": True,
            "event_governor_complete": True,
            "final_refresh_passed": True,
            "terminal_label": "FINAL_APPROVED",
            "model_invoked": True,
            "can_execute": False,
            "global_terminal_authority": "V17_TERMINAL_REDUCER",
        },
    }


def _payload(*rows, sports=("NFL", "MLB")):
    return {
        "discovery": {"sports_queried": list(sports), "events_discovered": len(rows)},
        "rows": [copy.deepcopy(r) for r in rows],
        "reconciliation": {"row_reconciliation": "PASS", "acquisition_audit": []},
        "can_execute": False,
    }


def _by_sport(result):
    return {row["sport"]: row for row in result["rows"]}


# --------------------------------------------------------------------------
# The reported contradiction
# --------------------------------------------------------------------------
def test_final_approved_row_with_blocked_audit_loses_rank_but_keeps_probability():
    result = enrich_full_board_result(_payload(_nfl_row()))
    row = _by_sport(result)["NFL"]

    assert row["publication_chain_audit"]["status"] == "BLOCKED"
    assert row["publication_chain_audit"]["first_blocker"] == "FINAL_REFRESH_NOT_COMPLETE"

    # ranking withdrawn, with provenance
    assert row["rank_eligible"] is False
    assert row["rank_eligible_pre_audit"] is True
    assert row["rank_ceiling_reason"] == (
        "PUBLICATION_CHAIN_AUDIT_BLOCKED:FINAL_REFRESH_NOT_COMPLETE"
    )

    # numerical model result and upstream label are preserved untouched
    assert row["probability_publishable"] is True
    assert row["detail"]["calibrated_probability"] == NFL_P
    assert row["detail"]["calibrated_lower_bound"] == NFL_LB
    assert row["detail"]["terminal_label"] == "FINAL_APPROVED"
    assert row["can_execute"] is False


def test_blocked_audit_row_terminates_exactly_once_as_held_and_coverage_is_not_overstated():
    result = enrich_full_board_result(_payload(_nfl_row()))
    recon = result["full_board_reconciliation"]

    assert recon["reconciliation_status"] == "PASS"
    assert recon["rows_discovered"] == recon["rows_terminal"] == 1
    assert recon["rows_by_disposition"] == {HELD: 1}
    assert RANK_ELIGIBLE not in recon["rows_by_disposition"]

    nfl_cov = {c["sport"]: c for c in recon["sport_coverage"]}["NFL"]
    assert nfl_cov["rows_rank_eligible"] == 0
    assert nfl_cov["coverage_status"] != "CANDIDATES_FOUND_AND_ROUTED"

    terminal = recon["rows"][0]
    assert terminal["publication_chain_status"] == "BLOCKED"
    assert terminal["blocker"] == "FINAL_REFRESH_NOT_COMPLETE"
    assert terminal["can_execute"] is False
    assert result["publication_complete"] is True


def test_row_with_complete_proof_including_final_refresh_stays_rank_eligible():
    result = enrich_full_board_result(_payload(_nfl_row(with_final_refresh=True)))
    row = _by_sport(result)["NFL"]
    # llp_governance.final_refresh is the canonical proof the audit accepts
    assert row["publication_chain_audit"]["status"] == "PASS"
    assert row["rank_eligible"] is True
    assert "rank_eligible_pre_audit" not in row
    assert "rank_ceiling_reason" not in row
    assert result["full_board_reconciliation"]["rows_by_disposition"] == {RANK_ELIGIBLE: 1}


# --------------------------------------------------------------------------
# No candidate loss / no collateral damage
# --------------------------------------------------------------------------
def test_fully_proven_mlb_row_is_unaffected_alongside_a_demoted_nfl_row():
    result = enrich_full_board_result(_payload(_nfl_row(), _mlb_passing_row()))
    rows = _by_sport(result)
    assert rows["MLB"]["rank_eligible"] is True
    assert rows["NFL"]["rank_eligible"] is False
    assert result["full_board_reconciliation"]["rows_by_disposition"] == {
        RANK_ELIGIBLE: 1,
        HELD: 1,
    }
    # every discovered row still reaches a terminal disposition
    assert result["full_board_contract"]["rows_discovered"] == 2
    assert result["full_board_contract"]["rows_final"] == 2


def test_ceiling_never_upgrades_a_row():
    row = _mlb_passing_row()
    row["rank_eligible"] = False
    row["probability_publishable"] = False
    result = enrich_full_board_result(_payload(row, sports=("MLB",)))
    out = _by_sport(result)["MLB"]
    assert out["rank_eligible"] is False
    assert "rank_eligible_pre_audit" not in out
    assert result["full_board_reconciliation"]["rows_by_disposition"].get(RANK_ELIGIBLE) is None


def test_non_numeric_typed_failures_are_untouched():
    unavailable = {
        "event_key": "NBA:9",
        "official_event_id": "9",
        "sport": "NBA",
        "league": "NBA",
        "event_status": "PREGAME",
        "bucket": "MODEL_UNAVAILABLE",
        "model_status": "MODEL_UNAVAILABLE",
        "rank_eligible": False,
        "probability_publishable": False,
        "detail": {"reason": "NBA_TEAM_EVENT_FITTED_MODEL_OR_ADAPTER_UNAVAILABLE", "model_invoked": False},
    }
    result = enrich_full_board_result(_payload(unavailable, sports=("NBA",)))
    out = _by_sport(result)["NBA"]
    assert out["publication_chain_audit"] is None  # audit not applicable, unchanged
    assert out["model_status"] == "MODEL_UNAVAILABLE"
    assert result["full_board_reconciliation"]["rows_by_disposition"] == {"MODEL_UNAVAILABLE": 1}


# --------------------------------------------------------------------------
# Reducer precedence: the ceiling slots in AFTER stronger typed outcomes
# --------------------------------------------------------------------------
def _flat(**kw):
    base = {"rank_eligible": True, "probability_publishable": True}
    base.update(kw)
    return base


def test_classifier_blocked_audit_caps_at_held():
    assert classify_terminal_disposition(_flat(publication_chain_status="BLOCKED")) == HELD


def test_classifier_recognizes_a_row_already_lowered_by_the_flat_row_ceiling():
    lowered = _flat(
        rank_eligible=False,
        rank_eligible_pre_audit=True,
        publication_chain_status="BLOCKED",
    )
    assert classify_terminal_disposition(lowered) == HELD


def test_classifier_does_not_reclassify_rows_that_were_never_rank_eligible():
    never = _flat(rank_eligible=False, publication_chain_status="BLOCKED")
    # no pre-audit marker: falls through exactly as before this change
    assert classify_terminal_disposition(never) == "OTHER_TYPED_BLOCKER"
    held_by_status = _flat(
        rank_eligible=False, publication_chain_status="BLOCKED", status="HOLD"
    )
    assert classify_terminal_disposition(held_by_status) == HELD


def test_classifier_pass_or_missing_audit_status_is_backward_compatible():
    assert classify_terminal_disposition(_flat(publication_chain_status="PASS")) == RANK_ELIGIBLE
    assert classify_terminal_disposition(_flat()) == RANK_ELIGIBLE
    assert classify_terminal_disposition(_flat(publication_chain_status=None)) == RANK_ELIGIBLE


def test_classifier_started_event_and_typed_model_failures_keep_precedence():
    assert (
        classify_terminal_disposition(_flat(publication_chain_status="BLOCKED", event_status="LIVE"))
        == EVENT_ALREADY_STARTED
    )
    assert (
        classify_terminal_disposition(
            _flat(publication_chain_status="BLOCKED", model_status=MODEL_OUTPUT_INVALID)
        )
        == MODEL_OUTPUT_INVALID
    )


# --------------------------------------------------------------------------
# Daily lane: the persisted row_status must follow the ceiling
# --------------------------------------------------------------------------
def test_daily_cross_sport_status_follows_the_ceiling_through_the_real_overlay():
    """Reproduces the persisted Bears shape: overlay -> stage status -> reduction.

    Before this change the overlay lowered nothing and the Daily row stayed
    COMPLETED with rank_eligible=true next to a BLOCKED audit.
    """
    from v17.daily_snapshot_runtime import _cross_sport_stage_status
    from v17.daily_terminal_reduction import reduce_row_terminal

    demoted = _by_sport(enrich_full_board_result(_payload(_nfl_row())))["NFL"]
    assert demoted["bucket"] == "MODEL_COMPLETED"  # model outcome is preserved
    status = _cross_sport_stage_status(demoted)
    assert status == "HELD"
    reduction = reduce_row_terminal([status])
    assert reduction["final_terminal"] == "HELD"
    assert reduction["approved_stage_exception_applied"] is False
    assert reduction["can_execute"] is False


def test_daily_cross_sport_status_is_unchanged_for_fully_proven_and_pre_overlay_rows():
    from v17.daily_snapshot_runtime import _cross_sport_stage_status

    proven = _by_sport(enrich_full_board_result(_payload(_nfl_row(with_final_refresh=True))))["NFL"]
    assert _cross_sport_stage_status(proven) == "COMPLETED"
    # a raw discovery row (before any overlay) behaves exactly as it did
    assert _cross_sport_stage_status(_nfl_row()) == "COMPLETED"


def test_daily_cross_sport_status_never_upgrades_a_non_completed_bucket():
    from v17.daily_snapshot_runtime import _cross_sport_stage_status

    for bucket in ("MODEL_UNAVAILABLE", "OTHER_GOVERNED_HOLD", None):
        assert _cross_sport_stage_status({"bucket": bucket, "rank_eligible": True}) == "HELD"
    assert _cross_sport_stage_status({"bucket": "MODEL_COMPLETED", "rank_eligible": False}) == "HELD"
    assert _cross_sport_stage_status({"bucket": "MODEL_COMPLETED"}) == "HELD"
