from __future__ import annotations

import copy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest

from v17.binary_candidate_lifecycle import BinaryTrainingRow, train_binary_candidate
from v17.ncaab_moneyline_replay import NCAABReplayInputError, _json_hash, verify_frozen_replay
from v17.ncaab_sportsdataverse_candidate import (
    FEATURE_NAMES, FEATURE_SCHEMA_VERSION, MODEL_FAMILY, SOURCE_POLICY_ID,
)

SHA = "a" * 40
BASE = datetime(2022, 11, 7, tzinfo=timezone.utc)


def frozen(n=600):
    out = []
    for i in range(n):
        start = BASE + timedelta(hours=6 * i)
        edge = 1.0 if i % 3 else -1.0
        feats = {name: float(((i * (k + 3)) % 17) / 17.0) for k, name in enumerate(FEATURE_NAMES)}
        feats["home_recent_point_diff"] = 6.0 * edge
        feats["away_recent_point_diff"] = -2.0 * edge
        manifest = {"policy": SOURCE_POLICY_ID, "game_id": f"{400000000 + i}", "market_features_used": False}
        out.append({
            "sport": "NCAAB", "official_event_id": f"NCAAB:{400000000 + i}",
            "event_start_time": start.isoformat(), "feature_as_of": (start - timedelta(seconds=1)).isoformat(),
            "feature_schema_version": FEATURE_SCHEMA_VERSION, "model_family": MODEL_FAMILY,
            "features": feats, "outcome_json": {"home_win": (edge > 0) != (i % 13 == 0)},
            "source_manifest": manifest, "source_manifest_sha256": _json_hash(manifest),
            "market_features_used": False, "can_execute": False,
        })
    return out


def candidate_for(payloads):
    rows = [BinaryTrainingRow(p["official_event_id"], p["event_start_time"], p["feature_as_of"],
                              p["outcome_json"]["home_win"], p["features"], p["source_manifest_sha256"])
            for p in payloads]
    c = train_binary_candidate(rows, model_family=MODEL_FAMILY, feature_names=FEATURE_NAMES, min_rows=500)
    artifact = dict(c.artifact_payload)
    return {
        "id": "test-candidate", "sport": "NCAAB", "model_family": MODEL_FAMILY,
        "feature_schema_version": FEATURE_SCHEMA_VERSION, "source_policy_id": SOURCE_POLICY_ID,
        "training_dataset_hash": c.dataset_hash, "artifact_checksum": _json_hash(artifact),
        "artifact_payload": artifact, "validation_metrics": asdict(c.metrics),
        "training_rows": c.metrics.train_n, "calibration_rows": c.metrics.calibration_n, "test_rows": c.metrics.test_n,
        "promoted": False, "active": False, "probability_publishable": False, "can_execute": False,
    }


@pytest.fixture(scope="module")
def base():
    rows = frozen()
    return rows, candidate_for(rows)


def codes(receipt):
    return {m["code"] for m in receipt["mismatches"]}


def test_exact_replay_reproduces_and_stays_research_only(base):
    rows, cand = base
    r = verify_frozen_replay(cand, rows, code_sha=SHA)
    assert r["status"] == "REPLAY_REPRODUCED", r["mismatches"]
    assert r["observed"]["dataset_hash"] == cand["training_dataset_hash"]
    assert r["observed"]["splits"] == {"train_n": 360, "calibration_n": 120, "test_n": 120}
    assert r["can_execute"] is False and r["probability_publishable"] is False
    assert r["certification"] is False and r["promotion"] is False


def test_replay_is_deterministic_and_order_independent(base):
    rows, cand = base
    a = verify_frozen_replay(cand, rows, code_sha=SHA)
    b = verify_frozen_replay(cand, list(reversed(rows)), code_sha=SHA)
    assert a["receipt_sha256"] == b["receipt_sha256"]


def test_receipt_is_immutable(base):
    rows, cand = base
    r = verify_frozen_replay(cand, rows, code_sha=SHA)
    with pytest.raises(TypeError):
        r["status"] = "CERTIFIED"  # type: ignore[index]


def test_tampered_feature_breaks_dataset_hash(base):
    rows, cand = base
    rows = copy.deepcopy(rows)
    rows[10]["features"]["home_recent_fg_pct"] += 0.01
    assert "NCAAB_REPLAY_DATASET_HASH_MISMATCH" in codes(verify_frozen_replay(cand, rows, code_sha=SHA))


def test_tampered_stored_coefficients_detected(base):
    rows, cand = base
    cand = copy.deepcopy(cand)
    cand["artifact_payload"]["coefficients"][0] += 0.5
    assert "NCAAB_REPLAY_STORED_ARTIFACT_TAMPERED" in codes(verify_frozen_replay(cand, rows, code_sha=SHA))


def test_truncated_expected_dataset_hash_is_typed_hold(base):
    rows, cand = base
    cand = dict(cand, training_dataset_hash=cand["training_dataset_hash"][:52])
    r = verify_frozen_replay(cand, rows, code_sha=SHA)
    assert r["status"] == "REPLAY_MISMATCH_HOLD"
    assert "NCAAB_REPLAY_EXPECTED_DATASET_HASH_INVALID" in codes(r)


@pytest.mark.parametrize("mutate,code", [
    (lambda p: p.update(feature_as_of=p["event_start_time"]), "NCAAB_REPLAY_TEMPORAL_LEAKAGE"),
    (lambda p: p.update(market_features_used=True), "NCAAB_REPLAY_MARKET_FEATURES_USED"),
    (lambda p: p.update(can_execute=True), "NCAAB_REPLAY_CAN_EXECUTE_VIOLATION"),
    (lambda p: p.update(feature_schema_version="NCAAB_DYNAMIC_TEAM_STATE_V2"), "NCAAB_REPLAY_FEATURE_SCHEMA_MISMATCH"),
    (lambda p: p["features"].update(home_moneyline_implied=0.6), "NCAAB_REPLAY_FEATURE_SET_MISMATCH"),
    (lambda p: p["features"].update(home_recent_fg_pct=float("nan")), "NCAAB_REPLAY_FEATURE_INVALID"),
    (lambda p: p["outcome_json"].update(home_win=None), "NCAAB_REPLAY_OUTCOME_INVALID"),
    (lambda p: p["source_manifest"].update(market_features_used=True), "NCAAB_REPLAY_SOURCE_MANIFEST_TAMPERED"),
])
def test_negative_row_paths(base, mutate, code):
    rows, cand = base
    rows = copy.deepcopy(rows)
    mutate(rows[42])
    r = verify_frozen_replay(cand, rows, code_sha=SHA)
    assert code in codes(r) and r["status"] == "REPLAY_MISMATCH_HOLD"


def test_duplicate_game_version_rejected(base):
    rows, cand = base
    assert "NCAAB_REPLAY_DUPLICATE_EVENT" in codes(verify_frozen_replay(cand, rows + [copy.deepcopy(rows[5])], code_sha=SHA))


@pytest.mark.parametrize("key,value", [
    ("model_family", "NCAAB_DYNAMIC_TEAM_STATE_LOGIT_V2"), ("sport", "NCAAF"),
    ("promoted", True), ("can_execute", True), ("probability_publishable", True),
])
def test_wrong_specialist_or_governance_rejected(base, key, value):
    rows, cand = base
    r = verify_frozen_replay(dict(cand, **{key: value}), rows, code_sha=SHA)
    assert r["status"] == "REPLAY_MISMATCH_HOLD"


def test_split_and_metric_mismatch(base):
    rows, cand = base
    cand = dict(cand, test_rows=5629, validation_metrics=dict(cand["validation_metrics"], ece=0.5))
    assert {"NCAAB_REPLAY_SPLIT_MISMATCH", "NCAAB_REPLAY_METRIC_MISMATCH"} <= codes(verify_frozen_replay(cand, rows, code_sha=SHA))


def test_insufficient_rows_is_typed_refit_failure(base):
    rows, cand = base
    assert "NCAAB_REPLAY_REFIT_FAILED" in codes(verify_frozen_replay(cand, rows[:100], code_sha=SHA))


@pytest.mark.parametrize("args,code", [
    (dict(code_sha="abc"), "NCAAB_REPLAY_CODE_SHA_REQUIRED"),
    (dict(candidate={}), "NCAAB_REPLAY_CANDIDATE_MISSING"),
    (dict(frozen_rows=[]), "NCAAB_REPLAY_FROZEN_ROWS_MISSING"),
])
def test_missing_inputs_raise_typed_errors(base, args, code):
    rows, cand = base
    kw = {"candidate": cand, "frozen_rows": rows, "code_sha": SHA} | args
    with pytest.raises(NCAABReplayInputError) as exc:
        verify_frozen_replay(**kw)
    assert exc.value.code == code
