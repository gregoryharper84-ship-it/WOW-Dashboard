from datetime import datetime, timedelta, timezone

from v17 import engineering_probe_cleanup as cleanup

NOW = datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc)


def pr(
    number=1,
    title="probe: live acceptance",
    state="open",
    labels=None,
    created_hours_ago=30,
    branch="probe/acceptance-1",
):
    created_at = (NOW - timedelta(hours=created_hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "number": number,
        "title": title,
        "state": state,
        "labels": [{"name": l} for l in (labels or [])],
        "created_at": created_at,
        "head": {"ref": branch},
    }


def test_old_probe_titled_pr_is_selected():
    result = cleanup.select_probe_prs_to_close([pr()], now=NOW)
    assert result == [{"number": 1, "branch": "probe/acceptance-1", "age_hours": 30.0}]


def test_young_probe_pr_is_not_selected():
    result = cleanup.select_probe_prs_to_close([pr(created_hours_ago=2)], now=NOW)
    assert result == []


def test_exactly_at_threshold_is_selected():
    result = cleanup.select_probe_prs_to_close([pr(created_hours_ago=24)], now=NOW)
    assert len(result) == 1


def test_just_under_threshold_is_not_selected():
    result = cleanup.select_probe_prs_to_close([pr(created_hours_ago=23.9)], now=NOW)
    assert result == []


def test_probe_labelled_but_not_titled_is_selected():
    result = cleanup.select_probe_prs_to_close(
        [pr(title="Live acceptance run", labels=["probe:live-acceptance"])], now=NOW
    )
    assert len(result) == 1


def test_non_probe_old_pr_is_not_selected():
    result = cleanup.select_probe_prs_to_close(
        [pr(title="Real fix", labels=[], created_hours_ago=48)], now=NOW
    )
    assert result == []


def test_closed_probe_pr_is_not_selected():
    result = cleanup.select_probe_prs_to_close([pr(state="closed")], now=NOW)
    assert result == []


def test_custom_max_age_hours():
    result = cleanup.select_probe_prs_to_close([pr(created_hours_ago=10)], now=NOW, max_age_hours=5)
    assert len(result) == 1


def test_multiple_prs_mixed_eligibility():
    rows = [
        pr(number=1, created_hours_ago=48),
        pr(number=2, created_hours_ago=1),
        pr(number=3, title="Real work", labels=[], created_hours_ago=48),
    ]
    result = cleanup.select_probe_prs_to_close(rows, now=NOW)
    assert [r["number"] for r in result] == [1]


def test_cli_plan_outputs_json(tmp_path, capsys):
    import json

    prs = [pr()]
    path = tmp_path / "prs.json"
    path.write_text(json.dumps(prs))
    rc = cleanup.main(["--prs-json", str(path), "--now", "2026-10-12T12:00:00Z"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out == [{"number": 1, "branch": "probe/acceptance-1", "age_hours": 30.0}]
