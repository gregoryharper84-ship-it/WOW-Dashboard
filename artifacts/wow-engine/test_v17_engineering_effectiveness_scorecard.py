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


def test_pr_creation_to_merge_percentiles_do_not_imply_production_deploy() -> None:
    rows = [
        {"number": 20, "title": "a", "body": "", "created_at": "2026-09-24T12:00:00Z", "merged_at": "2026-09-24T12:11:00Z"},
        {"number": 21, "title": "b", "body": "", "created_at": "2026-09-24T12:00:00Z", "merged_at": "2026-09-24T12:16:00Z"},
        {"number": 22, "title": "c", "body": "", "created_at": "2026-09-24T12:00:00Z", "merged_at": "2026-09-24T13:25:00Z"},
        {"number": 23, "title": "missing", "body": "", "merged_at": "2026-09-24T13:00:00Z"},
        {"number": 24, "title": "invalid order", "body": "", "created_at": "2026-09-24T13:05:00Z", "merged_at": "2026-09-24T13:00:00Z"},
    ]
    result = summarize(rows, [], now=NOW)
    assert result["pr_creation_to_merge"] == {
        "sample_count": 3,
        "p50_minutes": 16.0,
        "p95_minutes": 85.0,
        "under_30_minutes": 2,
        "under_60_minutes": 2,
    }
    assert result["merge_to_verified_production"]["status"] == "NOT_MEASURED"
    assert result["merge_to_verified_production"]["p50_minutes"] is None
    assert result["can_execute"] is False


def test_missing_merge_timestamps_keep_latency_unknown() -> None:
    result = summarize(
        [{"number": 25, "title": "no start", "merged_at": "2026-09-24T12:00:00Z"}],
        [],
        now=NOW,
    )
    assert result["pr_creation_to_merge"]["sample_count"] == 0
    assert result["pr_creation_to_merge"]["p50_minutes"] is None
    assert result["pr_creation_to_merge"]["p95_minutes"] is None
