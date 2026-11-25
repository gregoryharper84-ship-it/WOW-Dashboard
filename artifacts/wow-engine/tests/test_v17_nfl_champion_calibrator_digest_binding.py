"""Structural regression for the NFL champion calibrator digest binding (#1939).

The migration is a protected candidate. These tests never apply SQL, never
issue a certificate, and never change a probability. They prove that the
digest the migration writes is the canonical #2825-schema digest of the
champion's stored provenance, and that the write is narrow and fail-closed.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import nfl_event_model_v17 as model

ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = ROOT / "migrations" / "20261125_nfl_champion_calibrator_digest_binding.sql"
ROLLBACK_PATH = ROOT / "migrations" / "20261125_nfl_champion_calibrator_digest_binding_rollback.sql"

# Stored production provenance for champion artifact 7fee1209-... and
# calibrator 756379bd-... (read-only SQL, 2026-11-24 ~23:35 UTC). The
# training dataset hash was independently reproduced from
# wow_nfl_pregame_feature_rows (1,356 rows; 787/285/284), giving fit_start
# (min train gameday) and fit_end (max calibration gameday).
ARTIFACT_CHECKSUM = "aa41eefbbe93f94f9af0401a2c0a7bd9c9505569e8c3754ac396d552a0ead04b"
DATASET_HASH = "5d603a873fbb5f9885256a3e4100f16b8c3654a060d60b2d5190793fcc0e345a"
SPLIT_HASH = "5079cace62c021c7b5be2b8e04aacc6ca9cfa2ff06b7ff9ceb478419d9c43768"
TRAINING_CODE_SHA = "fa26799bf294f1bd63b7d5643c8a06a3a44a2f97"
PLATT_A = 0.9358407245138914
PLATT_B = -0.05740265013467314
UNCERTAINTY_MARGIN = 0.05591808310857731
CALIBRATION_ROWS = 285
FIT_START = "2021-10-07"
FIT_END = "2025-02-09"
STORED_FIT_METRICS = {
    "train_n": 787,
    "validation_n": 284,
    "calibration_n": 285,
    "train_seasons": [2021, 2022, 2023],
    "validation_auc": 0.6555023923444976,
    "baseline_log_loss": 0.6909632983050226,
    "validation_ece_10": 0.04190465343842191,
    "validation_season": 2025,
    "calibration_season": 2024,
    "uncertainty_margin": 0.05591808310857731,
    "validation_log_loss": 0.6529250857823151,
    "baseline_brier_score": 0.24890796301353832,
    "baseline_probability": 0.5473684210526316,
    "certification_checks": {
        "train_n": True,
        "validation_n": True,
        "calibration_n": True,
        "ece_within_limit": True,
        "auc_not_below_chance": True,
        "beats_baseline_brier": True,
        "platt_positive_slope": True,
        "beats_baseline_log_loss": True,
    },
    "validation_brier_score": 0.23115896846749356,
    "raw_validation_brier_score": 0.23275629814404553,
}


def _sql() -> str:
    return SQL_PATH.read_text()


def _payload_text() -> str:
    match = re.search(r"\$cal\$(.*?)\$cal\$", _sql(), re.S)
    assert match, "payload literal missing"
    return match.group(1)


def _digest() -> str:
    match = re.search(r"v_sha constant text := '([0-9a-f]{64})';", _sql())
    assert match, "digest literal missing"
    return match.group(1)


def _expected_payload() -> dict:
    """The #2825 NFL_PLATT_CALIBRATION_ARTIFACT_V1 shape, from stored provenance."""
    return {
        "schema_version": "NFL_PLATT_CALIBRATION_ARTIFACT_V1",
        "model_artifact_sha256": ARTIFACT_CHECKSUM,
        "model_artifact_version": f"NFL_EVENT_LOGREG_PLATT_V1_{DATASET_HASH[:12]}",
        "training_dataset_sha256": DATASET_HASH,
        "temporal_split_sha256": SPLIT_HASH,
        "training_code_sha": TRAINING_CODE_SHA,
        "calibration_version": f"NFL_PLATT_TIME_SPLIT_V1_{DATASET_HASH[:12]}",
        "calibration_method": model.CALIBRATION_METHOD,
        "platt_a": PLATT_A,
        "platt_b": PLATT_B,
        "bounds_method_version": model.BOUNDS_METHOD_VERSION,
        "uncertainty_margin": UNCERTAINTY_MARGIN,
        "calibration_rows": CALIBRATION_ROWS,
        "fit_start": FIT_START,
        "fit_end": FIT_END,
        "validation_metrics_sha256": model._sha256_json(STORED_FIT_METRICS),
    }


def test_payload_is_canonical_and_hashes_to_the_written_digest():
    text = _payload_text()
    assert model._canonical_json(json.loads(text)) == text
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == _digest()
    assert model._sha256_json(json.loads(text)) == _digest()


def test_payload_equals_stored_champion_provenance():
    assert json.loads(_payload_text()) == _expected_payload()


def test_split_hash_matches_the_champion_temporal_split():
    split = {
        "train_seasons": list(model.TRAIN_SEASONS),
        "calibration_season": model.CALIBRATION_SEASON,
        "validation_season": model.VALIDATION_SEASON,
    }
    assert model._sha256_json(split) == SPLIT_HASH


def test_digest_matches_2825_producer_when_available():
    producer = getattr(model, "_calibration_artifact_evidence", None)
    payload = _expected_payload()
    if producer is None:
        # #2825 not merged yet: the mirrored schema above is the contract.
        assert set(payload) == {
            "schema_version", "model_artifact_sha256", "model_artifact_version",
            "training_dataset_sha256", "temporal_split_sha256", "training_code_sha",
            "calibration_version", "calibration_method", "platt_a", "platt_b",
            "bounds_method_version", "uncertainty_margin", "calibration_rows",
            "fit_start", "fit_end", "validation_metrics_sha256",
        }
        return
    fitted = {
        "artifact_checksum": ARTIFACT_CHECKSUM,
        "model_artifact_version": payload["model_artifact_version"],
        "dataset_hash": DATASET_HASH,
        "split_hash": SPLIT_HASH,
        "training_code_sha": TRAINING_CODE_SHA,
        "calibration_version": payload["calibration_version"],
        "artifact_payload": {
            "calibration_method": model.CALIBRATION_METHOD,
            "platt_a": PLATT_A,
            "platt_b": PLATT_B,
            "bounds_method_version": model.BOUNDS_METHOD_VERSION,
            "uncertainty_margin": UNCERTAINTY_MARGIN,
        },
        "calibration_rows": CALIBRATION_ROWS,
        "fit_start": FIT_START,
        "fit_end": FIT_END,
        "metrics": STORED_FIT_METRICS,
    }
    artifact, digest = producer(fitted)
    assert artifact == payload
    assert digest == _digest()


def test_guards_bind_the_same_values_as_the_payload():
    sql = _sql()
    payload = json.loads(_payload_text())
    for token in (
        "c.calibrator_id = '756379bd-f557-4508-8ecb-91a5d336ec64'",
        f"c.platt_a = {payload['platt_a']!r}",
        f"c.platt_b = {payload['platt_b']!r}",
        f"c.source_data_hash = '{payload['training_dataset_sha256']}'",
        f"c.split_hash = '{payload['temporal_split_sha256']}'",
        f"c.training_n = {payload['calibration_rows']}",
        "a.artifact_id = '7fee1209-0bed-4bd0-aa0f-fc7a881eff80'",
        f"a.artifact_checksum = '{payload['model_artifact_sha256']}'",
        f"a.training_dataset_hash = '{payload['training_dataset_sha256']}'",
        f"a.training_code_sha = '{payload['training_code_sha']}'",
        "a.lifecycle_state = 'CHAMPION'",
        "a.can_execute is false",
        "not (c.fit_metrics_json ? 'calibration_artifact_sha256')",
        "not (c.fit_metrics_json ? 'calibration_artifact_payload')",
    ):
        assert token in sql, token


def test_write_is_one_update_of_fit_metrics_json_only_and_fails_closed():
    sql = _sql()
    lowered = sql.lower()
    assert lowered.count("update public.") == 1
    set_clause = re.search(r"update public\.wow_calibrators c\n\s+set (.*?)\n\s+where", sql, re.S).group(1)
    assert set_clause.strip().startswith("fit_metrics_json = c.fit_metrics_json")
    assert re.findall(r"'([a-z0-9_]+)',", set_clause) == [
        "calibration_artifact_payload", "calibration_artifact_sha256",
    ]
    for forbidden in ("insert into", "delete from", "create or replace", "alter table",
                      "drop ", "grant ", "probability_publishable", "lifecycle_state =",
                      "can_execute = true", "wow_team_event_specialist_certifications"):
        assert forbidden not in set_clause.lower()
    for forbidden in ("insert into", "delete from", "create or replace", "alter table",
                      "drop ", "grant ", "wow_team_event_specialist_certifications"):
        assert forbidden not in lowered, forbidden
    assert "if v_rows <> 1 then" in sql
    assert "NFL_CALIBRATOR_DIGEST_BINDING_PRECONDITION_FAILED" in sql
    assert "NFL_CALIBRATOR_DIGEST_SELF_CHECK_FAILED" in sql
    assert "NFL_CALIBRATOR_DIGEST_READBACK_MISMATCH" in sql
    assert "migration candidate, NOT APPLIED" in sql


def test_reducer_reads_the_key_this_migration_writes():
    reducer = (ROOT / "migrations" / "20261124_nfl_terminal_authority_v2_forward_cohort.sql").read_text()
    assert "v_calibration_sha := c.fit_metrics_json->>'calibration_artifact_sha256';" in reducer
    assert "v_cert->>'calibration_sha256'=v_calibration_sha" in reducer
    assert "'calibration_artifact_sha256', v_sha" in _sql()


def test_rollback_removes_only_the_binding_and_refuses_after_certification():
    rollback = ROLLBACK_PATH.read_text()
    assert _digest() in rollback
    assert "from public.wow_team_event_specialist_certifications" in rollback
    assert "where calibration_sha256 = v_sha" in rollback
    assert "- 'calibration_artifact_payload'" in rollback
    assert "- 'calibration_artifact_sha256'" in rollback
    assert rollback.lower().count("update public.") == 1
    assert "if v_rows <> 1 then" in rollback
