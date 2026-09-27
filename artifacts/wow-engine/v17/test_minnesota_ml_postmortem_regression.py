"""Regression receipt for MIN-TEX 2026-09-26 moneyline publication postmortem.

This file protects existing V17 contracts. It does not change model math,
calibration, qualification thresholds, or execution authority.
"""
from __future__ import annotations

import pytest

from v17.llp_governed_package_scoring import (
    STALE_MODEL_OUTPUT,
    edge_leaderboard_score,
    validate_governed_scoring_package,
)


def _minnesota_package() -> dict:
    return {
        "prediction_id": "regression-min-tex-20260926",
        "candidate_id": "MIN",
        "calibrated_probability": 0.5239,
        "calibrated_lower_bound": 0.4291,
        "calibrated_upper_bound": 0.6187,
        "immutable_model_timestamp": "2026-09-26T22:00:00Z",
        "model_version": "mlb-governed-regression-fixture",
        "calibration_method": "production-governed",
        "calibration_version": "regression-fixture",
        "source_snapshot_id": "min-tex-pre-delay-snapshot",
        "source_snapshot_timestamp": "2026-09-26T21:59:00Z",
        "latest_material_update_at": "2026-09-26T21:59:00Z",
        "outcome_space": "BINARY_OUTRIGHT_WINNER",
    }


def test_minnesota_point_edge_cannot_erase_negative_lower_bound_edge():
    row = _minnesota_package()
    market_no_vig = 0.465
    friction_buffer = 0.015

    point_edge = row["calibrated_probability"] - market_no_vig
    lower_bound_edge = edge_leaderboard_score(
        row,
        no_vig_probability=market_no_vig,
        friction_buffer=friction_buffer,
    )

    assert point_edge == pytest.approx(0.0589)
    assert lower_bound_edge == pytest.approx(-0.0509)
    assert lower_bound_edge < 0.0


def test_two_hour_delay_material_update_invalidates_pre_delay_model_snapshot():
    row = _minnesota_package()
    row["latest_material_update_at"] = "2026-09-27T00:01:00Z"

    audit = validate_governed_scoring_package(row)

    assert audit.status == STALE_MODEL_OUTPUT
    assert audit.rank_eligible is False
    assert audit.scoring_allowed is False
    assert "IMMUTABLE_MODEL_TIMESTAMP_PRECEDES_LATEST_MATERIAL_UPDATE" in audit.blockers
    assert audit.can_execute is False


def test_fresh_post_delay_model_snapshot_can_reenter_normal_governance():
    row = _minnesota_package()
    row["source_snapshot_id"] = "min-tex-post-delay-snapshot"
    row["source_snapshot_timestamp"] = "2026-09-27T00:02:00Z"
    row["latest_material_update_at"] = "2026-09-27T00:01:00Z"
    row["immutable_model_timestamp"] = "2026-09-27T00:03:00Z"

    audit = validate_governed_scoring_package(row)

    assert audit.status == "PASS"
    assert audit.rank_eligible is True
    assert audit.scoring_allowed is True
    assert audit.can_execute is False
