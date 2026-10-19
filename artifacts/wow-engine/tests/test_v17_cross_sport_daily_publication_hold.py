from __future__ import annotations

from copy import deepcopy

import pytest

from v17 import cross_sport_discovery_feed as discovery_feed
from v17 import cross_sport_winner_discovery as discovery
from v17 import daily_snapshot_runtime as daily


def _model_completed_row(*, audit, rank_eligible=True, publishable=True):
    row = {
        "sport": "NFL",
        "official_event_id": "nfl-official-regression-1",
        "commence_time_utc": "2026-10-20T18:00:00Z",
        "home_team": "Test Home",
        "away_team": "Test Away",
        "bucket": discovery.MODEL_COMPLETED,
        "rank_eligible": rank_eligible,
        "probability_publishable": publishable,
        "detail": {
            "calibrated_probability": 0.64,
            "calibrated_lower_bound": 0.55,
            "model_invoked": True,
        },
        "can_execute": False,
    }
    if audit is not None:
        row["publication_chain_audit"] = audit
    return row


def _daily_cross_row(monkeypatch, row):
    monkeypatch.setattr(discovery_feed, "enabled", lambda: True)
    monkeypatch.setattr(
        discovery,
        "run_cross_sport_winner_scan",
        lambda **_kwargs: {"rows": [deepcopy(row)], "reconciliation": {"row_reconciliation": "PASS"}},
    )
    result = daily._cross_sport_moneyline_rows(
        daily.DailySnapshotRequest(
            requested_slate_date="2026-10-20",
            requested_timezone="America/Chicago",
            lanes=["MONEYLINE"],
            max_props=0,
            max_team_events=1,
        ),
        run_id="regression-crosssport-publication",
        event_api=object(),
        covered_event_ids=set(),
        fetch_sport_events=lambda *_a, **_k: [],
    )
    assert result is not None
    rows, _scan = result
    assert len(rows) == 1
    return rows[0]


@pytest.mark.parametrize(
    ("audit", "expected_blocker"),
    [
        (None, "PUBLICATION_CHAIN_AUDIT_MISSING"),
        ({"status": "BLOCKED", "rank_eligible": False,
          "first_blocker": "PROBABILITY_PACKAGE_NOT_VALID"},
         "PROBABILITY_PACKAGE_NOT_VALID"),
        ({"status": "PASS", "rank_eligible": False, "first_blocker": None},
         "PUBLICATION_CHAIN_NOT_APPROVED"),
        ({"status": "UNKNOWN", "rank_eligible": True, "first_blocker": None},
         "PUBLICATION_CHAIN_NOT_APPROVED"),
    ],
)
def test_cross_sport_completed_scorer_must_hold_without_publication_proof(
    monkeypatch, audit, expected_blocker
):
    row = _daily_cross_row(monkeypatch, _model_completed_row(audit=audit))
    assert row["row_status"] == "HELD"
    assert row["probability_publishable"] is False
    assert row["can_execute"] is False
    assert row["result"]["bucket"] == discovery.MODEL_COMPLETED
    assert row["result"]["probability_publishable"] is False
    assert row["result"]["rank_eligible"] is False
    assert row["result"]["publication_blocker"] == expected_blocker
    assert row["result"]["prepublication_claim"] == {
        "probability_publishable": True,
        "rank_eligible": True,
    }
    # Preserve genuine fitted-model evidence for review; never replace it with
    # odds, generic predictions, or an invented model failure.
    assert row["result"]["detail"]["calibrated_probability"] == 0.64
    assert row["result"]["detail"]["calibrated_lower_bound"] == 0.55


def test_cross_sport_passed_publication_keeps_approved_completion(monkeypatch):
    row = _daily_cross_row(
        monkeypatch,
        _model_completed_row(audit={"status": "PASS", "rank_eligible": True, "first_blocker": None}),
    )
    assert row["row_status"] == "COMPLETED"
    assert row["probability_publishable"] is True
    assert row["result"]["probability_publishable"] is True
    assert row["result"]["rank_eligible"] is True
    assert row["can_execute"] is False


def test_cross_sport_unavailable_model_remains_distinct_from_stage_proof_hold(monkeypatch):
    source = _model_completed_row(audit=None, rank_eligible=False, publishable=False)
    source["bucket"] = discovery.MODEL_UNAVAILABLE
    source["detail"]["code"] = "MODEL_UNAVAILABLE"
    row = _daily_cross_row(monkeypatch, source)
    assert row["row_status"] == "HELD"
    assert row["probability_publishable"] is False
    assert row["result"]["bucket"] == discovery.MODEL_UNAVAILABLE
    assert row["result"]["detail"]["code"] == "MODEL_UNAVAILABLE"
    assert "publication_blocker" not in row["result"]
    assert row["can_execute"] is False
