"""Regression coverage for P1 governance inventory overflow (#2027)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "v17"))
import governance_dispatcher_github as transport


class FakeGitHub:
    def __init__(self, *, history=0, active=None, invalid=False):
        self.history = history
        self.active = active or []
        self.invalid = invalid
        self.paths = []

    def get(self, path):
        self.paths.append(path)
        if path.endswith("/pulls/41"):
            return {"number": 41, "created_at": (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")}
        q = parse_qs(urlsplit(path).query)
        if "status" in q:
            status = q["status"][0]
            rows = [r for r in self.active if r["status"] == status]
        elif "created" in q:
            start, end = q["created"][0].split("..")
            # >500 legacy runs on the first day. The partition must recover
            # all history without losing matching exact-head runs.
            first_day = (datetime.now(timezone.utc) - timedelta(days=3)).date().isoformat()
            rows = [{"id": n, "name": "unrelated", "status": "completed"} for n in range(self.history) if first_day >= start and first_day <= end]
        else:
            raise AssertionError("unscoped historical inventory read")
        page = int(q.get("page", ["1"])[0])
        rows = rows[(page-1)*100:page*100]
        if self.invalid and rows:
            rows[0] = dict(rows[0], status="completed")
        return {"workflow_runs": rows}


def test_wip_ignores_thousands_of_completed_runs():
    client = FakeGitHub(history=5000, active=[{"id": 1, "status": "queued"}, {"id": 2, "status": "in_progress"}])
    result = transport.count_wip(client, {"qa": {"id": 123}})
    assert result["qa"] == 2
    assert all("status=" in p for p in client.paths)


def test_wip_fail_closed_on_mismatched_active_status():
    client = FakeGitHub(active=[{"id": 1, "status": "queued"}], invalid=True)
    with pytest.raises(transport.DispatchError, match="GITHUB_ACTIVE_RUN_INVENTORY_INVALID"):
        transport.count_wip(client, {"qa": {"id": 123}})


def test_role_history_over_500_is_partitioned_and_complete():
    client = FakeGitHub(history=601)
    # A single day with >500 records must fail closed rather than drop evidence.
    with pytest.raises(transport.DispatchError, match="GITHUB_INVENTORY_TRUNCATED"):
        transport.list_role_runs(client, 123, "qa", 41, "a" * 40)
    assert any("created=" in p for p in client.paths)


def test_role_history_empty_across_recent_windows():
    client = FakeGitHub(history=0)
    assert transport.list_role_runs(client, 123, "qa", 41, "a" * 40) == []


def test_role_history_over_500_spread_over_days_keeps_complete_window():
    class SpreadClient(FakeGitHub):
        def get(self, path):
            if path.endswith("/pulls/41"):
                return super().get(path)
            self.paths.append(path)
            q = parse_qs(urlsplit(path).query)
            start, end = q["created"][0].split("..")
            base = (datetime.now(timezone.utc) - timedelta(days=3)).date()
            rows = []
            for day, count in ((base, 320), (base + timedelta(days=1), 310)):
                if start <= day.isoformat() <= end:
                    rows.extend(
                        {"id": int(day.strftime("%d")) * 10000 + i,
                         "name": "unrelated", "status": "completed"}
                        for i in range(count)
                    )
            page = int(q.get("page", ["1"])[0])
            return {"workflow_runs": rows[(page-1)*100:page*100]}

    client = SpreadClient()
    assert transport.list_role_runs(client, 123, "qa", 41, "a" * 40) == []
    assert any("created=" in path for path in client.paths)
    assert len(client.paths) > 8
