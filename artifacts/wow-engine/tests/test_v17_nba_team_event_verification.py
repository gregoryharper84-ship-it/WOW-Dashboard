"""Contract tests for the NBA team-event independent verification harness.

One test per V-check failure mode, plus independence, evidence, read-only
source, and schema-drift guards. The fixture fits a real artifact from a
synthetic corpus using the production fitting code, so the happy path exercises
genuine recomputation rather than a stubbed shape.
"""
from __future__ import annotations

import copy
import hashlib
import json
import random
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from basketball_specialist_pipeline import (
    _walk_forward_raw,
    disjoint_calibration_and_holdout,
)
from basketball_team_event_specialist import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    MODEL_FAMILY,
    TrainingGame,
    build_pregame_features,
    train_specialist,
)
from calibration import phase_b_platt
from v17.nba_team_event_verification import (
    REQUIRED_EVIDENCE_REFS,
    SCHEMA_VERSION,
    SupabaseVerificationSource,
    VerificationContextError,
    VerificationRequest,
    MappingVerificationSource,
    verify_nba_team_event,
)

SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "v17"
    / "engineering-fixes"
    / "nba_team_event_verification_receipt.schema.json"
)
CODE_SHA = "a" * 40
SPECIALIST = "wow.nba-game-win-probability-expert"
N_DATES = 230
TEAMS = ("BOS", "NYK", "LAL", "GSW")


def _corpus(
    *, regime_flip_from: int | None = None, n_dates: int = N_DATES, seed: int = 11
):
    rng = random.Random(seed)
    strength = {team: index * 0.20 for index, team in enumerate(TEAMS)}
    wins = {team: 0 for team in TEAMS}
    played = {team: 0 for team in TEAMS}
    start = date(2025, 10, 1)
    rows = []
    counter = 0
    for day in range(n_dates):
        game_date = start + timedelta(days=day)
        for home_index, away_index in ((0, 1), (2, 3)):
            home, away = TEAMS[home_index], TEAMS[away_index]
            if day % 2 == 1:
                home, away = away, home
            edge = 0.5 + (strength[home] - strength[away])
            if (
                regime_flip_from is not None
                and day >= regime_flip_from
                and played[home]
                and played[away]
            ):
                # Holdout-only regime flip: the side with the better prior record
                # loses. The model fitted on the earlier regime is confidently
                # wrong out of sample, which is what the V8 gate must detect.
                home_rate = wins[home] / played[home]
                away_rate = wins[away] / played[away]
                home_wins = (
                    rng.random() < 0.5 if home_rate == away_rate else home_rate < away_rate
                )
            else:
                home_wins = rng.random() < edge
                # Small label noise keeps the fixture non-separable without
                # pushing the baseline Brier against the 0.25 gate.
                if rng.random() < 0.02:
                    home_wins = not home_wins
            base = rng.randint(96, 116)
            if home_wins:
                home_score, away_score = base + rng.randint(1, 14), base
            else:
                home_score, away_score = base, base + rng.randint(1, 14)
            counter += 1
            wins[home if home_wins else away] += 1
            played[home] += 1
            played[away] += 1
            rows.append(
                {
                    "game_id": f"nba-{counter:05d}",
                    "season": 2025,
                    "game_date": game_date.isoformat(),
                    "home_team_id": home,
                    "away_team_id": away,
                    "home_score": home_score,
                    "away_score": away_score,
                    "source_provider": "SPORTSDATAVERSE_ESPN_NBA",
                    "source_endpoint": f"https://example.invalid/nba/game/{counter}",
                    "source_retrieved_at": datetime.combine(
                        game_date, datetime.min.time(), tzinfo=timezone.utc
                    ).replace(hour=22).isoformat(),
                    "source_payload_sha256": hashlib.sha256(
                        f"payload-{counter}".encode()
                    ).hexdigest(),
                    "settled": True,
                }
            )
    return rows


def _training_games(corpus):
    return [
        TrainingGame(
            game_id=row["game_id"],
            sport="NBA",
            season=row["season"],
            game_date=date.fromisoformat(row["game_date"]),
            home_team_id=row["home_team_id"],
            away_team_id=row["away_team_id"],
            home_score=row["home_score"],
            away_score=row["away_score"],
        )
        for row in corpus
    ]


def _feature_rows(features):
    rows = []
    for feature in features:
        values = {
            name: float(value) for name, value in zip(FEATURE_NAMES, feature.values)
        }
        payload = {
            "sport": "NBA",
            "game_id": feature.game_id,
            "game_date": feature.game_date.isoformat(),
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "home_games_prior": feature.home_games_prior,
            "away_games_prior": feature.away_games_prior,
            "features": values,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        rows.append(
            {
                "game_id": feature.game_id,
                "as_of": datetime.combine(
                    feature.game_date, datetime.min.time(), tzinfo=timezone.utc
                ).isoformat(),
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "home_games_prior": feature.home_games_prior,
                "away_games_prior": feature.away_games_prior,
                "feature_payload": payload,
                "feature_payload_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
            }
        )
    return rows


class _Fixture:
    def __init__(self, *, regime_flip_from: int | None = None):
        self.regime_flip_from = regime_flip_from
        self.artifact_id = "0b9e5d0a-0000-4000-8000-000000000001"
        self.model_version = "NBA_TEAM_EVENT_LOGISTIC_V1_TEST"
        self.calibration_id = "NBA_TEAM_EVENT_PLATT_V1_TEST"
        self.calibration_sha = "b" * 64

        corpus = _corpus(regime_flip_from=regime_flip_from)
        games = _training_games(corpus)
        features = build_pregame_features(games, "NBA")
        artifact = train_specialist(
            features, "NBA", model_artifact_version=self.model_version
        )
        calibration_train, _holdout = disjoint_calibration_and_holdout(
            features, artifact
        )
        raw, outcomes, folds, timestamps = _walk_forward_raw(calibration_train)
        fit = phase_b_platt(raw, outcomes, folds, timestamps)
        self.calibration_ece = float(fit.metrics.ece)

        created_at = datetime.now(timezone.utc)
        fit_end = datetime.combine(
            max(row.game_date for row in calibration_train),
            datetime.max.time(),
            tzinfo=timezone.utc,
        )

        self.corpus = corpus
        self.feature_rows = _feature_rows(features)
        self.artifact = {
            "artifact_id": self.artifact_id,
            "sport": "NBA",
            "model_family": MODEL_FAMILY,
            "model_artifact_version": self.model_version,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "artifact_format": "WOW_BASKETBALL_LOGISTIC_JSON_V1",
            "artifact_payload": json.loads(json.dumps(asdict(artifact))),
            "artifact_checksum": artifact.artifact_checksum,
            "training_row_count": artifact.training_row_count,
            "train_start_date": artifact.train_start_date,
            "train_end_date": artifact.train_end_date,
            "holdout_row_count": artifact.holdout_row_count,
            "holdout_brier": artifact.holdout_brier,
            "holdout_log_loss": artifact.holdout_log_loss,
            "holdout_accuracy": artifact.holdout_accuracy,
            "promoted": True,
            "active": True,
            "created_at": created_at.isoformat(),
        }
        self.calibrator = {
            "calibrator_version": self.calibration_id,
            "model_artifact_version": self.model_version,
            "calibration_method": "PLATT_TIME_SPLIT_V1",
            "coefficients": {
                "a": float(fit.coefficients.a),
                "b": float(fit.coefficients.b),
            },
            "calibration_row_count": len(raw),
            "brier": float(fit.metrics.brier),
            "log_loss": float(fit.metrics.log_loss),
            "ece": float(fit.metrics.ece),
            "calibration_bias": float(fit.metrics.calibration_bias),
            "calibration_sha256": self.calibration_sha,
            "calibration_fit_end": fit_end.isoformat(),
            "created_at": created_at.isoformat(),
        }
        self.capability = {
            "sport": "NBA",
            "capability_status": "CERTIFIED",
            "model_artifact_version": self.model_version,
            "calibrator_version": self.calibration_id,
            "corpus_rows": len(corpus),
            "settled_rows": len(corpus),
        }
        self.attestation = {
            "source_replay_status": "RAW_CAPTURE_HASH_AND_EVENT_REPLAY_PASS",
            "timestamp_attestation": "VERIFIED",
            "dataset_id": "DATASET_NBA_TEST_V1",
            "market_features_used": False,
            "market_probability_used_as_model": False,
            "generic_reasoning_used_as_model": False,
        }
        self.certification = {
            "model_version": self.model_version,
            "artifact_sha256": artifact.artifact_checksum,
            "calibration_sha256": self.calibration_sha,
            "certification_status": "CERTIFIED",
        }

    def bundle(self):
        return {
            "corpus": copy.deepcopy(self.corpus),
            "feature_rows": copy.deepcopy(self.feature_rows),
            "artifact": copy.deepcopy(self.artifact),
            "calibrator": copy.deepcopy(self.calibrator),
            "capability": copy.deepcopy(self.capability),
            "attestation": copy.deepcopy(self.attestation),
            "certification": copy.deepcopy(self.certification),
        }

    def request(self, **overrides):
        payload = {
            "artifact_id": self.artifact_id,
            "controlling_specialist": SPECIALIST,
            "input_contract_version": "NBA_GAME_WIN_INPUTS_V1",
            "independent_verifier": "INDEPENDENT_VERIFICATION",
            "implementation_owner": "wow-fitting-pipeline",
            "verification_owner": "wow-independent-qa",
            "evidence_refs": {key: f"ref://{key}" for key in REQUIRED_EVIDENCE_REFS},
        }
        payload.update(overrides)
        return VerificationRequest(**payload)

    def verify(self, bundle, **overrides):
        return verify_nba_team_event(
            MappingVerificationSource(bundle),
            self.request(**overrides.get("request", {})),
            code_sha=CODE_SHA,
            environment="pytest",
            verification_id="11111111-2222-4333-8444-555555555555",
        )


_CACHE: dict[int | None, _Fixture] = {}


def _fixture(regime_flip_from: int | None = None) -> _Fixture:
    if regime_flip_from not in _CACHE:
        _CACHE[regime_flip_from] = _Fixture(regime_flip_from=regime_flip_from)
    return _CACHE[regime_flip_from]


@pytest.fixture(scope="module")
def fx() -> _Fixture:
    return _fixture()


@pytest.fixture(scope="module")
def flipped() -> _Fixture:
    return _fixture(regime_flip_from=195)


def _failed(receipt):
    return {check["check_id"] for check in receipt["checks"] if check["status"] == "FAIL"}


def _check(receipt, check_id):
    for entry in receipt["checks"]:
        if entry["check_id"] == check_id:
            return entry
    raise AssertionError(f"check {check_id} missing")


def _assert_failed_only(receipt, expected):
    assert receipt["independent_verification_status"] == "FAIL"
    assert receipt["verdict"]["certification_eligible"] is False
    assert receipt["verdict"]["publishable_scope"] == "NONE"
    assert receipt["can_execute"] is False
    assert _failed(receipt) == {expected}, _failed(receipt)
    assert any(expected in blocker or blocker.startswith("CHECK_FAILED") for blocker in receipt["verdict"]["blockers"])
    assert f"CHECK_FAILED:{expected}" in receipt["verdict"]["blockers"]


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #
def test_happy_path_emits_pass_receipt(fx):
    receipt = fx.verify(fx.bundle())

    assert receipt["independent_verification_status"] == "PASS"
    assert receipt["verdict"] == {
        "publishable_scope": "NONE",
        "certification_eligible": True,
        "blockers": [],
    }
    assert receipt["can_execute"] is False
    assert receipt["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert [entry["check_id"] for entry in receipt["checks"]] == [
        f"V{index}" for index in range(1, 13)
    ]
    assert _failed(receipt) == set()
    assert receipt["schema_version"] == SCHEMA_VERSION
    assert receipt["model_artifact_sha256"] == fx.artifact["artifact_checksum"]
    assert receipt["metrics"]["training_rows"] == fx.artifact["training_row_count"]
    assert receipt["metrics"]["holdout_rows"] == fx.artifact["holdout_row_count"]
    # The advisory ECE limitation is always recorded.
    assert any(entry.startswith("V9:") for entry in receipt["limitations"])


# --------------------------------------------------------------------------- #
# V1-V12 failure modes
# --------------------------------------------------------------------------- #
def test_v1_missing_corpus_provenance_fails(fx):
    bundle = fx.bundle()
    bundle["corpus"][3]["source_payload_sha256"] = ""
    receipt = fx.verify(bundle)

    _assert_failed_only(receipt, "V1")
    assert any(
        blocker.startswith("CORPUS_PROVENANCE_INCOMPLETE:")
        for blocker in _check(receipt, "V1")["blockers"]
    )


def test_v2_stale_corpus_fails(fx):
    receipt = fx.verify(
        fx.bundle(),
        request={"as_of": datetime(2028, 6, 1, tzinfo=timezone.utc)},
    )

    _assert_failed_only(receipt, "V2")
    assert any(
        blocker.startswith("CORPUS_STALE:")
        for blocker in _check(receipt, "V2")["blockers"]
    )


def test_v3_duplicate_game_id_fails(fx):
    bundle = fx.bundle()
    bundle["corpus"][7]["game_id"] = bundle["corpus"][6]["game_id"]
    receipt = fx.verify(bundle)

    _assert_failed_only(receipt, "V3")
    assert _check(receipt, "V3")["observed"]["duplicate_game_ids"]


def test_v4_tampered_feature_payload_fails(fx):
    bundle = fx.bundle()
    bundle["feature_rows"][12]["feature_payload"]["features"]["home_win_rate_prior"] += 0.01
    receipt = fx.verify(bundle)

    _assert_failed_only(receipt, "V4")
    assert any(
        blocker.startswith("FEATURE_PAYLOAD_MISMATCH:")
        for blocker in _check(receipt, "V4")["blockers"]
    )


def test_v5_tampered_artifact_payload_fails(fx):
    bundle = fx.bundle()
    bundle["artifact"]["artifact_payload"]["coefficients"][0] += 0.5
    receipt = fx.verify(bundle)

    _assert_failed_only(receipt, "V5")
    assert "ARTIFACT_CHECKSUM_MISMATCH" in _check(receipt, "V5")["blockers"]


def test_v6_tampered_holdout_metric_fails(fx):
    bundle = fx.bundle()
    bundle["artifact"]["holdout_brier"] = float(bundle["artifact"]["holdout_brier"]) + 0.01
    receipt = fx.verify(bundle)

    _assert_failed_only(receipt, "V6")
    assert "HOLDOUT_BRIER_NOT_REPRODUCIBLE" in _check(receipt, "V6")["blockers"]


def test_v7_tampered_platt_coefficient_fails(fx):
    bundle = fx.bundle()
    bundle["calibrator"]["coefficients"]["a"] = 0.0
    receipt = fx.verify(bundle)

    _assert_failed_only(receipt, "V7")
    assert "PLATT_COEFFICIENTS_NOT_REPRODUCIBLE" in _check(receipt, "V7")["blockers"]


def test_v8_fit_gate_fails_when_holdout_regime_flips(flipped):
    receipt = flipped.verify(flipped.bundle())

    _assert_failed_only(receipt, "V8")
    assert any(
        blocker.startswith("FIT_GATE_NOT_MET:")
        for blocker in _check(receipt, "V8")["blockers"]
    )
    # The stored rows still reproduce, so V6/V7 are not collateral damage.
    assert "V6" not in _failed(receipt)
    assert "V7" not in _failed(receipt)


def test_v9_declared_ece_threshold_fails(fx):
    receipt = fx.verify(
        fx.bundle(), request={"max_ece": fx.calibration_ece / 2.0}
    )

    _assert_failed_only(receipt, "V9")
    assert any(
        blocker.startswith("CALIBRATION_ECE_ABOVE_DECLARED_MAXIMUM:")
        for blocker in _check(receipt, "V9")["blockers"]
    )


def test_v10_capability_version_mismatch_fails(fx):
    bundle = fx.bundle()
    bundle["capability"]["model_artifact_version"] = "STALE_VERSION"
    receipt = fx.verify(bundle)

    _assert_failed_only(receipt, "V10")
    assert any(
        blocker.startswith("MODEL_VERSION_MISMATCH:capability:")
        for blocker in _check(receipt, "V10")["blockers"]
    )


def test_v11_unattested_source_fails(fx):
    bundle = fx.bundle()
    bundle["attestation"]["source_replay_status"] = "NOT_INDEPENDENTLY_ATTESTED"
    receipt = fx.verify(bundle)

    _assert_failed_only(receipt, "V11")
    assert any(
        blocker.startswith("SOURCE_EVENT_REPLAY_NOT_ATTESTED:")
        for blocker in _check(receipt, "V11")["blockers"]
    )


def test_v12_market_substitution_fails(fx):
    bundle = fx.bundle()
    bundle["attestation"]["market_probability_used_as_model"] = True
    receipt = fx.verify(bundle)

    _assert_failed_only(receipt, "V12")
    assert any(
        blocker.endswith("market_probability_used_as_model")
        for blocker in _check(receipt, "V12")["blockers"]
    )


# --------------------------------------------------------------------------- #
# Verdict-level rules
# --------------------------------------------------------------------------- #
def test_independence_violation_fails_even_when_every_check_passes(fx):
    receipt = fx.verify(
        fx.bundle(),
        request={"verification_owner": "wow-fitting-pipeline"},
    )

    assert receipt["independent_verification_status"] == "FAIL"
    assert _failed(receipt) == set()
    assert "VERIFICATION_INDEPENDENCE_UNPROVEN" in receipt["verdict"]["blockers"]
    assert receipt["verdict"]["certification_eligible"] is False


def test_missing_evidence_ref_fails(fx):
    refs = {key: f"ref://{key}" for key in REQUIRED_EVIDENCE_REFS}
    refs["replay_evidence_ref"] = ""
    receipt = fx.verify(fx.bundle(), request={"evidence_refs": refs})

    assert receipt["independent_verification_status"] == "FAIL"
    assert "EVIDENCE_REF_MISSING:REPLAY_EVIDENCE_REF" in receipt["verdict"]["blockers"]
    assert receipt["verdict"]["certification_eligible"] is False


def test_missing_artifact_row_is_a_context_error_not_a_receipt(fx):
    bundle = fx.bundle()
    bundle["artifact"] = None

    with pytest.raises(VerificationContextError) as excinfo:
        fx.verify(bundle)

    assert excinfo.value.code == "ARTIFACT_ROW_NOT_FOUND"


def test_missing_corpus_is_a_context_error(fx):
    bundle = fx.bundle()
    bundle["corpus"] = []

    with pytest.raises(VerificationContextError) as excinfo:
        fx.verify(bundle)

    assert excinfo.value.code == "CORPUS_EMPTY"


def test_non_nba_request_is_rejected(fx):
    with pytest.raises(VerificationContextError) as excinfo:
        fx.verify(fx.bundle(), request={"sport": "WNBA"})

    assert excinfo.value.code == "SPORT_NOT_SUPPORTED_BY_HARNESS"


# --------------------------------------------------------------------------- #
# Read-only source
# --------------------------------------------------------------------------- #
class _Query:
    def __init__(self, ops, table):
        self.ops = ops
        self.ops.append(("table", table))

    def select(self, columns):
        self.ops.append(("select", columns))
        return self

    def eq(self, key, value):
        self.ops.append(("eq", key, value))
        return self

    def order(self, column, **kwargs):
        self.ops.append(("order", column, kwargs))
        return self

    def limit(self, value):
        self.ops.append(("limit", value))
        return self

    def range(self, start, end):
        self.ops.append(("range", start, end))
        return self

    def execute(self):
        self.ops.append(("execute",))
        return SimpleNamespace(data=[])


class _ReadOnlyClient:
    def __init__(self):
        self.ops = []

    def table(self, name):
        return _Query(self.ops, name)


def test_supabase_source_never_writes():
    client = _ReadOnlyClient()
    source = SupabaseVerificationSource(client, attestation={"dataset_id": "x"})

    assert source.corpus_rows("NBA") == []
    assert source.feature_rows("NBA") == []
    assert source.artifact_row("artifact") is None
    assert source.calibrator_row("version") is None
    assert source.capability_row("NBA") is None
    assert source.certification_row("version") is None

    names = {op[0] for op in client.ops}
    assert names <= {"table", "select", "eq", "order", "limit", "range", "execute"}
    assert names & {"insert", "update", "upsert", "delete", "rpc"} == set()
    assert ("eq", "settled", True) in client.ops


# --------------------------------------------------------------------------- #
# Schema drift
# --------------------------------------------------------------------------- #
def test_receipt_matches_published_schema_contract(fx):
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    receipt = fx.verify(fx.bundle())

    assert set(schema["required"]) <= set(receipt)
    assert set(receipt) <= set(schema["properties"])
    assert receipt["schema_version"] == schema["properties"]["schema_version"]["const"]
    assert receipt["sport"] == schema["properties"]["sport"]["const"]
    assert receipt["league_scope"] == schema["properties"]["league_scope"]["const"]
    assert receipt["model_family"] == schema["properties"]["model_family"]["const"]
    assert (
        receipt["feature_schema_version"]
        == schema["properties"]["feature_schema_version"]["const"]
    )
    assert receipt["verdict"]["publishable_scope"] == "NONE"
    assert schema["properties"]["verdict"]["properties"]["publishable_scope"][
        "const"
    ] == "NONE"
    for key in ("model_artifact_sha256", "calibration_sha256"):
        assert len(receipt[key]) == 64
        assert int(receipt[key], 16) >= 0
    for entry in receipt["checks"]:
        assert set(entry) == {"check_id", "status", "observed", "blockers"}
    assert {entry["check_id"] for entry in receipt["checks"]} == {
        f"V{index}" for index in range(1, 13)
    }
    assert set(receipt["verdict"]) == {"publishable_scope", "certification_eligible", "blockers"}
    assert set(receipt["evidence_refs"]) == set(REQUIRED_EVIDENCE_REFS)
