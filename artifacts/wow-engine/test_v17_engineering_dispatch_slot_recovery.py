"""Dispatcher slot recovery: wake-only, never displaces or overrides the dispatcher, fails closed."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from v17 import engineering_dispatch_slot_recovery as recovery

ROOT = Path(__file__).resolve().parents[2]
# Native slot is 04:17 (dispatcher cron "17 * * * *"); NOW is 28 minutes past it.
SLOT = datetime(2026, 11, 20, 4, 17, tzinfo=timezone.utc)
NOW = SLOT + timedelta(minutes=28)
REPO = recovery.REPOSITORY
WF = recovery.DISPATCHER_WORKFLOW
WORKFLOW_PATH = f"/repos/{REPO}/actions/workflows/{WF}"
RUNS_PATH = f"{WORKFLOW_PATH}/runs?branch=main&per_page=100"
DISPATCH_PATH = f"{WORKFLOW_PATH}/dispatches"


def _run(minutes_ago, lease="GLOBAL", status="completed", event="schedule", branch="main", now=NOW):
    created = (now - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "event": event,
        "status": status,
        "head_branch": branch,
        "created_at": created,
        "display_title": (
            f"WOW V17 provider source=manual lease={lease} incident=AUTO resident=NONE"
        ),
    }


class FakeGitHub:
    def __init__(self, state="active", runs=(), dispatch_status=204, fail_on=None):
        self.state = state
        self.runs = list(runs)
        self.dispatch_status = dispatch_status
        self.fail_on = fail_on
        self.calls = []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if self.fail_on == path:
            raise OSError("network down")
        if method == "GET" and path == WORKFLOW_PATH:
            return 200, {"state": self.state}
        if method == "GET" and path == RUNS_PATH:
            return 200, {"workflow_runs": self.runs}
        if method == "POST" and path == DISPATCH_PATH:
            return self.dispatch_status, None
        raise AssertionError(f"unexpected GitHub call {method} {path}")

    @property
    def dispatches(self):
        return [c for c in self.calls if c[0] == "POST"]


def test_missed_slot_dispatches_default_lane_on_main_with_auto_inputs():
    gh = FakeGitHub(runs=[_run(70)])
    result = recovery.run_once("tok", now=NOW, http=gh)
    assert result["status"] == "DISPATCHED"
    assert gh.dispatches == [("POST", DISPATCH_PATH, {
        "ref": "main",
        "inputs": {
            "force_provider": "auto",
            "lease_group": "GLOBAL",
            "reason": "SCHEDULE_SLOT_RECOVERY",
        },
    })]
    assert result["can_execute"] is False


def test_default_lane_run_since_the_slot_means_it_was_served():
    gh = FakeGitHub(runs=[_run(20, event="workflow_dispatch"), _run(90)])
    result = recovery.run_once("tok", now=NOW, http=gh)
    assert result["status"] == "SLOT_NOT_DUE"
    assert gh.dispatches == []


def test_run_just_before_the_slot_does_not_serve_it():
    # Completed 03:17-slot run, native 04:17 slot not seen by 04:45 -> recover 04:17.
    gh = FakeGitHub(runs=[_run(88), _run(35, event="workflow_run")])
    assert recovery.run_once("tok", now=NOW, http=gh)["status"] == "DISPATCHED"


@pytest.mark.parametrize("minutes_past_slot,expected", [
    (0, "SLOT_NOT_DUE"), (13, "SLOT_NOT_DUE"), (24, "SLOT_NOT_DUE"),   # native grace
    (25, "DISPATCHED"), (45, "DISPATCHED"),                            # recovery window
    (46, "SLOT_NOT_DUE"), (58, "SLOT_NOT_DUE"),                        # too close to next native slot
])
def test_recovery_only_inside_the_overdue_window_of_the_native_slot(minutes_past_slot, expected):
    now = SLOT + timedelta(minutes=minutes_past_slot)
    gh = FakeGitHub(runs=[_run(30, now=SLOT)])  # last run 03:47, before this slot
    result = recovery.run_once("tok", now=now, http=gh)
    assert result["status"] == expected
    assert len(gh.dispatches) == (1 if expected == "DISPATCHED" else 0)
    assert result["slot"] == SLOT.isoformat()


def test_runs_off_main_are_neither_slot_nor_busy_evidence():
    # provider-control only runs on refs/heads/main, so feature-branch runs never served a slot.
    gh = FakeGitHub(runs=[
        _run(5, branch="diagnostic/feature", status="in_progress"),
        _run(10, branch="diagnostic/feature"),
        _run(90),
    ])
    result = recovery.run_once("tok", now=NOW, http=gh)
    assert result["status"] == "DISPATCHED"
    assert ("GET", RUNS_PATH, None) in gh.calls


def test_other_lane_runs_do_not_count_as_the_hourly_slot():
    gh = FakeGitHub(runs=[_run(5, lease="PR_2711"), _run(10, lease="P0_NBA_RESTORE"), _run(80)])
    result = recovery.run_once("tok", now=NOW, http=gh)
    assert result["status"] == "DISPATCHED"
    assert len(gh.dispatches) == 1


def test_lease_embedded_in_a_source_title_is_not_the_runs_own_lane():
    run = _run(10, lease="PR_2711")
    run["display_title"] = ("WOW V17 provider source=worker lease=GLOBAL incident=9 "
                            "lease=PR_2711 incident=AUTO resident=NONE")
    gh = FakeGitHub(runs=[run, _run(80)])
    assert recovery.run_once("tok", now=NOW, http=gh)["status"] == "DISPATCHED"


def test_no_default_lane_history_is_due():
    gh = FakeGitHub(runs=[])
    assert recovery.run_once("tok", now=NOW, http=gh)["status"] == "DISPATCHED"


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "pending", "requested"])
def test_queued_or_running_default_lane_run_is_never_displaced(status):
    # A new dispatch in the same concurrency group would cancel a pending run.
    gh = FakeGitHub(runs=[_run(90, status=status)])
    result = recovery.run_once("tok", now=NOW, http=gh)
    assert result["status"] == "DISPATCHER_BUSY"
    assert gh.dispatches == []


@pytest.mark.parametrize("state", ["disabled_manually", "disabled_inactivity", "deleted", None])
def test_inactive_dispatcher_stays_stopped(state):
    gh = FakeGitHub(state=state, runs=[_run(500)])
    result = recovery.run_once("tok", now=NOW, http=gh)
    assert result["status"] == "DISPATCHER_NOT_ACTIVE"
    assert gh.dispatches == []
    assert all(method == "GET" for method, _, _ in gh.calls)


@pytest.mark.parametrize("fail_on", [WORKFLOW_PATH, RUNS_PATH])
def test_unreadable_github_evidence_fails_closed_without_dispatch(fail_on):
    gh = FakeGitHub(runs=[_run(500)], fail_on=fail_on)
    result = recovery.run_once("tok", now=NOW, http=gh)
    assert result["status"] == "GITHUB_UNAVAILABLE"
    assert gh.dispatches == []


def test_non_200_or_malformed_reads_fail_closed():
    def http(method, path, body=None):
        if path == WORKFLOW_PATH:
            return 200, {"state": "active"}
        return 200, {"workflow_runs": [{"status": "completed", "display_title": "lease=GLOBAL",
                                        "head_branch": "main", "created_at": "not-a-time"}]}
    calls = []
    result = recovery.run_once("tok", now=NOW, http=lambda *a: calls.append(a) or http(*a))
    assert result["status"] == "GITHUB_EVIDENCE_INVALID"
    assert not [c for c in calls if c[0] == "POST"]

    def http_no_branch(method, path, body=None):
        if path == WORKFLOW_PATH:
            return 200, {"state": "active"}
        if method == "POST":
            raise AssertionError("must not dispatch on unattributable evidence")
        return 200, {"workflow_runs": [{"status": "completed", "display_title": "lease=GLOBAL",
                                        "created_at": "2026-11-20T03:00:00Z"}]}
    assert recovery.run_once("tok", now=NOW, http=http_no_branch)["status"] == "GITHUB_EVIDENCE_INVALID"

    def http_403(method, path, body=None):
        return 403, {"message": "forbidden"}
    assert recovery.run_once("tok", now=NOW, http=http_403)["status"] == "GITHUB_UNAVAILABLE"


def test_rejected_dispatch_is_reported_not_hidden():
    gh = FakeGitHub(runs=[_run(70)], dispatch_status=422)
    result = recovery.run_once("tok", now=NOW, http=gh)
    assert result["status"] == "DISPATCH_REJECTED"
    assert result["http_status"] == 422


def test_only_read_and_dispatch_endpoints_are_ever_called():
    seen = set()
    for runs in ([_run(70)], [_run(10)], [_run(90, status="queued")]):
        gh = FakeGitHub(runs=runs)
        recovery.run_once("tok", now=NOW, http=gh)
        seen |= {(m, p) for m, p, _ in gh.calls}
    assert seen <= {("GET", WORKFLOW_PATH), ("GET", RUNS_PATH), ("POST", DISPATCH_PATH)}


def test_main_fails_closed_without_token(monkeypatch, capsys):
    monkeypatch.delenv(recovery.TOKEN_ENV, raising=False)
    monkeypatch.setenv("WOW_CAN_EXECUTE", "false")
    called = []
    assert recovery.main(http=lambda *a: called.append(a)) == 2
    assert called == []
    assert json.loads(capsys.readouterr().out)["status"] == "CONFIGURATION_MISSING"


def test_main_refuses_any_execution_authority(monkeypatch, capsys):
    monkeypatch.setenv(recovery.TOKEN_ENV, "secret-token-value")
    monkeypatch.setenv("WOW_CAN_EXECUTE", "true")
    called = []
    assert recovery.main(http=lambda *a: called.append(a)) == 2
    assert called == []
    assert json.loads(capsys.readouterr().out)["status"] == "EXECUTION_AUTHORITY_REFUSED"


def test_main_exit_codes_and_token_never_printed(monkeypatch, capsys):
    # Dispatcher contract only; lane replay has its own tests (test_v17_schedule_lane_recovery.py).
    from v17 import schedule_lane_recovery
    monkeypatch.setattr(schedule_lane_recovery, "recover", lambda token, now, http: [])
    monkeypatch.setenv(recovery.TOKEN_ENV, "secret-token-value")
    monkeypatch.setenv("WOW_CAN_EXECUTE", "false")
    assert recovery.main(http=FakeGitHub(runs=[_run(70)]), now=NOW) == 0
    assert recovery.main(http=FakeGitHub(runs=[_run(10)]), now=NOW) == 0
    assert recovery.main(http=FakeGitHub(runs=[_run(70)], fail_on=RUNS_PATH), now=NOW) == 1
    assert recovery.main(http=FakeGitHub(runs=[_run(70)], dispatch_status=422), now=NOW) == 1
    out = capsys.readouterr().out
    assert "secret-token-value" not in out
    assert all(json.loads(line)["can_execute"] is False for line in out.splitlines())


def test_recovery_inputs_still_match_the_dispatcher_contract():
    dispatcher = yaml.safe_load((ROOT / ".github/workflows" / WF).read_text())
    triggers = dispatcher[True]
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert set(recovery.DISPATCH_INPUTS) <= set(inputs)
    assert "auto" in inputs["force_provider"]["options"]
    # Recovery replays the dispatcher's own hourly slot; it must not run faster than it.
    assert [c["cron"] for c in triggers["schedule"]] == [f"{recovery.SLOT_MINUTE} * * * *"]
    assert timedelta(0) < recovery.SLOT_GRACE < recovery.SLOT_RECOVERY_DEADLINE < timedelta(hours=1)
    assert dispatcher["jobs"]["provider-control"]["if"].count("refs/heads/main") >= 1
    assert "lease=${{ inputs.lease_group || 'GLOBAL' }}" in " ".join(dispatcher["run-name"].split())


def test_render_cron_is_wake_only_and_not_autodeployed():
    manifest = yaml.safe_load((ROOT / "render.yaml").read_text())
    svc = next(s for s in manifest["services"]
               if s.get("name") == "wow-v17-dispatcher-slot-recovery")
    env = {e["key"]: e for e in svc["envVars"]}
    assert svc["type"] == "cron" and svc["schedule"] == "*/15 * * * *"
    # At least two Render ticks per hour land inside the recovery window (one retry).
    ticks = [SLOT.replace(minute=0) + timedelta(minutes=m) for m in range(0, 120, 15)]
    in_window = {t for t in ticks
                 if recovery.SLOT_GRACE <= t - recovery.current_slot(t) <= recovery.SLOT_RECOVERY_DEADLINE}
    assert len(in_window) >= 4  # 2 per hour over two hours
    assert svc["startCommand"] == "python -m v17.engineering_dispatch_slot_recovery"
    assert svc["autoDeployTrigger"] == "off"
    assert env[recovery.TOKEN_ENV]["sync"] is False and "value" not in env[recovery.TOKEN_ENV]
    assert env["WOW_CAN_EXECUTE"]["value"] == "false" and env["WOW_DRY_RUN_ONLY"]["value"] == "true"
    assert not any("SUPABASE" in key or "API_KEY" in key for key in env)
