"""Off-GitHub replay of missed hourly product-lane slots (#2744 cron)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from v17 import engineering_dispatch_slot_recovery as dispatcher
from v17 import schedule_lane_recovery as lanes

ROOT = Path(__file__).resolve().parents[3]
PROP = "wow-v17-production-source-heavy-prop-evidence"
LIFECYCLE, MLB, NFL = ("wow-v17-priority-prop-lifecycle.yml", "wow-v17-mlb-prop-forward-evidence.yml",
                       "wow-v17-nfl-prop-forward-evidence.yml")
TEAM, CLOSURE = "wow-v17-team-event-recoverable-refresh.yml", "wow-v17-terminal-closure-controller.yml"
NBA_DAILY = "wow-v17-basketball-model-maintenance.yml"
NOW = datetime(2026, 11, 20, 4, 45, tzinfo=timezone.utc)  # :07 and :17 slots due; :23 waiting; :37 too close


def _run(at, status="completed"):
    return {"created_at": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": status}


class FakeLanes:
    def __init__(self, runs=None, states=None, fail=None, dispatch_status=204):
        self.runs = runs or {}
        self.states = states or {}
        self.fail = fail or set()
        self.dispatch_status = dispatch_status
        self.calls = []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        wf = path.split("/actions/workflows/")[1].split("/")[0]
        if wf in self.fail:
            raise OSError("down")
        if method == "GET" and path.endswith(wf):
            return 200, {"state": self.states.get(wf, "active")}
        if method == "GET" and "/runs?branch=main&per_page=30" in path:
            return 200, {"workflow_runs": self.runs.get(wf, [])}
        if method == "POST" and path.endswith("/dispatches"):
            return self.dispatch_status, None
        raise AssertionError((method, path))

    def dispatched(self):
        return [c[1].split("/actions/workflows/")[1].split("/")[0] for c in self.calls if c[0] == "POST"]


def _by_wf(results):
    return {r["workflow"]: r for r in results}


def test_missed_slots_dispatch_due_lanes_only():
    gh = FakeLanes(runs={w: [_run(NOW - timedelta(hours=3))] for w, _, _ in lanes.LANE_TARGETS})
    out = _by_wf(lanes.recover("t", now=NOW, http=gh))
    assert sorted(gh.dispatched()) == sorted([LIFECYCLE, TEAM, CLOSURE])
    assert out[MLB]["reason"] == "WAITING_FOR_NATIVE_SCHEDULE"  # 04:23 slot, 22 min old
    assert out[NFL]["reason"] == "WAITING_FOR_NATIVE_SCHEDULE"  # 04:37 slot, 8 min old
    assert all(r["can_execute"] is False for r in out.values())
    # No inputs are ever passed; only main.
    assert all(c[2] == {"ref": "main"} for c in gh.calls if c[0] == "POST")


def test_one_dispatch_per_shared_concurrency_group_per_tick():
    now = NOW.replace(minute=50)  # :07 (43 min) and :23 (27 min) both due
    gh = FakeLanes()
    out = _by_wf(lanes.recover("t", now=now, http=gh))
    assert [w for w in gh.dispatched() if w in (LIFECYCLE, MLB, NFL)] == [LIFECYCLE]
    assert out[MLB]["status"] == "GROUP_DISPATCHED_THIS_TICK"


@pytest.mark.parametrize("status", sorted(dispatcher.ACTIVE_STATUSES))
def test_busy_group_is_never_displaced(status):
    gh = FakeLanes(runs={NFL: [_run(NOW - timedelta(minutes=5), status=status)]})
    out = _by_wf(lanes.recover("t", now=NOW, http=gh))
    assert LIFECYCLE not in gh.dispatched()
    assert {out[w]["status"] for w in (LIFECYCLE, MLB, NFL)} == {"LANE_GROUP_BUSY"}
    assert TEAM in gh.dispatched()  # other groups unaffected


def test_run_since_slot_serves_it_and_run_before_does_not():
    slot = lanes.lane_slot(NOW, 17)
    gh = FakeLanes(runs={TEAM: [_run(slot + timedelta(minutes=1))], CLOSURE: [_run(slot - timedelta(minutes=1))]})
    out = _by_wf(lanes.recover("t", now=NOW, http=gh))
    assert out[TEAM]["reason"] == "SLOT_ALREADY_SERVED"
    assert out[CLOSURE]["status"] == "DISPATCHED"


@pytest.mark.parametrize("state", ["disabled_manually", "disabled_inactivity"])
def test_inactive_lane_stays_stopped(state):
    gh = FakeLanes(states={TEAM: state})
    out = _by_wf(lanes.recover("t", now=NOW, http=gh))
    assert out[TEAM]["status"] == "LANE_NOT_ACTIVE" and TEAM not in gh.dispatched()


def test_unreadable_member_fails_its_group_closed():
    gh = FakeLanes(fail={MLB})
    out = _by_wf(lanes.recover("t", now=NOW, http=gh))
    assert out[MLB]["status"] == "GITHUB_UNAVAILABLE"
    assert out[LIFECYCLE]["status"] == "LANE_GROUP_EVIDENCE_INCOMPLETE"
    assert LIFECYCLE not in gh.dispatched() and TEAM in gh.dispatched()


def test_malformed_runs_fail_closed():
    gh = FakeLanes(runs={TEAM: [{"status": "completed"}]})
    out = _by_wf(lanes.recover("t", now=NOW, http=gh))
    assert out[TEAM]["status"] == "GITHUB_EVIDENCE_INVALID" and TEAM not in gh.dispatched()


def test_rejected_dispatch_is_reported():
    out = _by_wf(lanes.recover("t", now=NOW, http=FakeLanes(dispatch_status=422)))
    assert out[TEAM]["status"] == "DISPATCH_REJECTED" and out[TEAM]["http_status"] == 422


def test_daily_nba_maintenance_recovers_one_missed_slot_without_duplicate_dispatch():
    now = datetime(2026, 11, 23, 13, 30, tzinfo=timezone.utc)
    native = lanes.daily_slot(now, 13, 5)
    assert native == now.replace(minute=5)
    gh = FakeLanes(runs={NBA_DAILY: [_run(native - timedelta(days=1))]})
    result = _by_wf(lanes.recover("t", now=now, http=gh))[NBA_DAILY]
    assert result["status"] == "DISPATCHED"
    assert result["slot"] == native.isoformat()
    assert result["can_execute"] is False
    assert NBA_DAILY in gh.dispatched()
    assert [body for method, path, body in gh.calls if method == "POST" and NBA_DAILY in path] == [{"ref": "main"}]

    already_ran = FakeLanes(runs={NBA_DAILY: [_run(native + timedelta(minutes=2))]})
    result = _by_wf(lanes.recover("t", now=now, http=already_ran))[NBA_DAILY]
    assert result["status"] == "SLOT_NOT_DUE" and result["reason"] == "SLOT_ALREADY_SERVED"
    assert NBA_DAILY not in already_ran.dispatched()


def test_daily_nba_owner_stop_busy_and_deadline_fail_closed():
    now = datetime(2026, 11, 23, 13, 45, tzinfo=timezone.utc)
    inactive = FakeLanes(states={NBA_DAILY: "disabled_manually"})
    assert _by_wf(lanes.recover("t", now=now, http=inactive))[NBA_DAILY]["status"] == "LANE_NOT_ACTIVE"
    assert NBA_DAILY not in inactive.dispatched()
    busy = FakeLanes(runs={NBA_DAILY: [_run(now - timedelta(minutes=5), status="in_progress")]})
    assert _by_wf(lanes.recover("t", now=now, http=busy))[NBA_DAILY]["status"] == "LANE_GROUP_BUSY"
    assert NBA_DAILY not in busy.dispatched()
    expired = FakeLanes()
    late = now.replace(minute=55)
    assert _by_wf(lanes.recover("t", now=late, http=expired))[NBA_DAILY]["reason"] == "NEXT_NATIVE_SLOT_TOO_CLOSE"
    assert NBA_DAILY not in expired.dispatched()


def test_daily_nba_slot_matches_native_cron_and_existing_tick_windows():
    workflow, hour, minute, group = lanes.DAILY_TARGETS[0]
    assert workflow == NBA_DAILY
    wf = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text())
    assert wf[True]["schedule"] == [{"cron": f"{minute} {hour} * * *"}]
    assert "workflow_dispatch" in wf[True]
    assert wf["concurrency"]["group"].startswith(group + "-")
    for m in (30, 45):
        tick = datetime(2026, 11, 23, hour, m, tzinfo=timezone.utc)
        assert lanes.SLOT_GRACE <= tick - lanes.daily_slot(tick, hour, minute) <= lanes.SLOT_RECOVERY_DEADLINE


def test_targets_match_workflow_cron_group_and_dispatch_contract():
    for workflow, minute, group in lanes.LANE_TARGETS:
        wf = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text())
        on = wf[True]
        assert [c["cron"] for c in on["schedule"]] == [f"{minute} * * * *"], workflow
        assert "workflow_dispatch" in on, workflow
        inputs = (on["workflow_dispatch"] or {}).get("inputs") or {}
        assert not [k for k, v in inputs.items() if (v or {}).get("required") in (True, "true")], workflow
        assert wf["concurrency"]["group"] == group, workflow
        assert wf["concurrency"]["cancel-in-progress"] is False, workflow


def test_render_cron_ticks_land_in_every_lane_window():
    for _, minute, _ in lanes.LANE_TARGETS:
        start = NOW.replace(minute=0)
        ticks = [start + timedelta(minutes=m) for m in range(0, 120, 15)]
        due = [t for t in ticks if lanes.SLOT_GRACE <= t - lanes.lane_slot(t, minute) <= lanes.SLOT_RECOVERY_DEADLINE]
        assert len(due) >= 2, minute  # at least once per hour over two hours


def test_main_runs_dispatcher_then_lanes_without_printing_token(monkeypatch, capsys):
    monkeypatch.setenv(dispatcher.TOKEN_ENV, "secret-token-value")
    monkeypatch.setenv("WOW_CAN_EXECUTE", "false")
    lane_gh = FakeLanes()

    def http(method, path, body=None):
        if dispatcher.DISPATCHER_WORKFLOW in path:
            if method == "GET" and path.endswith(dispatcher.DISPATCHER_WORKFLOW):
                return 200, {"state": "active"}
            if method == "GET":
                return 200, {"workflow_runs": []}
            return 204, None
        return lane_gh(method, path, body)

    assert dispatcher.main(http=http, now=NOW) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[0]["job"] == "wow-v17-dispatcher-slot-recovery"
    assert {line["workflow"] for line in lines[1:]} == (
        {w for w, _, _ in lanes.LANE_TARGETS} | {w for w, _, _, _ in lanes.DAILY_TARGETS}
    )
    assert all(line["can_execute"] is False for line in lines)
    assert "secret-token-value" not in json.dumps(lines)
    lane_gh.dispatch_status = 500
    assert dispatcher.main(http=http, now=NOW) == 1


def test_slot_past_the_deadline_is_left_for_the_next_native_slot():
    now = NOW.replace(minute=58)  # 04:07 slot is 51 min old
    gh = FakeLanes()
    out = _by_wf(lanes.recover("t", now=now, http=gh))
    assert out[LIFECYCLE]["reason"] == "NEXT_NATIVE_SLOT_TOO_CLOSE" and LIFECYCLE not in gh.dispatched()
