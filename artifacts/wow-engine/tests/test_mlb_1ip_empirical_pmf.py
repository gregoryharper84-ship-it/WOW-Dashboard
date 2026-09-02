from dataclasses import dataclass

import pytest

from mlb_1ip_empirical_pmf import (
    ARTIFACT_FORMAT,
    MODEL_FAMILY,
    fit_empirical_pmf,
    score_empirical_pmf,
)


@dataclass(frozen=True)
class Row:
    bf: int
    pitches: int


def _rows():
    rows = []
    rows.extend(Row(3, 12 + (i % 4)) for i in range(450))
    rows.extend(Row(4, 16 + (i % 5)) for i in range(400))
    rows.extend(Row(5, 21 + (i % 6)) for i in range(300))
    return rows


def test_fit_empirical_pmf_is_compact_nonpublishable_and_deterministic():
    artifact_a = fit_empirical_pmf(_rows())
    artifact_b = fit_empirical_pmf(_rows())

    assert artifact_a["model_family"] == MODEL_FAMILY
    assert artifact_a["artifact_format"] == ARTIFACT_FORMAT
    assert artifact_a["training_rows"] == 1150
    assert artifact_a["artifact_checksum"] == artifact_b["artifact_checksum"]
    assert artifact_a["probability_publishable"] is False
    assert artifact_a["can_execute"] is False
    assert abs(sum(artifact_a["bf_weights"].values()) - 1.0) < 1e-12


def test_score_empirical_pmf_returns_exact_probability_mass():
    artifact = fit_empirical_pmf(_rows())
    more = score_empirical_pmf(artifact, line_value=15.5, side="MORE")
    less = score_empirical_pmf(artifact, line_value=15.5, side="LESS")

    assert more["selected_probability"] == more["P_MORE"]
    assert less["selected_probability"] == less["P_LESS"]
    assert abs(more["P_MORE"] + more["P_LESS"] + more["prob_push"] - 1.0) < 1e-12
    assert more["probability_publishable"] is False
    assert more["can_execute"] is False


def test_empirical_pmf_rejects_insufficient_training_sample():
    with pytest.raises(ValueError, match="MLB_1IP_TRAINING_ROWS_INSUFFICIENT"):
        fit_empirical_pmf([Row(3, 12)] * 999)


def test_empirical_pmf_rejects_wrong_artifact_family():
    artifact = fit_empirical_pmf(_rows())
    artifact["model_family"] = "WRONG"
    with pytest.raises(ValueError, match="MLB_1IP_ARTIFACT_MODEL_FAMILY_INVALID"):
        score_empirical_pmf(artifact, line_value=15.5, side="MORE")
