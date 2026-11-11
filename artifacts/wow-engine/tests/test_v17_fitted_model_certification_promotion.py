"""Cross-sport fitted-model certification replay and promotion regressions."""
from __future__ import annotations

import copy
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI

from v17.fitted_model_certification_promotion import (
    DEFAULT_REQUIRED_METRICS,
    PROMOTION_ALREADY_APPLIED,
    PROMOTION_APPROVED,
    PROMOTION_AUTHORIZATION_INVALID,
    PROMOTION_BLOCKED,
    PROMOTION_POLICY_REVIEW_REQUIRED,
    PROMOTION_READY,
    REPLAY_FAILED,
    REPLAY_PASS,
    REMAIN_SHADOW,
    PromotionAuthorization,
    PromotionPolicy,
    add_promotion_blockers,
    build_cross_sport_matrix,
    certification_sha256,
    compute_replay_metrics,
    corpus_sha256,
    evaluate_fitted_model_promotion,
    parse_fitted_artifact,
    parse_replay_row,
    replay_fitted_artifact,
    sign_authorization,
    sign_policy,
)
from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS
from v17.fitted_model_certification_service import (
    FittedMatrixRequest,
    FittedPromotionRequest,
    FittedReplayRequest,
    install_fitted_model_certification_promotion_routes,
    run_fitted_model_matrix,
    run_fitted_model_promotion,
    run_fitted_model_replay,
)

SOURCE_SHA = sha256(b"source-manifest").hexdigest()
TRAIN_HASH = sha256(b"training-dataset").hexdigest()
ARTIFACT_SHA = sha256(b"artifact-checksum").hexdigest()
CALIBRATOR_SHA = sha256(b"calibrator-checksum").hexdigest()
CODE_SHA = sha256(b"training-code").hexdigest()[:40]
POLICY_RATIFIED_AT = "2026-11-01T00:00:00+00:00"
DECISION_AT = "2026-11-02T00:00:00+00:00"
TEST_APPROVAL_KEY = "test-fitted-promotion-approval-key"


def _row(
    row_id: str,
    event_id: str,
    start: str,
    partition: str,
    probability: float | list[float],
    outcome: float | list[float],
    **overrides,
):
    payload = {
        "row_id": row_id,
        "official_event_id": event_id,
        "event_start_time": start,
        "feature_as_of": start.replace("T18:00:00", "T14:00:00"),
        "partition": partition,
        "settlement_state": "SETTLED",
        "probabilities": [probability] if isinstance(probability, (int, float)) else probability,
        "outcome": [outcome] if isinstance(outcome, (int, float)) else outcome,
        "lower_bounds": None,
        "source_manifest_sha256": SOURCE_SHA,
        "controlling_specialist": "TEST_SPECIALIST",
        "market_features_used": False,
        "can_execute": False,
    }
    payload.update(overrides)
    return payload


def _binary_rows():
    return [
        _row("r1", "e1", "2026-04-01T18:00:00+00:00", "TRAIN", 0.8, 1),
        _row("r2", "e2", "2026-04-02T18:00:00+00:00", "TRAIN", 0.6, 0),
        _row("r3", "e3", "2026-04-03T18:00:00+00:00", "CALIBRATION", 0.5, 1),
        _row("r4", "e4", "2026-04-04T18:00:00+00:00", "TEST", 0.2, 0),
    ]


def _multiclass_rows():
    return [
        _row("m1", "s1", "2026-05-01T18:00:00+00:00", "TRAIN", [0.6, 0.3, 0.1], [1, 0, 0]),
        _row("m2", "s2", "2026-05-02T18:00:00+00:00", "TRAIN", [0.2, 0.5, 0.3], [0, 1, 0]),
        _row("m3", "s3", "2026-05-03T18:00:00+00:00", "CALIBRATION", [0.1, 0.2, 0.7], [0, 0, 1]),
        _row("m4", "s4", "2026-05-04T18:00:00+00:00", "TEST", [0.5, 0.25, 0.25], [1, 0, 0]),
    ]


def _identity(rows, **overrides):
    payload = {
        "artifact_id": "artifact-binary-1",
        "sport": "NBA",
        "league": "NBA",
        "surface": "TEAM_EVENT",
        "model_family": "NBA_TEST_BINARY_LOGIT_V1",
        "model_artifact_version": "NBA_TEST_BINARY_LOGIT_V1_deadbeef",
        "feature_schema_version": "v1",
        "controlling_specialist": "TEST_SPECIALIST",
        "artifact_author_id": "model-author",
        "training_dataset_hash": TRAIN_HASH,
        "training_code_sha": CODE_SHA,
        "artifact_checksum": ARTIFACT_SHA,
        "calibrator_id": "calibrator-1",
        "calibrator_checksum": CALIBRATOR_SHA,
        "calibration_method": "PLATT",
        "expected_corpus_sha256": corpus_sha256(
            [parse_replay_row(row) for row in rows]
        ),
    }
    payload.update(overrides)
    return payload


def _artifact(rows=None, *, identity_overrides=None, stored_metrics=None, **overrides):
    rows = rows or _binary_rows()
    # Stored metrics are the TEST-only holdout metrics, not an all-partition
    # average. Computing them here keeps fixtures aligned with the contract.
    computed = compute_replay_metrics([parse_replay_row(row) for row in rows])
    metrics = stored_metrics or {
        name: computed[name] for name in ("brier", "log_loss", "ece")
    }
    payload = {
        "identity": _identity(rows, **(identity_overrides or {})),
        "rows": rows,
        "stored_metrics": metrics,
        "split_counts": {"train_n": 2, "calibration_n": 1, "test_n": 1},
    }
    payload.update(overrides)
    return payload


def _rebind(artifact):
    rows = [parse_replay_row(row) for row in artifact["rows"]]
    artifact["identity"]["expected_corpus_sha256"] = corpus_sha256(rows)
    computed = compute_replay_metrics(rows)
    artifact["stored_metrics"] = {
        name: computed[name] for name in ("brier", "log_loss", "ece")
    }
    return artifact


def _policy(**overrides):
    payload = {
        "policy_id": "TEST_POLICY_V1",
        "status": "RATIFIED",
        "scope_sport": "ALL",
        "scope_surface": "ALL",
        "min_settled_rows": 4,
        "min_test_rows": 1,
        "max_brier": 0.25,
        "max_log_loss": 1.0,
        "max_ece": 0.5,
        "require_test_holdout": True,
        "ratified_by": "WOW_GOVERNANCE",
        "ratified_at": POLICY_RATIFIED_AT,
        "notes": "test policy",
    }
    payload.update(overrides)
    return PromotionPolicy(**payload)


def _signed_policy(**overrides):
    """Build a policy signed by the test governance key."""
    return sign_policy(_policy(**overrides), TEST_APPROVAL_KEY)


def _signed_authorization(receipt, policy, artifact, **overrides):
    """Build an authorization signed by the test governance key."""
    return sign_authorization(
        _authorization(receipt, policy, artifact, **overrides),
        TEST_APPROVAL_KEY,
    )


def _authorization(receipt, policy, artifact, **overrides):
    identity = artifact["identity"] if isinstance(artifact, dict) else artifact.identity.as_dict()
    payload = {
        "authorization_id": "AUTH-1",
        "policy_id": policy.policy_id,
        "evidence_sha256": receipt["receipt_sha256"],
        "artifact_checksum": identity["artifact_checksum"],
        "reviewer_id": "independent-reviewer",
        "reviewer_role": "INDEPENDENT_MODEL_REVIEWER",
        "decision": "APPROVE",
        "decided_at": DECISION_AT,
    }
    payload.update(overrides)
    return PromotionAuthorization(**payload)


# ---------------------------------------------------------------------------
# Deterministic replay
# ---------------------------------------------------------------------------


def test_known_binary_replay_reproduces_metrics_and_never_authorizes():
    receipt = replay_fitted_artifact(_artifact())
    assert receipt["status"] == REPLAY_PASS
    assert receipt["blockers"] == []
    assert receipt["metrics"]["row_n"] == 1
    assert receipt["metrics"]["brier"] == pytest.approx(0.04, abs=1e-12)
    assert receipt["metrics"]["log_loss"] == pytest.approx(0.22314355131420976, abs=1e-12)
    assert receipt["metrics"]["ece"] == pytest.approx(0.2, abs=1e-12)
    assert set(receipt["verified_stored_metrics"]) == {"brier", "log_loss", "ece"}
    assert receipt["unverified_stored_metrics"] == []
    assert len(receipt["receipt_sha256"]) == 64
    for field in (
        "automatic_certification",
        "automatic_promotion",
        "probability_publishable",
        "can_execute",
    ):
        assert receipt[field] is False
    assert receipt["global_terminal_reducer"] == "V17_TERMINAL_REDUCER"


def test_replay_is_deterministic_and_tamper_evident():
    first = replay_fitted_artifact(_artifact())
    second = replay_fitted_artifact(_artifact())
    assert first["receipt_sha256"] == second["receipt_sha256"]

    tampered = copy.deepcopy(_artifact())
    tampered["rows"][3]["probabilities"] = [0.7]
    result = replay_fitted_artifact(tampered)
    assert result["status"] == REPLAY_FAILED
    assert "FITTED_CERTIFICATION_CORPUS_SHA256_MISMATCH" in result["blockers"]
    assert "FITTED_CERTIFICATION_STORED_METRIC_MISMATCH:brier" in result["blockers"]
    assert "FITTED_CERTIFICATION_STORED_METRIC_MISMATCH:log_loss" in result["blockers"]
    assert result["can_execute"] is False


def test_malformed_packet_is_typed_and_never_model_unavailable():
    result = replay_fitted_artifact({"identity": {}})
    assert result["status"] == REPLAY_FAILED
    assert result["identity"] is None
    assert any(code.startswith("FITTED_CERTIFICATION_") for code in result["blockers"])
    assert "MODEL_UNAVAILABLE" not in result["blockers"]


def test_temporal_leakage_partition_overlap_and_split_mismatch_fail_closed():
    leak = copy.deepcopy(_artifact())
    leak["rows"][0]["feature_as_of"] = leak["rows"][0]["event_start_time"]
    leaked = replay_fitted_artifact(leak)
    assert "FITTED_CERTIFICATION_TEMPORAL_LEAKAGE" in leaked["blockers"]

    overlap = copy.deepcopy(_artifact())
    overlap["rows"][2]["official_event_id"] = "e1"
    overlapped = replay_fitted_artifact(overlap)
    assert "FITTED_CERTIFICATION_PARTITION_EVENT_OVERLAP" in overlapped["blockers"]

    reordered = copy.deepcopy(_artifact())
    reordered["rows"][2]["event_start_time"] = "2026-03-01T18:00:00+00:00"
    assert "FITTED_CERTIFICATION_PARTITION_TEMPORAL_ORDER_INVALID" in replay_fitted_artifact(reordered)["blockers"]

    split_mismatch = copy.deepcopy(_artifact())
    split_mismatch["split_counts"]["train_n"] = 3
    assert "FITTED_CERTIFICATION_SPLIT_COUNT_MISMATCH:TRAIN" in replay_fitted_artifact(split_mismatch)["blockers"]


def test_mixed_probability_arity_fails_closed():
    rows = _binary_rows()
    rows[0]["probabilities"] = [0.3, 0.3, 0.4]
    rows[0]["outcome"] = [0, 1, 0]
    result = replay_fitted_artifact(_artifact(rows))
    assert result["status"] == REPLAY_FAILED
    assert "FITTED_CERTIFICATION_PROBABILITY_ARITY_INCONSISTENT" in result["blockers"]


def test_multiclass_soccer_replay_and_probability_normalisation():
    rows = _multiclass_rows()
    metrics = compute_replay_metrics([parse_replay_row(row) for row in rows])
    artifact = _artifact(
        rows,
        identity_overrides={
            "sport": "SOCCER",
            "league": "EPL",
            "model_family": "SOCCER_1X2_MULTINOMIAL_LOGIT_V1",
            "model_artifact_version": "SOCCER_1X2_MULTINOMIAL_LOGIT_V1_v1",
        },
        stored_metrics=metrics,
    )
    assert replay_fitted_artifact(artifact)["status"] == REPLAY_PASS

    invalid = copy.deepcopy(artifact)
    invalid["rows"][0]["probabilities"] = [0.6, 0.3, 0.2]
    _rebind(invalid)
    result = replay_fitted_artifact(invalid)
    assert "FITTED_CERTIFICATION_PROBABILITY_NORMALISATION_INVALID" in result["blockers"]
    assert result["status"] == REPLAY_FAILED


def test_probability_domain_lower_bound_and_push_states_are_preserved():
    domain = copy.deepcopy(_artifact())
    domain["rows"][0]["probabilities"] = [1.0]
    _rebind(domain)
    assert "FITTED_CERTIFICATION_PROBABILITY_DOMAIN_INVALID" in replay_fitted_artifact(domain)["blockers"]

    bounds = copy.deepcopy(_artifact())
    bounds["rows"][0]["lower_bounds"] = [0.9]
    _rebind(bounds)
    assert "FITTED_CERTIFICATION_LOWER_BOUND_ORDER_INVALID" in replay_fitted_artifact(bounds)["blockers"]

    pushed = copy.deepcopy(_artifact())
    pushed["rows"][2]["settlement_state"] = "PUSH"
    _rebind(pushed)
    result = replay_fitted_artifact(pushed)
    assert "FITTED_CERTIFICATION_NON_SETTLED_OUTCOME_FORBIDDEN" in result["blockers"]
    assert result["settlement_counts"]["PUSH"] == 1
    assert result["settlement_counts"]["SETTLED"] == 3


def test_identity_source_and_market_feature_governance_are_mandatory():
    missing_source = _artifact()
    del missing_source["rows"][0]["source_manifest_sha256"]
    result = replay_fitted_artifact(missing_source)
    assert "FITTED_CERTIFICATION_SOURCE_MANIFEST_HASH_INVALID" in result["blockers"]

    market = _artifact()
    market["rows"][0]["market_features_used"] = True
    assert "FITTED_CERTIFICATION_MARKET_FEATURES_FORBIDDEN" in replay_fitted_artifact(market)["blockers"]

    executable = _artifact(can_execute=True)
    assert "FITTED_CERTIFICATION_CAN_EXECUTE_FORBIDDEN" in replay_fitted_artifact(executable)["blockers"]

    automatic = _artifact(automatic_promotion=True)
    assert "FITTED_CERTIFICATION_AUTOMATIC_PROMOTION_FORBIDDEN" in replay_fitted_artifact(automatic)["blockers"]


def test_unverified_stored_metrics_are_reported_not_silently_trusted():
    artifact = _artifact()
    artifact["stored_metrics"]["auc"] = 0.71
    receipt = replay_fitted_artifact(artifact)
    assert receipt["status"] == REPLAY_PASS
    assert "auc" in receipt["unverified_stored_metrics"]

    mismatch = _artifact()
    mismatch["stored_metrics"]["brier"] = 0.15
    result = replay_fitted_artifact(mismatch)
    assert "brier" not in result["verified_stored_metrics"]
    assert "FITTED_CERTIFICATION_STORED_METRIC_MISMATCH:brier" in result["blockers"]


# ---------------------------------------------------------------------------
# Promotion policy and independent authorization
# ---------------------------------------------------------------------------


def test_promotion_requires_an_explicit_ratified_policy():
    artifact = _artifact()
    no_policy = evaluate_fitted_model_promotion(artifact)
    assert no_policy["status"] == PROMOTION_POLICY_REVIEW_REQUIRED
    assert no_policy["blockers"] == ["PROMOTION_POLICY_REQUIRED"]
    assert no_policy["apply_packet"] is None

    draft = _policy(status="DRAFT")
    review = evaluate_fitted_model_promotion(artifact, policy=draft)
    assert review["status"] == PROMOTION_POLICY_REVIEW_REQUIRED
    assert "PROMOTION_POLICY_NOT_RATIFIED" in review["blockers"]

    scoped = _policy(scope_sport="NFL")
    scoped_result = evaluate_fitted_model_promotion(artifact, policy=scoped)
    assert scoped_result["status"] == PROMOTION_POLICY_REVIEW_REQUIRED
    assert "PROMOTION_POLICY_SCOPE_MISMATCH" in scoped_result["blockers"]


def test_promotion_requires_all_holdout_metrics_even_if_caller_requests_row_count_only():
    # Research replay may use a narrow diagnostic. Promotion never may.
    artifact = _artifact(stored_metrics={"row_n": 1})
    research = replay_fitted_artifact(artifact, required_metrics=["row_n"])
    assert research["status"] == REPLAY_PASS
    assert research["verified_stored_metrics"] == ["row_n"]

    for selected_metrics in (["row_n"], ()):  # empty cannot waive core metrics
        result = evaluate_fitted_model_promotion(
            artifact, policy=_policy(), required_metrics=selected_metrics,
        )
        assert result["status"] == PROMOTION_BLOCKED
        assert result["apply_packet"] is None
        for metric in ("brier", "log_loss", "ece"):
            assert f"FITTED_CERTIFICATION_STORED_METRIC_MISSING:{metric}" in result["blockers"]
        assert result["probability_publishable"] is False
        assert result["can_execute"] is False

    complete = _artifact()
    complete["stored_metrics"]["row_n"] = 1
    ready = evaluate_fitted_model_promotion(
        complete, policy=_policy(), required_metrics=["row_n"],
    )
    assert ready["status"] == PROMOTION_READY
    assert set(ready["replay"]["verified_stored_metrics"]) == {
        "brier", "log_loss", "ece", "row_n",
    }


def test_policy_metric_gate_failure_remains_shadow():
    artifact = _artifact()
    tight = _policy(max_brier=0.01)
    result = evaluate_fitted_model_promotion(artifact, policy=tight)
    assert result["status"] == REMAIN_SHADOW
    assert "PROMOTION_POLICY_GATE_FAILED:brier" in result["blockers"]
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False

    review = evaluate_fitted_model_promotion(_artifact(), policy=_policy(min_settled_rows=5))
    assert review["status"] == REMAIN_SHADOW
    assert "PROMOTION_POLICY_GATE_FAILED:min_settled_rows" in review["blockers"]


def test_promotion_requires_independent_authorization_bound_to_exact_receipt():
    artifact = _artifact()
    policy = _policy()
    receipt = replay_fitted_artifact(artifact)
    ready = evaluate_fitted_model_promotion(artifact, policy=policy)
    assert ready["status"] == PROMOTION_READY
    assert ready["apply_packet"] is None

    wrong_evidence = _authorization(
        {"receipt_sha256": sha256(b"wrong").hexdigest()}, policy, artifact
    )
    invalid = evaluate_fitted_model_promotion(artifact, policy=policy, authorization=wrong_evidence)
    assert invalid["status"] == PROMOTION_AUTHORIZATION_INVALID
    assert "PROMOTION_AUTHORIZATION_EVIDENCE_MISMATCH" in invalid["blockers"]

    author_review = _authorization(receipt, policy, artifact, reviewer_id="model-author")
    author_result = evaluate_fitted_model_promotion(artifact, policy=policy, authorization=author_review)
    assert author_result["status"] == PROMOTION_AUTHORIZATION_INVALID
    assert "PROMOTION_AUTHORIZATION_AUTHOR_NOT_INDEPENDENT" in author_result["blockers"]

    rejection = _authorization(receipt, policy, artifact, decision="REJECT")
    rejected = evaluate_fitted_model_promotion(artifact, policy=policy, authorization=rejection)
    assert "PROMOTION_AUTHORIZATION_DECISION_NOT_APPROVE" in rejected["blockers"]

    early = _authorization(
        receipt, policy, artifact, decided_at="2026-10-31T00:00:00+00:00"
    )
    early_result = evaluate_fitted_model_promotion(
        artifact, policy=policy, authorization=early
    )
    assert early_result["status"] == PROMOTION_AUTHORIZATION_INVALID
    assert "PROMOTION_AUTHORIZATION_BEFORE_POLICY_RATIFICATION" in early_result["blockers"]


def test_approved_promotion_is_a_dry_run_packet_not_a_registry_mutation():
    artifact = _artifact()
    policy = _policy()
    receipt = replay_fitted_artifact(artifact)
    authorization = _authorization(receipt, policy, artifact)
    packet = evaluate_fitted_model_promotion(artifact, policy=policy, authorization=authorization)
    assert packet["status"] == PROMOTION_APPROVED
    assert packet["blockers"] == []
    assert packet["apply_packet"]["dry_run"] is True
    assert packet["apply_packet"]["requires_privileged_application"] is True
    assert packet["apply_packet"]["publication_authorized"] is False
    assert packet["apply_packet"]["target_registry_flags"] == {
        "lifecycle_state": "CERTIFIED",
        "promoted": True,
        "active": True,
        "probability_publishable": False,
        "can_execute": False,
    }
    assert packet["probability_publishable"] is False
    assert packet["automatic_promotion"] is False
    assert packet["can_execute"] is False
    assert len(packet["promotion_sha256"]) == 64

    again = evaluate_fitted_model_promotion(artifact, policy=policy, authorization=authorization)
    assert again["promotion_sha256"] == packet["promotion_sha256"]

    rehashed = add_promotion_blockers(packet, ("TEST_EXTRA",), status=PROMOTION_BLOCKED)
    assert rehashed["promotion_sha256"] == certification_sha256(
        {key: value for key, value in rehashed.items() if key != "promotion_sha256"}
    )
    assert rehashed["apply_packet"] is None


def test_already_promoted_state_requires_matching_authorization():
    promoted = copy.deepcopy(_artifact())
    promoted.update(
        {"promoted": True, "active": True, "lifecycle_state": "PROSPECTIVE_CERTIFIED"}
    )
    policy = _policy()
    blocked = evaluate_fitted_model_promotion(promoted, policy=policy)
    assert blocked["status"] == PROMOTION_BLOCKED
    assert "PROMOTED_STATE_WITHOUT_PROMOTION_AUTHORIZATION" in blocked["blockers"]

    receipt = replay_fitted_artifact(promoted)
    authorization = _authorization(receipt, policy, promoted)
    applied = evaluate_fitted_model_promotion(
        promoted, policy=policy, authorization=authorization
    )
    assert applied["status"] == PROMOTION_ALREADY_APPLIED
    assert applied["apply_packet"] is None


# ---------------------------------------------------------------------------
# Cross-sport matrix
# ---------------------------------------------------------------------------


def test_matrix_covers_every_required_sport_and_surface_without_authority():
    mlb = _artifact(
        identity_overrides={
            "artifact_id": "artifact-mlb-1",
            "sport": "MLB",
            "league": "MLB",
            "model_family": "MLB_TEST_BINARY_LOGIT_V1",
            "model_artifact_version": "MLB_TEST_BINARY_LOGIT_V1_v1",
            "artifact_checksum": sha256(b"mlb-artifact").hexdigest(),
        }
    )
    nfl = _artifact(
        identity_overrides={
            "artifact_id": "artifact-nfl-1",
            "sport": "NFL",
            "league": "NFL",
            "model_family": "NFL_TEST_BINARY_LOGIT_V1",
            "model_artifact_version": "NFL_TEST_BINARY_LOGIT_V1_v1",
            "artifact_checksum": sha256(b"nfl-artifact").hexdigest(),
        }
    )
    nba_prop = _artifact(
        identity_overrides={
            "artifact_id": "artifact-nba-prop-1",
            "sport": "NBA",
            "league": "NBA",
            "surface": "PROP",
            "stat_type": "POINTS",
            "model_family": "NBA_POINTS_TEST_V1",
            "model_artifact_version": "NBA_POINTS_TEST_V1_v1",
            "artifact_checksum": sha256(b"nba-prop-artifact").hexdigest(),
        }
    )
    tennis = _artifact(
        identity_overrides={
            "artifact_id": "artifact-tennis-1",
            "sport": "TENNIS",
            "league": "ATP",
            "model_family": "TENNIS_TEST_BINARY_LOGIT_V1",
            "model_artifact_version": "TENNIS_TEST_BINARY_LOGIT_V1_v1",
            "artifact_checksum": sha256(b"tennis-artifact").hexdigest(),
        }
    )
    policy = _policy()
    mlb_receipt = replay_fitted_artifact(mlb)
    authorization = _authorization(mlb_receipt, policy, mlb)

    matrix = build_cross_sport_matrix(
        [mlb, nfl, nba_prop, tennis],
        policies=[policy],
        authorizations=[authorization],
    )
    assert matrix["status"] == "FITTED_MODEL_CERTIFICATION_MATRIX_COMPLETE"
    assert len(matrix["lanes"]) == len(EXPECTED_TEAM_EVENT_SPORTS) * 2
    assert {row["sport"] for row in matrix["lanes"]} == set(EXPECTED_TEAM_EVENT_SPORTS)
    assert {row["surface"] for row in matrix["lanes"]} == {"TEAM_EVENT", "PROP"}
    assert len(matrix["sports"]) == 13
    assert matrix["summary"]["required_sports"] == 13
    assert matrix["summary"]["required_lanes"] == 26
    assert matrix["summary"]["cataloged_lanes"] == 26
    assert matrix["summary"]["lanes_with_artifact"] == 4
    assert matrix["summary"]["promotion_approved_lanes"] == 1
    assert matrix["summary"]["build_required_lanes"] == 4
    assert all(row["can_execute"] is False for row in matrix["lanes"])
    assert matrix["can_execute"] is False
    assert matrix["probability_publishable"] is False

    lanes = {(row["sport"], row["surface"]): row for row in matrix["lanes"]}
    assert lanes[("MLB", "TEAM_EVENT")]["status"] == PROMOTION_APPROVED
    assert lanes[("NFL", "TEAM_EVENT")]["status"] == PROMOTION_READY
    assert lanes[("NBA", "PROP")]["status"] == PROMOTION_READY
    assert lanes[("TENNIS", "TEAM_EVENT")]["status"] == PROMOTION_READY
    assert lanes[("BOXING", "TEAM_EVENT")]["status"] == "MODEL_BUILD_REQUIRED"
    assert lanes[("PGA", "PROP")]["status"] == "CANDIDATE_EVIDENCE_MISSING"
    assert build_cross_sport_matrix(
        [mlb, nfl, nba_prop, tennis],
        policies=[policy],
        authorizations=[authorization],
    )["matrix_sha256"] == matrix["matrix_sha256"]


def test_matrix_keeps_matching_authorization_rejection():
    artifact = _artifact()
    policy = _policy()
    receipt = replay_fitted_artifact(artifact)
    rejection = _authorization(receipt, policy, artifact, decision="REJECT")
    matrix = build_cross_sport_matrix(
        [artifact],
        policies=[policy],
        authorizations=[rejection],
    )
    lane = next(
        row for row in matrix["lanes"]
        if row["sport"] == "NBA" and row["surface"] == "TEAM_EVENT"
    )
    assert lane["status"] == PROMOTION_AUTHORIZATION_INVALID
    assert "PROMOTION_AUTHORIZATION_DECISION_NOT_APPROVE" in lane["blockers"]


def test_matrix_policy_ambiguity_is_explicit():
    policy_a = _policy(policy_id="AMBIGUOUS_A")
    policy_b = _policy(policy_id="AMBIGUOUS_B")
    matrix = build_cross_sport_matrix(
        [_artifact()],
        policies=[policy_a, policy_b],
    )
    lane = next(
        row for row in matrix["lanes"]
        if row["sport"] == "NBA" and row["surface"] == "TEAM_EVENT"
    )
    assert "PROMOTION_POLICY_AMBIGUOUS" in lane["blockers"]
    assert lane["status"] == PROMOTION_POLICY_REVIEW_REQUIRED


# ---------------------------------------------------------------------------
# Durable receipts, routes and migration
# ---------------------------------------------------------------------------


class _FakeQuery:
    def __init__(self, db, table):
        self.db = db
        self.table_name = table
        self.filters = {}
        self.limit_n = None
        self.mode = "select"
        self.record = None

    def select(self, *_args, **_kwargs):
        self.mode = "select"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, value):
        self.limit_n = value
        return self

    def insert(self, record):
        self.mode = "insert"
        self.record = dict(record)
        return self

    def upsert(self, record, on_conflict=None, ignore_duplicates=False):
        self.mode = "upsert"
        self.record = dict(record)
        self.on_conflict = on_conflict
        self.ignore_duplicates = ignore_duplicates
        return self

    @staticmethod
    def _hash(value):
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
        )

    def _validate_schema(self, row):
        if row.get("surface") not in {"TEAM_EVENT", "PROP", "__UNIDENTIFIED__"}:
            raise ValueError("wow_fitted_replay_surface")
        if self.table_name == "wow_fitted_model_certification_replay_receipts":
            corpus = row.get("received_corpus_sha256")
            if corpus != "__UNIDENTIFIED__" and not self._hash(corpus):
                raise ValueError("wow_fitted_replay_corpus_hash_shape")
            if not self._hash(row.get("receipt_sha256")):
                raise ValueError("wow_fitted_replay_receipt_hash_shape")
        if self.table_name == "wow_fitted_model_promotion_receipts":
            replay_hash = row.get("replay_receipt_sha256")
            if replay_hash != "__UNIDENTIFIED__" and not self._hash(replay_hash):
                raise ValueError("wow_fitted_promotion_replay_hash_shape")
            if not self._hash(row.get("promotion_sha256")):
                raise ValueError("wow_fitted_promotion_packet_hash_shape")

    def execute(self):
        rows = self.db.tables.setdefault(self.table_name, [])
        if self.mode in {"insert", "upsert"}:
            self._validate_schema(self.record)
            if self.mode == "upsert" and self.ignore_duplicates:
                keys = [key for key in (self.on_conflict or "").split(",") if key]
                if keys and any(
                    all(row.get(key) == self.record.get(key) for key in keys)
                    for row in rows
                ):
                    return SimpleNamespace(data=[])
            row = dict(self.record)
            row.setdefault("receipt_id", f"receipt-{len(rows) + 1}")
            rows.append(row)
            return SimpleNamespace(data=[row])
        matched = [
            row for row in rows
            if all(row.get(key) == value for key, value in self.filters.items())
        ]
        if self.limit_n is not None:
            matched = matched[: self.limit_n]
        return SimpleNamespace(data=matched)


class _FakeDB:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return _FakeQuery(self, name)


class _BrokenDB:
    def table(self, _name):
        raise RuntimeError("receipt store unavailable")


def test_replay_receipt_is_idempotent_and_audit_is_explicit():
    db = _FakeDB()
    request = FittedReplayRequest(artifact=_artifact())
    first = run_fitted_model_replay(request, db=db)
    assert first["status"] == REPLAY_PASS
    assert first["receipt_persisted"] is True
    assert first["receipt_idempotent"] is False
    assert first["audit_complete"] is True

    second = run_fitted_model_replay(request, db=db)
    assert second["receipt_id"] == first["receipt_id"]
    assert second["receipt_idempotent"] is True

    malformed = FittedReplayRequest(artifact={"identity": {}})
    failed_first = run_fitted_model_replay(malformed, db=db)
    failed_second = run_fitted_model_replay(malformed, db=db)
    assert failed_first["status"] == REPLAY_FAILED
    assert failed_first["receipt_persisted"] is True
    assert failed_second["receipt_idempotent"] is True
    assert failed_second["receipt_id"] == failed_first["receipt_id"]


def test_promotion_receipt_is_idempotent_and_broken_store_blocks_apply():
    db = _FakeDB()
    artifact = _artifact()
    policy = _signed_policy()
    receipt = replay_fitted_artifact(artifact)
    authorization = _signed_authorization(receipt, policy, artifact)
    request = FittedPromotionRequest(
        artifact=artifact,
        policy=policy.as_dict(),
        authorization=authorization.as_dict(),
    )
    first = run_fitted_model_promotion(
        request,
        db=db,
        approval_key=TEST_APPROVAL_KEY,
    )
    assert first["status"] == PROMOTION_APPROVED
    assert first["replay_receipt_persisted"] is True
    assert first["promotion_receipt_persisted"] is True
    assert first["audit_complete"] is True

    second = run_fitted_model_promotion(
        request,
        db=db,
        approval_key=TEST_APPROVAL_KEY,
    )
    assert second["promotion_receipt_id"] == first["promotion_receipt_id"]
    assert second["promotion_receipt_idempotent"] is True

    broken = run_fitted_model_promotion(
        request,
        db=_BrokenDB(),
        approval_key=TEST_APPROVAL_KEY,
    )
    assert broken["status"] == PROMOTION_BLOCKED
    assert broken["apply_packet"] is None
    assert "PROMOTION_REPLAY_RECEIPT_NOT_PERSISTED" in broken["blockers"]
    assert "PROMOTION_RECEIPT_NOT_PERSISTED" in broken["blockers"]
    assert broken["audit_complete"] is False

    replay_broken = run_fitted_model_replay(
        FittedReplayRequest(artifact=artifact), db=_BrokenDB()
    )
    assert replay_broken["status"] == REPLAY_PASS
    assert replay_broken["receipt_persisted"] is False
    assert replay_broken["audit_complete"] is False
    assert replay_broken["receipt_store_error"] == "RuntimeError"


@pytest.mark.parametrize("selected_metrics", (["row_n"], []))
def test_promotion_service_persists_the_same_full_metric_replay_it_evaluates(
    selected_metrics,
):
    """Promotion may add diagnostics but cannot persist a narrower replay."""
    db = _FakeDB()
    artifact = _artifact()
    artifact["stored_metrics"]["row_n"] = 1
    policy = _signed_policy()
    effective_metrics = tuple(
        dict.fromkeys((*DEFAULT_REQUIRED_METRICS, *selected_metrics))
    )
    receipt = replay_fitted_artifact(artifact, required_metrics=effective_metrics)
    authorization = _signed_authorization(receipt, policy, artifact)

    result = run_fitted_model_promotion(
        FittedPromotionRequest(
            artifact=artifact,
            policy=policy.as_dict(),
            authorization=authorization.as_dict(),
            required_metrics=selected_metrics,
        ),
        db=db,
        approval_key=TEST_APPROVAL_KEY,
    )

    assert result["status"] == PROMOTION_APPROVED
    assert result["audit_complete"] is True
    stored_replays = db.tables["wow_fitted_model_certification_replay_receipts"]
    assert len(stored_replays) == 1
    stored = stored_replays[0]
    assert stored["receipt_sha256"] == result["replay"]["receipt_sha256"]
    assert result["replay_receipt_id"] == stored["receipt_id"]
    assert set(stored["payload"]["verified_stored_metrics"]) == set(effective_metrics)


@pytest.mark.parametrize("selected_metrics", (["row_n"], []))
def test_promotion_service_blocks_missing_holdout_metrics(selected_metrics):
    """Reject service-level promotion when only a diagnostic metric is stored."""
    db = _FakeDB()
    artifact = _artifact(stored_metrics={"row_n": 1})
    policy = _signed_policy()
    row_count_receipt = replay_fitted_artifact(artifact, required_metrics=["row_n"])
    assert row_count_receipt["status"] == REPLAY_PASS
    authorization = _signed_authorization(row_count_receipt, policy, artifact)

    result = run_fitted_model_promotion(
        FittedPromotionRequest(
            artifact=artifact,
            policy=policy.as_dict(),
            authorization=authorization.as_dict(),
            required_metrics=selected_metrics,
        ),
        db=db,
        approval_key=TEST_APPROVAL_KEY,
    )

    assert result["status"] == PROMOTION_BLOCKED
    assert result["apply_packet"] is None
    assert result["replay_receipt_persisted"] is True
    assert result["replay"]["status"] == REPLAY_FAILED
    for metric in DEFAULT_REQUIRED_METRICS:
        assert (
            f"FITTED_CERTIFICATION_STORED_METRIC_MISSING:{metric}"
            in result["blockers"]
        )
    stored_replays = db.tables["wow_fitted_model_certification_replay_receipts"]
    assert len(stored_replays) == 1
    assert stored_replays[0]["status"] == REPLAY_FAILED
    assert stored_replays[0]["receipt_sha256"] == result["replay"]["receipt_sha256"]


def test_service_rejects_unsigned_policy_and_authorization():
    db = _FakeDB()
    request = FittedPromotionRequest(
        artifact=_artifact(),
        policy=_policy().as_dict(),
    )
    result = run_fitted_model_promotion(
        request,
        db=db,
        approval_key=TEST_APPROVAL_KEY,
    )
    assert result["status"] == "FITTED_MODEL_CERTIFICATION_CONTRACT_FAILED"
    assert result["code"] == "PROMOTION_TRUST_ROOT_INVALID"
    assert "PROMOTION_POLICY_SIGNATURE_INVALID" in result["blockers"]
    assert result["can_execute"] is False


def test_invalid_policy_payload_is_a_typed_contract_failure():
    result = run_fitted_model_promotion(
        FittedPromotionRequest(artifact=_artifact(), policy={"policy_id": "INCOMPLETE"}),
        db=None,
    )
    assert result["status"] == "FITTED_MODEL_CERTIFICATION_CONTRACT_FAILED"
    assert result["code"] == "PROMOTION_POLICY_INVALID"
    assert result["can_execute"] is False


def test_matrix_service_and_route_installation_are_idempotent():
    policy = _policy()
    matrix = run_fitted_model_matrix(
        FittedMatrixRequest(
            artifacts=[_artifact()],
            policies=[policy.as_dict()],
        )
    )
    assert matrix["summary"]["cataloged_lanes"] == 26
    assert matrix["can_execute"] is False

    app = FastAPI()
    kwargs = {
        # Production passes an already-built Depends(...) seam; exercise that
        # exact shape rather than only a bare callable.
        "auth_dependency": Depends(lambda: None),
        "db_client_fn": _FakeDB,
    }
    install_fitted_model_certification_promotion_routes(app, **kwargs)
    install_fitted_model_certification_promotion_routes(app, **kwargs)
    paths = [getattr(route, "path", None) for route in app.router.routes]
    assert paths.count("/internal/v17/fitted-model-certification/replay") == 1
    assert paths.count("/internal/v17/fitted-model-certification/promotion") == 1
    assert paths.count("/internal/v17/fitted-model-certification/matrix") == 1
    operation_ids = {
        getattr(route, "operation_id", None) for route in app.router.routes
    }
    assert "runWowV17FittedModelCertificationReplay" in operation_ids
    assert "runWowV17FittedModelPromotionDecision" in operation_ids
    assert "runWowV17FittedModelCertificationMatrix" in operation_ids


def test_migration_is_append_only_rls_protected_and_never_authorizing():
    sql = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "20261111_v17_fitted_model_certification_replay_promotion.sql"
    ).read_text()
    assert "wow_fitted_model_certification_replay_receipts" in sql
    assert "wow_fitted_model_promotion_receipts" in sql
    assert "enable row level security" in sql
    assert "revoke all" in sql
    assert "grant select, insert" in sql
    assert "probability_publishable = false" in sql
    assert "can_execute = false" in sql
    assert "before update or delete" in sql
    assert "WOW_FITTED_MODEL_RECEIPTS_ARE_APPEND_ONLY" in sql
    assert "create unique index" in sql
