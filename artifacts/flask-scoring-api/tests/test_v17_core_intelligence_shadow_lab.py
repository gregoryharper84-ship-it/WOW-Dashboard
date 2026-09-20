from __future__ import annotations

import sys
from pathlib import Path

WOW_ENGINE_ROOT = Path(__file__).resolve().parents[2] / "wow-engine"
if str(WOW_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(WOW_ENGINE_ROOT))

from v17.core_intelligence_shadow_lab import evaluate_shadow_package


def _rows(n: int = 120):
    rows = []
    for i in range(n):
        target = 1 if i % 2 == 0 else 0
        champion = 0.62 if target else 0.38
        challenger = 0.72 if target else 0.28
        rows.append({
            "source_row_id": f"row-{i}",
            "outcome_target": target,
            "champion_probability": champion,
            "challenger_probability": challenger,
        })
    return rows


def test_paired_shadow_lab_requires_same_rows_and_keeps_governance_false():
    evidence, audit, review = evaluate_shadow_package(
        challenger_id="candidate-1",
        target_key="MLB|MLB|OUTRIGHT_WINNER|MODEL",
        rows=_rows(),
        source_review_pass=True,
        certification_replay_pass=True,
    )
    assert len(evidence) == 120
    assert audit.holdout_n == 120
    assert audit.challenger_brier < audit.champion_brier
    assert audit.challenger_log_loss < audit.champion_log_loss
    assert review.eligible_for_governed_review is True
    assert review.automatic_promotion is False
    assert review.probability_publishable is False
    assert review.can_execute is False


def test_shadow_lab_rejects_duplicate_source_rows():
    rows = _rows(2)
    rows[1]["source_row_id"] = rows[0]["source_row_id"]
    try:
        evaluate_shadow_package(
            challenger_id="candidate-1",
            target_key="MLB|MLB|OUTRIGHT_WINNER|MODEL",
            rows=rows,
            source_review_pass=True,
            certification_replay_pass=True,
        )
    except ValueError as exc:
        assert str(exc) == "SHADOW_DUPLICATE_SOURCE_ROW"
    else:
        raise AssertionError("duplicate paired rows must fail closed")


def test_shadow_lab_blocks_even_good_metrics_without_certification_replay():
    _, _, review = evaluate_shadow_package(
        challenger_id="candidate-1",
        target_key="MLB|MLB|OUTRIGHT_WINNER|MODEL",
        rows=_rows(),
        source_review_pass=True,
        certification_replay_pass=False,
    )
    assert review.eligible_for_governed_review is False
    assert "CERTIFICATION_REPLAY_REQUIRED" in review.blockers
