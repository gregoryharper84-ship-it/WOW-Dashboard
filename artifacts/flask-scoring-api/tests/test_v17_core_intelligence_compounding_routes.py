from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

WOW_ENGINE_ROOT = Path(__file__).resolve().parents[2] / "wow-engine"
if str(WOW_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(WOW_ENGINE_ROOT))

from v17.core_intelligence_compounding_routes import read_compounding_summary


class _ReadOnlyQuery:
    def __init__(self, rows):
        self._rows = rows
        self._limit = None

    def select(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, value):
        self._limit = value
        return self

    def execute(self):
        rows = self._rows if self._limit is None else self._rows[: self._limit]
        return SimpleNamespace(data=rows)


class _ReadOnlyDb:
    def __init__(self, tables):
        self.tables = tables
        self.reads = []

    def table(self, name):
        self.reads.append(name)
        return _ReadOnlyQuery(self.tables.get(name, []))


def test_compounding_summary_get_path_reads_persisted_memory_without_writes():
    db = _ReadOnlyDb({
        "wow_intelligence_market_scorecards": [{"snapshot_id": "m1"}],
        "wow_intelligence_signal_scorecards": [{"snapshot_id": "s1"}],
        "wow_intelligence_specialist_scorecards": [{"snapshot_id": "sp1"}],
        "wow_intelligence_challenger_proposals": [{"proposal_id": "c1"}],
        "wow_intelligence_promotion_reviews": [
            {"review_id": "r1", "eligible_for_governed_review": True},
            {"review_id": "r2", "eligible_for_governed_review": False},
        ],
    })

    summary = read_compounding_summary(db, limit=10)

    assert summary["status"] == "PASS"
    assert summary["read_only"] is True
    assert summary["can_execute"] is False
    assert summary["market_scorecard_n"] == 1
    assert summary["signal_scorecard_n"] == 1
    assert summary["specialist_scorecard_n"] == 1
    assert summary["challenger_proposal_n"] == 1
    assert summary["promotion_review_n"] == 2
    assert summary["promotion_eligible_n"] == 1
    assert db.reads == [
        "wow_intelligence_market_scorecards",
        "wow_intelligence_signal_scorecards",
        "wow_intelligence_specialist_scorecards",
        "wow_intelligence_challenger_proposals",
        "wow_intelligence_promotion_reviews",
    ]
