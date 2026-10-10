from __future__ import annotations

import copy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest

from v17.binary_candidate_lifecycle import BinaryTrainingRow, train_binary_candidate
from v17 import ncaab_moneyline_replay as replay
from v17.ncaab_moneyline_replay import NCAABReplayInputError, _json_hash, load_frozen_inputs, verify_frozen_replay
from v17.ncaab_sportsdataverse_candidate import (
    FEATURE_NAMES, FEATURE_SCHEMA_VERSION, MODEL_FAMILY, SOURCE_POLICY_ID,
)

SHA = "a" * 40
TRAIN_SHA = "b" * 40
CID = "e874cd6d-5ea3-4b85-a1f0-ac239e285704"
CREATED = "2026-10-01T00:00:00+00:00"
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
            "created_at": "2026-09-30T12:00:00+00:00",
        })
    return out


def candidate_for(payloads):
    rows = [BinaryTrainingRow(p["official_event_id"], p["event_start_time"], p["feature_as_of"],
                              p["outcome_json"]["home_win"], p["features"], p["source_manifest_sha256"])
            for p in payloads]
    c = train_binary_candidate(rows, model_family=MODEL_FAMILY, feature_names=FEATURE_NAMES, min_rows=500)
    artifact = dict(c.artifact_payload)
    return {
        "candidate_id": CID, "training_code_sha": TRAIN_SHA, "created_at": CREATED, "sport": "NCAAB", "model_family": MODEL_FAMILY,
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
    r = verify_frozen_replay(cand, rows, expected_candidate_id=CID, verifier_sha=SHA)
    assert r["status"] == "REPLAY_REPRODUCED", r["mismatches"]
    assert r["observed"]["dataset_hash"] == cand["training_dataset_hash"]
    assert r["observed"]["splits"] == {"train_n": 360, "calibration_n": 120, "test_n": 120}
    assert r["can_execute"] is False and r["probability_publishable"] is False
    assert r["certification"] is False and r["promotion"] is False


def test_replay_is_deterministic_and_order_independent(base):
    rows, cand = base
    a = verify_frozen_replay(cand, rows, expected_candidate_id=CID, verifier_sha=SHA)
    b = verify_frozen_replay(cand, list(reversed(rows)), expected_candidate_id=CID, verifier_sha=SHA)
    assert a["receipt_sha256"] == b["receipt_sha256"]


def test_receipt_is_immutable(base):
    rows, cand = base
    r = verify_frozen_replay(cand, rows, expected_candidate_id=CID, verifier_sha=SHA)
    with pytest.raises(TypeError):
        r["status"] = "CERTIFIED"  # type: ignore[index]


def test_tampered_feature_breaks_dataset_hash(base):
    rows, cand = base
    rows = copy.deepcopy(rows)
    rows[10]["features"]["home_recent_fg_pct"] += 0.01
    assert "NCAAB_REPLAY_DATASET_HASH_MISMATCH" in codes(verify_frozen_replay(cand, rows, expected_candidate_id=CID, verifier_sha=SHA))


def test_tampered_stored_coefficients_detected(base):
    rows, cand = base
    cand = copy.deepcopy(cand)
    cand["artifact_payload"]["coefficients"][0] += 0.5
    assert "NCAAB_REPLAY_STORED_ARTIFACT_TAMPERED" in codes(verify_frozen_replay(cand, rows, expected_candidate_id=CID, verifier_sha=SHA))


def test_truncated_expected_dataset_hash_is_typed_hold(base):
    rows, cand = base
    cand = dict(cand, training_dataset_hash=cand["training_dataset_hash"][:52])
    r = verify_frozen_replay(cand, rows, expected_candidate_id=CID, verifier_sha=SHA)
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
    (lambda p: p.pop("source_manifest"), "NCAAB_REPLAY_SOURCE_MANIFEST_MISSING"),
    (lambda p: p.update(source_manifest={}), "NCAAB_REPLAY_SOURCE_MANIFEST_MISSING"),
    (lambda p: p.update(created_at="2026-10-02T00:00:00+00:00"), "NCAAB_REPLAY_ROW_POST_CANDIDATE"),
    (lambda p: p.pop("created_at"), "NCAAB_REPLAY_ROW_CREATED_AT_MISSING"),
    (lambda p: p.update(event_start_time="not-a-time"), "NCAAB_REPLAY_EVENT_TIME_INVALID"),
])
def test_negative_row_paths(base, mutate, code):
    rows, cand = base
    rows = copy.deepcopy(rows)
    mutate(rows[42])
    r = verify_frozen_replay(cand, rows, expected_candidate_id=CID, verifier_sha=SHA)
    assert code in codes(r) and r["status"] == "REPLAY_MISMATCH_HOLD"


def test_duplicate_game_version_rejected(base):
    rows, cand = base
    assert "NCAAB_REPLAY_DUPLICATE_EVENT" in codes(verify_frozen_replay(cand, rows + [copy.deepcopy(rows[5])], expected_candidate_id=CID, verifier_sha=SHA))


@pytest.mark.parametrize("key,value", [
    ("model_family", "NCAAB_DYNAMIC_TEAM_STATE_LOGIT_V2"), ("sport", "NCAAF"),
    ("promoted", True), ("can_execute", True), ("probability_publishable", True),
])
def test_wrong_specialist_or_governance_rejected(base, key, value):
    rows, cand = base
    r = verify_frozen_replay(dict(cand, **{key: value}), rows, expected_candidate_id=CID, verifier_sha=SHA)
    assert r["status"] == "REPLAY_MISMATCH_HOLD"


def test_split_and_metric_mismatch(base):
    rows, cand = base
    cand = dict(cand, test_rows=5629, validation_metrics=dict(cand["validation_metrics"], ece=0.5))
    assert {"NCAAB_REPLAY_SPLIT_MISMATCH", "NCAAB_REPLAY_METRIC_MISMATCH"} <= codes(verify_frozen_replay(cand, rows, expected_candidate_id=CID, verifier_sha=SHA))


def test_insufficient_rows_is_typed_refit_failure(base):
    rows, cand = base
    assert "NCAAB_REPLAY_REFIT_FAILED" in codes(verify_frozen_replay(cand, rows[:100], expected_candidate_id=CID, verifier_sha=SHA))


@pytest.mark.parametrize("args,code", [
    (dict(verifier_sha="abc"), "NCAAB_REPLAY_VERIFIER_SHA_REQUIRED"),
    (dict(expected_candidate_id=""), "NCAAB_REPLAY_EXPECTED_CANDIDATE_ID_REQUIRED"),
    (dict(candidate={}), "NCAAB_REPLAY_CANDIDATE_MISSING"),
    (dict(frozen_rows=[]), "NCAAB_REPLAY_FROZEN_ROWS_MISSING"),
])
def test_missing_inputs_raise_typed_errors(base, args, code):
    rows, cand = base
    kw = {"candidate": cand, "frozen_rows": rows, "expected_candidate_id": CID, "verifier_sha": SHA} | args
    with pytest.raises(NCAABReplayInputError) as exc:
        verify_frozen_replay(**kw)
    assert exc.value.code == code


def test_receipt_binds_candidate_and_separates_verifier_from_training_sha(base):
    rows, cand = base
    r = verify_frozen_replay(cand, rows, expected_candidate_id=CID, verifier_sha=SHA)
    assert r["candidate_id"] == CID and r["expected_candidate_id"] == CID
    assert r["verifier_sha"] == SHA and r["candidate_training_code_sha"] == TRAIN_SHA
    assert len(r["observed"]["corpus_identity_sha256"]) == 64
    assert len(r["observed"]["calibrator_sha256"]) == 64


@pytest.mark.parametrize("change,code", [
    ({"candidate_id": None}, "NCAAB_REPLAY_CANDIDATE_ID_MISSING"),
    ({"candidate_id": "00000000-0000-0000-0000-000000000000"}, "NCAAB_REPLAY_CANDIDATE_ID_MISMATCH"),
    ({"training_code_sha": "abc123"}, "NCAAB_REPLAY_TRAINING_CODE_SHA_INVALID"),
    ({"created_at": None}, "NCAAB_REPLAY_CANDIDATE_CREATED_AT_MISSING"),
])
def test_candidate_identity_provenance_fails_closed(base, change, code):
    rows, cand = base
    r = verify_frozen_replay(dict(cand, **change), rows, expected_candidate_id=CID, verifier_sha=SHA)
    assert code in codes(r) and r["status"] == "REPLAY_MISMATCH_HOLD"


def test_leakage_uses_timezone_aware_comparison(base):
    rows, cand = base
    rows = copy.deepcopy(rows)
    # Lexically earlier than start, but 1h AFTER start once offsets are applied.
    start = datetime.fromisoformat(rows[50]["event_start_time"])
    rows[50]["feature_as_of"] = (start + timedelta(hours=1)).astimezone(timezone(timedelta(hours=-5))).isoformat()
    assert rows[50]["feature_as_of"] < rows[50]["event_start_time"]
    assert "NCAAB_REPLAY_TEMPORAL_LEAKAGE" in codes(verify_frozen_replay(cand, rows, expected_candidate_id=CID, verifier_sha=SHA))


class _Q:
    def __init__(self, data, log):
        self.data, self.log, self.lo, self.hi = data, log, 0, None

    def select(self, *_):
        return self

    def eq(self, k, v):
        self.log.append(("eq", k, v))
        self.data = [d for d in self.data if d.get(k) == v]
        return self

    def order(self, *_):
        return self

    def range(self, lo, hi):
        self.lo, self.hi = lo, hi
        return self

    def execute(self):
        data = self.data if self.hi is None else self.data[self.lo:self.hi + 1]
        return type("R", (), {"data": data})()

    def __getattr__(self, name):  # any mutation attempt fails the test
        raise AssertionError(f"write attempted: {name}")


class _Client:
    def __init__(self, cands, rows):
        self.tables, self.log = {"wow_d1_candidate_artifacts": cands, "wow_d1_training_rows": rows}, []

    def table(self, name):
        return _Q(list(self.tables[name]), self.log)


def test_loader_is_read_only_paginates_and_round_trips(base, monkeypatch):
    rows, cand = base
    monkeypatch.setattr(replay, "PAGE_SIZE", 128)
    noise = dict(copy.deepcopy(rows[0]), sport="NCAAF", official_event_id="NCAAF:1")
    client = _Client([cand], rows + [noise])
    got_cand, got_rows, query = load_frozen_inputs(client, CID)
    assert got_cand["candidate_id"] == CID and len(got_rows) == len(rows) == query["rows_returned"]
    r = verify_frozen_replay(got_cand, got_rows, expected_candidate_id=CID, verifier_sha=SHA, source_query=query)
    assert r["status"] == "REPLAY_REPRODUCED", r["mismatches"]


def test_loader_requires_exactly_one_candidate(base):
    rows, cand = base
    with pytest.raises(NCAABReplayInputError) as exc:
        load_frozen_inputs(_Client([], rows), CID)
    assert exc.value.code == "NCAAB_REPLAY_CANDIDATE_MISSING"


def test_cli_without_credentials_is_typed_block(monkeypatch, capsys):
    for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_KEY",
              "NCAAB_REPLAY_READ_ONLY_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert replay.main([CID, SHA]) == 3
    assert "NCAAB_REPLAY_READ_CREDENTIAL_UNAVAILABLE" in capsys.readouterr().out


def test_cli_rejects_privileged_credential_substitution(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://validation.example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "privileged-key-must-not-fallback")
    monkeypatch.delenv("NCAAB_REPLAY_READ_ONLY_KEY", raising=False)
    assert replay.main([CID, SHA]) == 3
    assert "NCAAB_REPLAY_READ_CREDENTIAL_UNAVAILABLE" in capsys.readouterr().out


def test_cli_typed_failure_on_missing_candidate(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://validation.example.supabase.co")
    monkeypatch.setenv("NCAAB_REPLAY_READ_ONLY_KEY", "dedicated-select-only-test-key")
    import supabase
    monkeypatch.setattr(supabase, "create_client", lambda *_: _Client([], []))
    assert replay.main([CID, SHA]) == 3
    assert "NCAAB_REPLAY_CANDIDATE_MISSING" in capsys.readouterr().out


def test_cli_typed_failure_on_provider_read_error(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://validation.example.supabase.co")
    monkeypatch.setenv("NCAAB_REPLAY_READ_ONLY_KEY", "dedicated-select-only-test-key")
    import supabase
    monkeypatch.setattr(supabase, "create_client", lambda *_: (_ for _ in ()).throw(RuntimeError("sensitive-token")))
    assert replay.main([CID, SHA]) == 3
    output = capsys.readouterr().out
    assert "NCAAB_REPLAY_SOURCE_READ_FAILED" in output
    assert "sensitive-token" not in output
