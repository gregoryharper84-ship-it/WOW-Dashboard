from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prop_distribution_contract import CoverageDecision, RawDiscreteDistribution
from v17.distribution_protocol import (
    DistributionProtocolError,
    compute_dependency_graph_hash,
    evaluate_market_line,
    verify_dependency_artifact,
    verify_distribution_protocol,
)
from v17.distribution_protocol_adapters import RawDiscreteDistributionAdapter


def _raw():
    return RawDiscreteDistribution(
        support={0: 0.10, 1: 0.20, 2: 0.30, 3: 0.40},
        coverage=CoverageDecision(True, 0.0, ()),
        model_artifact_version="model-v1",
        training_code_sha="a" * 40,
        training_dataset_hash="dataset-1",
        feature_schema_version="features-v1",
        feature_transform_sha="b" * 40,
        feature_snapshot_hash="snapshot-1",
        artifact_checksum="c" * 64,
        inference_timestamp=datetime.now(timezone.utc).isoformat(),
    )


def _adapter():
    return RawDiscreteDistributionAdapter(
        _raw(),
        distribution_version="dist-v1",
        min_line=0.5,
        max_line=3.0,
        supported_steps=(0.5,),
    )


def test_discrete_half_line_settlement_is_centralized():
    result = evaluate_market_line(_adapter(), 1.5)
    assert abs(result.probability_under - 0.30) < 1e-12
    assert abs(result.probability_push - 0.0) < 1e-12
    assert abs(result.probability_over - 0.70) < 1e-12


def test_discrete_whole_line_preserves_push_mass():
    result = evaluate_market_line(_adapter(), 2.0)
    assert abs(result.probability_under - 0.30) < 1e-12
    assert abs(result.probability_push - 0.30) < 1e-12
    assert abs(result.probability_over - 0.40) < 1e-12


def test_line_outside_certified_domain_fails_typed():
    try:
        evaluate_market_line(_adapter(), 4.5)
    except DistributionProtocolError as exc:
        assert exc.code == "LINE_OUT_OF_SPECIALIST_DOMAIN"
    else:
        raise AssertionError("out-of-domain line was evaluated")


def test_distribution_fingerprint_is_deterministic():
    first = verify_distribution_protocol(_adapter(), 1.5)
    second = verify_distribution_protocol(_adapter(), 1.5)
    assert first["distribution_fingerprint"] == second["distribution_fingerprint"]
    assert len(first["distribution_fingerprint"]) == 64
    assert first["can_execute"] is False


def test_dependency_graph_hash_is_order_independent_and_bound():
    deps_a = {
        "exposure": "nba_minutes_v4",
        "points": "nba_points_v3",
        "rebounds": "nba_rebounds_v2",
        "assists": "nba_assists_v2",
        "dependency": "pra_dep_v1",
        "calibration": "pra_cal_v3",
    }
    deps_b = dict(reversed(list(deps_a.items())))
    graph_hash = compute_dependency_graph_hash(deps_a)
    assert graph_hash == compute_dependency_graph_hash(deps_b)
    receipt = verify_dependency_artifact(
        dependencies=deps_a,
        expected_graph_hash=graph_hash,
        dependency_artifact_sha="d" * 64,
    )
    assert receipt["dependency_graph_bound"] is True
    assert receipt["can_execute"] is False


def test_dependency_version_change_invalidates_binding():
    deps = {"points": "nba_points_v3", "dependency": "pra_dep_v1"}
    old_hash = compute_dependency_graph_hash(deps)
    deps["points"] = "nba_points_v4"
    try:
        verify_dependency_artifact(
            dependencies=deps,
            expected_graph_hash=old_hash,
            dependency_artifact_sha="e" * 64,
        )
    except DistributionProtocolError as exc:
        assert exc.code == "DEPENDENCY_ARTIFACT_MISMATCH"
    else:
        raise AssertionError("changed dependency was accepted under stale binding")
