"""Regression for the October 22 Bears native NFL bridge / full-board mismatch.

Fixture uses the non-secret envelope shape of persisted V17 Daily receipt
v17-daily-47a6f3b7-4a77-4643-916d-eeee53768ad7, row 1, plus its
score_snapshot_id-bound governance receipt. This tests post-transport
reconciliation; the SQL terminal reducer is verified separately against the
actual immutable Supabase score snapshot.
"""
from __future__ import annotations

from copy import deepcopy

from v17.full_board_overlay import enrich_full_board_result


def _bears_receipt() -> dict:
    return {
        "discovery": {"sports_queried": ["NFL"]},
        "rows": [{
            "sport": "NFL",
            "league": "NFL",
            "event_key": "NFL:2026_07_NE_CHI",
            "official_event_id": "2026_07_NE_CHI",
            "event_status": "PREGAME",
            "bucket": "MODEL_COMPLETED",
            "model_status": "SPORTING_PROBABILITY_COMPLETED",
            "rank_eligible": True,
            "probability_publishable": True,
            "detail": {
                "terminal_label": "FINAL_APPROVED",
                "can_execute": False,
                "global_terminal_authority": "V17_TERMINAL_REDUCER",
                "score_snapshot_id": "8618ff41-5063-4c4a-99ae-ceaf36a23d50",
                "event_prediction_id": "9ad1208b-48c9-45d3-849c-b8be3e7b1797",
                "model_timestamp": "2026-10-22T18:19:00.969898+00:00",
                "model_artifact_id": "certified-NFL-artifact",
                "model_probability_available": True,
                "sporting_probability_completed": True,
                "sporting_probability_status": "COMPLETED",
                "calibration_health_status": "PASS",
                "calibration_method": "PLATT_TIME_SPLIT",
                "calibrated_home_probability": 0.6385781215435826,
                "calibrated_home_lower_bound": 0.5826600384350054,
                "llp_probability_audit_result": "PASS_PROBABILITY_AUDIT",
                "event_mutex_status": "PASS",
                "candidate_envelope": {
                    "official_event_id": "2026_07_NE_CHI",
                    "event_key": "NFL:2026_07_NE_CHI",
                    "latest_material_update_timestamp": "2026-10-22T17:26:34.531833+00:00",
                    "event_start_time_utc": "2026-10-23T00:15:00+00:00",
                },
                "llp_governance": {
                    "status": "PASS",
                    "blockers": [],
                    "global_terminal_reducer": "V17_TERMINAL_REDUCER",
                    "can_execute": False,
                    "score_snapshot_id": "8618ff41-5063-4c4a-99ae-ceaf36a23d50",
                    "event_prediction_id": "9ad1208b-48c9-45d3-849c-b8be3e7b1797",
                    "probability_audit_result": "PASS_PROBABILITY_AUDIT",
                    "event_mutex_status": "PASS",
                    "postmodel_gates_status": "PASS",
                    "final_gates_status": "PASS",
                    "calibration_health_status": "PASS",
                    "terminal": {
                        "status": "PASS",
                        "blockers": [],
                        "terminal_label": "FINAL_APPROVED",
                        "rank_eligible": True,
                        "probability_publishable": True,
                        "global_terminal_reducer": "V17_TERMINAL_REDUCER",
                        "can_execute": False,
                    },
                },
            },
        }],
    }


def _one(payload: dict) -> tuple[dict, dict]:
    report = enrich_full_board_result(payload)
    return report["rows"][0], report["full_board_reconciliation"]["rows"][0]


def test_bears_real_native_bridge_pass_not_false_blocked_by_legacy_field_names():
    row, terminal = _one(_bears_receipt())
    assert row["publication_chain_audit"]["status"] == "PASS"
    assert all(row["publication_chain_audit"]["stages"].values())
    assert row["rank_eligible"] is True
    assert row["probability_publishable"] is True
    assert row["governance_status"] == "FINAL_APPROVED"
    assert terminal["rank_eligible"] is True
    assert row["detail"]["calibrated_home_lower_bound"] == 0.5826600384350054
    assert row["can_execute"] is False


def test_mandatory_explicit_false_blocks_even_valid_native_bridge():
    receipt = _bears_receipt()
    receipt["rows"][0]["detail"]["probability_package_valid"] = False
    row, reconciled = _one(receipt)
    assert row["publication_chain_audit"]["status"] == "BLOCKED"
    assert row["publication_chain_audit"]["first_blocker"] == "PROBABILITY_PACKAGE_NOT_VALID"
    assert row["rank_eligible"] is False
    assert row["probability_publishable"] is False
    assert row["terminal_label"] != "FINAL_APPROVED"
    assert row["governance_status"] != "FINAL_APPROVED"
    assert row["upstream_terminal_label"] == "FINAL_APPROVED"
    assert reconciled["rank_eligible"] is False
    assert row["detail"]["calibrated_home_lower_bound"] == 0.5826600384350054


def test_failed_sql_final_gate_must_not_be_overridden_by_terminal_label():
    receipt = _bears_receipt()
    receipt["rows"][0]["detail"]["llp_governance"]["final_gates_status"] = "HOLD"
    row, _ = _one(receipt)
    assert row["publication_chain_audit"]["status"] == "BLOCKED"
    assert row["publication_chain_audit"]["stages"]["final_refresh_passed"] is False
    assert row["rank_eligible"] is False
    assert row["governance_status"] != "FINAL_APPROVED"


def test_snapshot_mismatch_cannot_borrow_some_other_attempts_governance():
    receipt = _bears_receipt()
    receipt["rows"][0]["detail"]["llp_governance"]["score_snapshot_id"] = "another-attempt"
    row, _ = _one(receipt)
    assert row["publication_chain_audit"]["status"] == "BLOCKED"
    assert row["rank_eligible"] is False


def test_explicit_refresh_failure_overrides_native_final_pass():
    receipt = _bears_receipt()
    receipt["rows"][0]["detail"]["final_refresh_status"] = "HOLD"
    row, _ = _one(receipt)
    assert row["publication_chain_audit"]["status"] == "BLOCKED"
    assert row["publication_chain_audit"]["stages"]["final_refresh_passed"] is False
    assert row["rank_eligible"] is False


def test_stale_model_after_material_update_must_not_pass_native_refresh():
    receipt = _bears_receipt()
    receipt["rows"][0]["detail"]["candidate_envelope"]["latest_material_update_timestamp"] = "2026-10-22T18:20:00+00:00"
    row, _ = _one(receipt)
    assert row["publication_chain_audit"]["status"] == "BLOCKED"
    assert row["publication_chain_audit"]["stages"]["final_refresh_passed"] is False


def test_false_probability_audit_survives_positive_alias():
    receipt = _bears_receipt()
    detail = receipt["rows"][0]["detail"]
    detail["probability_audit_passed"] = False
    row, _ = _one(receipt)
    assert row["publication_chain_audit"]["status"] == "BLOCKED"
    assert row["publication_chain_audit"]["first_blocker"] == "PROBABILITY_AUDIT_NOT_COMPLETE"


def test_unrelated_sport_not_admitted_via_nfl_bridge():
    receipt = _bears_receipt()
    receipt["rows"][0]["sport"] = "NBA"
    row, _ = _one(receipt)
    assert row["publication_chain_audit"]["status"] == "BLOCKED"
    assert row["rank_eligible"] is False


def test_native_mutex_failure_vetoes_stale_positive_alias():
    receipt = _bears_receipt()
    receipt["rows"][0]["detail"]["llp_governance"]["event_mutex_status"] = "FAIL"
    # The scorer's old, flattened convenience alias must not outrank the
    # snapshot-bound native SQL bridge.
    row, _ = _one(receipt)
    assert row["publication_chain_audit"]["status"] == "BLOCKED"
    assert row["publication_chain_audit"]["stages"]["event_governor_complete"] is False
    assert row["rank_eligible"] is False


def test_native_refresh_failure_vetoes_explicit_legacy_pass():
    receipt = _bears_receipt()
    receipt["rows"][0]["detail"]["final_refresh_passed"] = True
    receipt["rows"][0]["detail"]["llp_governance"]["final_gates_status"] = "HOLD"
    row, _ = _one(receipt)
    assert row["publication_chain_audit"]["status"] == "BLOCKED"
    assert row["publication_chain_audit"]["first_blocker"] == "FINAL_REFRESH_EVIDENCE_CONFLICT"
    assert row["rank_eligible"] is False
