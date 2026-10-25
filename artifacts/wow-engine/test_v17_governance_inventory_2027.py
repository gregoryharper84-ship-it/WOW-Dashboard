"""Regression coverage for P1 governance inventory overflow (#2027)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "v17"))
import governance_dispatcher_github as transport


class FakeGitHub:
    def __init__(self, *, history=0, active=None, invalid=False,
                 spacing_seconds=10):
        self.history = history
        self.active = active or []
        self.invalid = invalid
        self.spacing_seconds = spacing_seconds
        self.paths = []
        self.base = (datetime.now(timezone.utc) - timedelta(days=3)).replace(microsecond=0)

    def get(self, path):
        self.paths.append(path)
        if path.endswith("/pulls/41"):
            return {"number": 41, "created_at": self.base.strftime("%Y-%m-%dT%H:%M:%SZ")}
        q = parse_qs(urlsplit(path).query)
        if "status" in q:
            status = q["status"][0]
            rows = [r for r in self.active if r["status"] == status]
        elif "created" in q:
            start_text, end_text = q["created"][0].split("..")
            start = datetime.fromisoformat(start_text.replace("Z", "+00:00"))
            end = datetime.fromisoformat(end_text.replace("Z", "+00:00"))
            rows = [
                {"id": n + 1, "name": "unrelated", "status": "completed"}
                for n in range(self.history)
                if start <= self.base + timedelta(seconds=n * self.spacing_seconds) <= end
            ]
        else:
            raise AssertionError("unscoped historical inventory read")
        page = int(q.get("page", ["1"])[0])
        rows = rows[(page - 1) * 100:page * 100]
        if self.invalid and rows:
            rows[0] = dict(rows[0], status="completed")
        return {"workflow_runs": rows}


def test_wip_ignores_thousands_of_completed_runs():
    client = FakeGitHub(history=5000, active=[
        {"id": 1, "status": "queued"},
        {"id": 2, "status": "in_progress"},
    ])
    result = transport.count_wip(client, {"qa": {"id": 123}})
    assert result["qa"] == 2
    assert all("status=" in p for p in client.paths)


def test_wip_fail_closed_on_mismatched_active_status():
    client = FakeGitHub(active=[{"id": 1, "status": "queued"}], invalid=True)
    with pytest.raises(transport.DispatchError, match="GITHUB_ACTIVE_RUN_INVENTORY_INVALID"):
        transport.count_wip(client, {"qa": {"id": 123}})


def test_role_history_more_than_500_same_day_is_partitioned():
    client = FakeGitHub(history=601, spacing_seconds=10)
    assert transport.list_role_runs(client, 123, "qa", 41, "a" * 40) == []
    assert len(client.paths) > 8
    assert any("created=" in p and "T" in p for p in client.paths)


def test_role_history_over_500_single_second_fails_closed():
    client = FakeGitHub(history=601, spacing_seconds=0)
    with pytest.raises(transport.DispatchError, match="GITHUB_INVENTORY_TRUNCATED"):
        transport.list_role_runs(client, 123, "qa", 41, "a" * 40)


def test_role_history_empty_across_recent_windows():
    client = FakeGitHub(history=0)
    assert transport.list_role_runs(client, 123, "qa", 41, "a" * 40) == []


def test_role_history_over_500_spread_over_days_keeps_complete_window():
    client = FakeGitHub(history=630, spacing_seconds=300)
    assert transport.list_role_runs(client, 123, "qa", 41, "a" * 40) == []
    assert len(client.paths) > 8


@pytest.mark.parametrize("created", ["not-a-time", "2026-10-01T11:00:00"])
def test_role_history_rejects_bad_pr_opened_timestamp(created):
    class BadPR(FakeGitHub):
        def get(self, path):
            if path.endswith("/pulls/41"):
                return {"number": 41, "created_at": created}
            return super().get(path)
    with pytest.raises(transport.DispatchError, match="ROLE_HISTORY_PR_DATE_INVALID"):
        transport.list_role_runs(BadPR(), 123, "qa", 41, "a" * 40)
