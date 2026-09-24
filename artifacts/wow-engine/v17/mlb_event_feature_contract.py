"""Immutable audit declaration for the certified MLB fitted feature contract.

This module is metadata only. It does not score, hydrate, calibrate, rank,
register, certify, promote, or publish a sporting probability.

Evidence for this exact ordered vector was recovered read-only from the
certified persisted artifact chain on 2026-09-23:

- model artifact: MLB_V16_V2D_CONTEXT_SHARED_SIM_R1
- feature schema: MLB_V2D_CONTEXT_V1
- model family: V2D_SHARED_NB_PLUS_CONTEXT_REGIMES
- frozen spec: MLB_V2D_2024_TRAIN_2022_2024_HOME_PRIOR_R1
- fitted trainer state: wow_mlb_v2b_run_trainer_state.feature_names
- forward snapshots: wow_mlb_forward_feature_snapshots.feature_names

The persisted fitted trainer vector and all hydrated forward snapshots observed
in that audit shared this exact 38-feature order. The digest below is a new
repository audit digest over JSON-serialized FEATURE_ORDER; it is deliberately
not presented as the frozen spec's historical ``feature_order_md5`` because the
serialization used for that legacy MD5 is not encoded in repository sources.

Declaring the fitted contract does not prove live score-time consumption.
Feature Consumption Receipts must remain UNVERIFIED until the scorer emits
explicit consumption evidence.
"""
from __future__ import annotations

import hashlib
import json
from typing import Sequence

CAN_EXECUTE = False
FEATURE_SCHEMA_VERSION = "MLB_V2D_CONTEXT_V1"
MODEL_FAMILY = "V2D_SHARED_NB_PLUS_CONTEXT_REGIMES"
MODEL_ARTIFACT_VERSION = "MLB_V16_V2D_CONTEXT_SHARED_SIM_R1"
FROZEN_SPEC_VERSION = "MLB_V2D_2024_TRAIN_2022_2024_HOME_PRIOR_R1"
FEATURE_ORDER_SOURCE = "wow_mlb_v2b_run_trainer_state.feature_names"
FORWARD_SNAPSHOT_SOURCE = "wow_mlb_forward_feature_snapshots.feature_names"

FEATURE_ORDER = (
    "is_home",
    "off_runs_pg",
    "off_hits_pg",
    "off_hr_pg",
    "off_bb_pg",
    "off_so_pg",
    "off_tb_pg",
    "off_run_diff_pg",
    "off_win_rate",
    "off_sb_pg",
    "off_cs_pg",
    "off_days_rest",
    "opp_runs_allowed_pg",
    "opp_errors_pg",
    "opp_win_rate",
    "opp_bp_era",
    "opp_bp_k_rate",
    "opp_bp_bb_rate",
    "opp_bp_hr_rate",
    "opp_bp_pitches_3d",
    "opp_bp_outs_3d",
    "opp_bp_apps_3d",
    "opp_starter_prior_starts",
    "opp_starter_era",
    "opp_starter_k_rate",
    "opp_starter_bb_rate",
    "opp_starter_h_rate",
    "opp_starter_hr_rate",
    "opp_starter_outs_per_start",
    "opp_starter_tbf_per_start",
    "opp_starter_pitches_per_start",
    "opp_starter_strike_rate",
    "opp_starter_days_rest",
    "opp_starter_pitches_last3",
    "park_total_runs_prior",
    "park_prior_games",
    "opp_days_rest",
    "min_team_prior_games",
)


def feature_order_sha256(feature_order: Sequence[str] = FEATURE_ORDER) -> str:
    payload = json.dumps(list(feature_order), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


FEATURE_ORDER_SHA256 = "eda4c6a20efade791dfaf7df4759a344c22ab9872765c4ca3c8cb27d82fd0a52"


def observed_feature_order_matches(feature_names: Sequence[str]) -> bool:
    """Audit helper only; does not alter scorer behavior or typed failures."""
    return tuple(str(value) for value in feature_names) == FEATURE_ORDER


assert feature_order_sha256() == FEATURE_ORDER_SHA256


__all__ = [
    "CAN_EXECUTE",
    "FEATURE_ORDER",
    "FEATURE_ORDER_SHA256",
    "FEATURE_ORDER_SOURCE",
    "FEATURE_SCHEMA_VERSION",
    "FORWARD_SNAPSHOT_SOURCE",
    "FROZEN_SPEC_VERSION",
    "MODEL_ARTIFACT_VERSION",
    "MODEL_FAMILY",
    "feature_order_sha256",
    "observed_feature_order_matches",
]
