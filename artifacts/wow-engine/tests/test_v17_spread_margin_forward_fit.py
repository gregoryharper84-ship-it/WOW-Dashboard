from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from v17.spread_margin_challenger import (
    MarginTrainingRow,
    SpreadChallengerUnavailable,
    train_margin_distribution_candidate,
)
from v17.spread_margin_forward_fit import fit_margin_distribution_artifact


def _row(index: int, *, leaked: bool = False) -> MarginTrainingRow:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=index)
    x1 = float((index % 9) - 4)
    x2 = float(((index * 3) % 11) - 5)
    deterministic_noise = float(((index * 7) % 5) - 2)
    margin = int(round(1.7 * x1 - 0.8 * x2 + deterministic_noise))
    feature_as_of = start + timedelta(seconds=1) if leaked else start - timedelta(seconds=1)
    return MarginTrainingRow(
        event_id=f"event-{index:04d}",
        event_start_time=start.isoformat(),
        feature_as_of=feature_as_of.isoformat(),
        margin=margin,
        features={"x1": x1, "x2": x2},
        source_manifest_sha256=f"sha-{index}",
    )


def test_forward_fit_artifact_exactly_matches_full_challenger_train_artifact():
    rows = [_row(i) for i in range(120)]
    full_artifact, _metrics = train_margin_distribution_candidate(
        rows,
        sport="NFL",
        min_rows=100,
        ridge_alpha=4.0,
    )
    forward_artifact = fit_margin_distribution_artifact(
        rows,
        sport="NFL",
        min_rows=100,
        ridge_alpha=4.0,
    )

    assert forward_artifact == full_artifact
    assert forward_artifact.payload() == full_artifact.payload()


def test_forward_fit_preserves_typed_feature_leakage_failure():
    rows = [_row(i) for i in range(120)]
    rows[70] = _row(70, leaked=True)

    with pytest.raises(SpreadChallengerUnavailable) as forward_exc:
        fit_margin_distribution_artifact(rows, sport="NFL", min_rows=100, ridge_alpha=4.0)
    with pytest.raises(SpreadChallengerUnavailable) as full_exc:
        train_margin_distribution_candidate(rows, sport="NFL", min_rows=100, ridge_alpha=4.0)

    assert forward_exc.value.code == full_exc.value.code == "SPREAD_FEATURE_LEAKAGE"
    assert forward_exc.value.code != "MODEL_UNAVAILABLE"


def test_forward_fit_preserves_typed_feature_schema_failure():
    rows = [_row(i) for i in range(120)]
    bad = rows[30]
    rows[30] = MarginTrainingRow(
        event_id=bad.event_id,
        event_start_time=bad.event_start_time,
        feature_as_of=bad.feature_as_of,
        margin=bad.margin,
        features={"x1": 1.0, "different": 2.0},
        source_manifest_sha256=bad.source_manifest_sha256,
    )

    with pytest.raises(SpreadChallengerUnavailable) as forward_exc:
        fit_margin_distribution_artifact(rows, sport="NFL", min_rows=100, ridge_alpha=4.0)
    with pytest.raises(SpreadChallengerUnavailable) as full_exc:
        train_margin_distribution_candidate(rows, sport="NFL", min_rows=100, ridge_alpha=4.0)

    assert forward_exc.value.code == full_exc.value.code == "SPREAD_FEATURE_SCHEMA_MISMATCH"
    assert forward_exc.value.code != "MODEL_UNAVAILABLE"
