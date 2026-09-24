from datetime import datetime, timezone

from v17.engineering_effectiveness_scorecard import is_followup, summarize, superseded_by


NOW = datetime(2026, 9, 24, 17, 0, tzinfo=timezone.utc)


def test_followup_detection_uses_explicit_or_recognizable_repair_metadata() -> None:
    assert is_followup({"title": "fix", "body": "Followup-Of: #800"}) is True
    assert is_followup({"title": "fix", "body": "Replacement for stale #792"}) is True
    assert is_followup({"title": "fix", "body": "ordinary independent repair"}) is False
    assert is_followup({"title": "fix", "body": "Run post-merge acceptance after deploy."}) is False


def test_superseded_by_requires_explicit_machine_marker() -> None:
    assert superseded_by({"body": "Superseded-By: #805"}) == 805
    assert superseded_by({"body": "This might eventually be replaced."}) is None


def test_scorecard_reports_followup_churn_and_stale_open_work() -> None:
    merged = [
        {
            "number": 1,
            "title": "first pass",
            "body": "independent fix",
            "merged_at": "2026-09-24T12:00:00Z",
        },
        {
            "number": 2,
            "title": "follow-up",
            "body": "Followup-Of: #1",
            "merged_at": "2026-09-24T13:00:00Z",
        },
        {
            "number": 3,
            "title": "old",
            "body": "independent",
            "merged_at": "2026-09-19T13:00:00Z",
        },
    ]
    open_prs = [
        {
            "number": 10,
            "title": "stale",
            "body": "Superseded-By: #2",
            "created_at": "2026-09-20T00:00:00Z",
        },
        {
            "number": 11,
            "title": "fresh",
            "body": "active",
            "created_at": "2026-09-24T16:00:00Z",
        },
    ]

    result = summarize(merged, open_prs, now=NOW)
    assert result["merged_prs"] == 2
    assert result["followup_or_replacement_prs"] == 1
    assert result["first_pass_proxy"] == 0.5
    assert result["explicit_superseded_open_prs"] == [10]
    assert result["stale_open_prs"] == [10]
    assert result["can_execute"] is False
