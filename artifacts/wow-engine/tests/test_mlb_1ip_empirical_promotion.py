from dataclasses import dataclass

import pytest

from mlb_1ip_empirical_pmf import fit_empirical_pmf
from mlb_1ip_empirical_promotion import (
    FEATURE_SCHEMA_VERSION,
    REGISTRY_COLUMNS,
    SUPPORTED_LINES,
    build_empirical_promotion_payload,
    build_empirical_shadow_candidate,
    registry_row_from_promoted,
)


@dataclass(frozen=True)
class Row:
    bf: int
    pitches: int


def _artifact():
    rows = []
    rows.extend(Row(3, 12 + (i % 4)) for i in range(450))
    rows.extend(Row(4, 16 + (i % 5)) for i in range(400))
    rows.extend(Row(5, 21 + (i % 6)) for i in range(300))
    return fit_empirical_pmf(rows)


def _shadow():
    return build_empirical_shadow_candidate(
        artifact=_artifact(),
        training_dataset_hash="a" * 64,
        training_code_sha="b" * 40,
        scoring_code_sha="c" * 40,
        split_hash="d" * 64,
        source_snapshot_hashes=["e" * 64, "f" * 64],
        validation_metrics={
            "validation_rows": 1323,
            "brier": 0.20677374121890155,
            "ece": 0.014757387773260975,
            "gates_passed": True,
        },
        validated_lines=SUPPORTED_LINES,
    )


def _promote(shadow=None, **overrides):
    shadow = shadow or _shadow()
    kwargs = {
        "implementer_context": "chatgpt-implementation-session",
        "reviewer_context": "independent-review-session",
        "review_verdict": "APPROVE_FOR_PROMOTION",
        "review_evidence_hash": "1" * 64,
        "expected_artifact_checksum": shadow["artifact_checksum"],
        "expected_split_hash": shadow["validation_lineage"]["split_hash"],
        "expected_brier": shadow["validation_metrics"]["brier"],
        "expected_ece": shadow["validation_metrics"]["ece"],
    }
    kwargs.update(overrides)
    return build_empirical_promotion_payload(shadow, **kwargs)


def test_shadow_candidate_is_lineage_bound_registry_aligned_and_nonpublishable():
    shadow = _shadow()
    assert shadow["lifecycle_state"] == "SHADOW"
    assert shadow["promoted"] is False
    assert shadow["active"] is False
    assert shadow["probability_publishable"] is False
    assert shadow["can_execute"] is False
    assert shadow["feature_schema_version"] == FEATURE_SCHEMA_VERSION == "PROP_FEATURES_V1"
    assert shadow["supported_lines"] == list(SUPPORTED_LINES)
    assert shadow["validation_metrics"]["validated_lines"] == list(SUPPORTED_LINES)
    assert len(shadow["validation_lineage"]["validation_lineage_hash"]) == 64


def test_shadow_candidate_rejects_tampered_artifact_checksum():
    artifact = _artifact()
    artifact["bf_weights"]["3"] += 0.01
    with pytest.raises(ValueError, match="MLB_1IP_PROMOTION_ARTIFACT_CHECKSUM_MISMATCH"):
        build_empirical_shadow_candidate(
            artifact=artifact,
            training_dataset_hash="a" * 64,
            training_code_sha="b" * 40,
            scoring_code_sha="c" * 40,
            split_hash="d" * 64,
            source_snapshot_hashes=["e" * 64],
            validation_metrics={"validation_rows": 1323, "brier": 0.20, "ece": 0.02, "gates_passed": True},
            validated_lines=SUPPORTED_LINES,
        )


def test_shadow_candidate_rejects_line_support_mismatch():
    with pytest.raises(ValueError, match="MLB_1IP_PROMOTION_VALIDATED_LINE_SUPPORT_MISMATCH"):
        build_empirical_shadow_candidate(
            artifact=_artifact(),
            training_dataset_hash="a" * 64,
            training_code_sha="b" * 40,
            scoring_code_sha="c" * 40,
            split_hash="d" * 64,
            source_snapshot_hashes=["e" * 64],
            validation_metrics={"validation_rows": 1323, "brier": 0.20, "ece": 0.02, "gates_passed": True},
            validated_lines=[11.5, 13.5],
        )


def test_promotion_rejects_self_review():
    with pytest.raises(ValueError, match="MLB_1IP_INDEPENDENT_REVIEW_REQUIRED"):
        _promote(reviewer_context="chatgpt-implementation-session")


def test_promotion_rejects_nonapproval_verdict():
    with pytest.raises(ValueError, match="MLB_1IP_REVIEW_NOT_APPROVED"):
        _promote(review_verdict="HOLD_WITH_FINDINGS")


def test_promotion_rejects_missing_review_evidence():
    with pytest.raises(ValueError, match="MLB_1IP_REVIEW_EVIDENCE_INVALID"):
        _promote(review_evidence_hash="")


def test_promotion_rejects_checksum_split_and_metric_mismatches():
    with pytest.raises(ValueError, match="MLB_1IP_PROMOTION_ARTIFACT_CHECKSUM_MISMATCH"):
        _promote(expected_artifact_checksum="9" * 64)
    with pytest.raises(ValueError, match="MLB_1IP_PROMOTION_SPLIT_HASH_MISMATCH"):
        _promote(expected_split_hash="8" * 64)
    with pytest.raises(ValueError, match="MLB_1IP_PROMOTION_BRIER_MISMATCH"):
        _promote(expected_brier=0.21)
    with pytest.raises(ValueError, match="MLB_1IP_PROMOTION_ECE_MISMATCH"):
        _promote(expected_ece=0.03)


def test_approved_promotion_payload_still_cannot_publish_or_execute():
    promoted = _promote()
    assert promoted["lifecycle_state"] == "PROSPECTIVE_CERTIFIED"
    assert promoted["promoted"] is True
    assert promoted["active"] is True
    assert promoted["review_evidence"]["verdict"] == "APPROVE_FOR_PROMOTION"
    assert promoted["probability_publishable"] is False
    assert promoted["can_execute"] is False
    assert promoted["certification_id"].startswith("PROP-CERT-MLB-1IP-EMP-")


def test_registry_row_uses_only_existing_contract_and_embeds_review_lineage():
    row = registry_row_from_promoted(_promote())
    assert set(row) == set(REGISTRY_COLUMNS)
    assert row["feature_schema_version"] == "PROP_FEATURES_V1"
    assert row["validation_metrics"]["validated_lines"] == list(SUPPORTED_LINES)
    assert row["validation_metrics"]["validation_lineage"]["split_hash"] == "d" * 64
    assert row["validation_metrics"]["review_evidence"]["verdict"] == "APPROVE_FOR_PROMOTION"
    assert row["probability_publishable"] is False
    assert row["can_execute"] is False


def test_registry_row_refuses_unreviewed_shadow():
    with pytest.raises(ValueError, match="MLB_1IP_REGISTRY_ROW_CERTIFICATION_REQUIRED"):
        registry_row_from_promoted(_shadow())
