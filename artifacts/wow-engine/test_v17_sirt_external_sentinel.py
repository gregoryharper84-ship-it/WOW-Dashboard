"""Off-GitHub SIRT sentinel: liveness is separate from verdict and fails closed."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from v17 import sirt_external_sentinel as sentinel
from v17.engineering_auditor_github import (
    EXTERNAL_SENTINEL_ID,
    EXTERNAL_SENTINEL_MAX_AGE,
    reconcile_sirt_watchdog,
)
from v17.engineering_auditor_worker import IndependentPollHealth

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 22, 20, 0, tzinfo=timezone.utc)


def _auditor_runtime(now=NOW):
    return {"auditor_id": "WOW_ENGINEERING_AUDITOR", "status": "RUNNING",
            "last_heartbeat_at": now.isoformat(), "can_execute": False,
            "terminal_authority": "V17_TERMINAL_REDUCER"}


def test_heartbeat_written_after_healthy_assessment_without_github():
    written = []
    result = sentinel.run_once(
        "https://x.supabase.co", "k", now=NOW,
        reader_runtime=lambda *a, **k: _auditor_runtime(),
        reader_rows=lambda *a, **k: [],
        writer=lambda url, key, payload: written.append(payload),
    )
    assert result["status"] == "OBSERVED_HEALTHY"
    row = written[0]
    assert row["auditor_id"] == EXTERNAL_SENTINEL_ID and row["status"] == "RUNNING"
    assert row["can_execute"] is False and row["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert row["last_error_code"] == "OBSERVED_HEALTHY"


def test_blocked_verdict_is_recorded_not_hidden_and_still_proves_liveness():
    written = []
    stale = _auditor_runtime(NOW - timedelta(hours=2))
    result = sentinel.run_once(
        "https://x.supabase.co", "k", now=NOW,
        reader_runtime=lambda *a, **k: stale,
        reader_rows=lambda *a, **k: [],
        writer=lambda url, key, payload: written.append(payload),
    )
    assert result["status"] == "BLOCKED"
    assert written[0]["last_error_code"] == "BLOCKED"
    assert written[0]["status"] == "RUNNING"


def test_unreadable_evidence_never_advances_liveness():
    written = []

    def boom(*a, **k):
        raise TimeoutError("db down")

    with pytest.raises(TimeoutError):
        sentinel.run_once("https://x.supabase.co", "k", now=NOW,
                          reader_runtime=boom, reader_rows=boom,
                          writer=lambda *a: written.append(a))
    assert written == []


def test_main_returns_2_and_leaks_nothing_without_configuration(monkeypatch, capsys):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    assert sentinel.main() == 2
    out = capsys.readouterr().out
    assert "SIRT_EXTERNAL_SENTINEL_NOT_RECORDED" in out and "https" not in out


def test_writer_rejects_foreign_identity():
    with pytest.raises(ValueError, match="IDENTITY_INVALID"):
        sentinel.write_heartbeat("https://x.supabase.co", "k", {"auditor_id": "WOW_ENGINEERING_AUDITOR"})


class _Store:
    def __init__(self, row=None, raises=False):
        self.row, self.raises = row, raises
        self.opened, self.resolved = [], []

    def read_runtime_row(self, auditor_id):
        if self.raises:
            raise RuntimeError("db")
        assert auditor_id == EXTERNAL_SENTINEL_ID
        return self.row

    def open_finding(self, *a, **k):
        self.opened.append((a, k))

    def resolve_finding(self, *a, **k):
        self.resolved.append((a, k))


class _Session:
    """Historical GitHub runs are irrelevant to the Render heartbeat verdict."""

    def __init__(self, runs):
        self.runs = runs

    def get(self, url, params=None, timeout=None, **kw):
        runs = self.runs

        class R:
            status_code = 200

            def json(self_inner):
                return {"workflow_runs": runs}

        return R()


def _stale_run():
    return {"id": 1, "head_branch": "main", "event": "schedule", "status": "completed",
            "conclusion": "failure", "created_at": (NOW - timedelta(hours=5)).isoformat()}


def _beat(age, **over):
    row = {"auditor_id": EXTERNAL_SENTINEL_ID, "status": "RUNNING",
           "last_heartbeat_at": (NOW - age).isoformat(), "can_execute": False,
           "terminal_authority": "V17_TERMINAL_REDUCER"}
    row.update(over)
    return row


def test_fresh_external_heartbeat_clears_github_throttle_false_alarm():
    store = _Store(_beat(timedelta(minutes=10)))
    assert reconcile_sirt_watchdog(store, session=_Session([_stale_run()]), now=NOW) == "WATCHDOG_EXTERNAL_OBSERVED"
    assert not store.opened and store.resolved


@pytest.mark.parametrize("row", [
    None,
    _beat(EXTERNAL_SENTINEL_MAX_AGE + timedelta(minutes=1)),
    _beat(timedelta(minutes=1), status="DEGRADED"),
    _beat(timedelta(minutes=1), can_execute=True),
    _beat(timedelta(minutes=1), terminal_authority="OTHER"),
    _beat(timedelta(minutes=1), auditor_id="WOW_ENGINEERING_AUDITOR"),
    _beat(-timedelta(hours=1)),
    _beat(timedelta(minutes=1), last_heartbeat_at="garbage"),
])
def test_stale_or_malformed_external_heartbeat_keeps_missing_verdict(row):
    store = _Store(row)
    assert reconcile_sirt_watchdog(store, session=_Session([_stale_run()]), now=NOW) == "WATCHDOG_HEARTBEAT_MISSING"
    assert store.opened


def test_external_read_error_fails_closed():
    store = _Store(raises=True)
    assert reconcile_sirt_watchdog(store, session=_Session([_stale_run()]), now=NOW) == "WATCHDOG_HEARTBEAT_MISSING"


def test_worker_treats_external_observation_as_healthy_only_when_github_ok():
    state = IndependentPollHealth()
    state.report_github(None)
    state.report_watchdog("WATCHDOG_EXTERNAL_OBSERVED")
    assert state.runtime_state() == ("RUNNING", "CLEAR")
    state.report_github("GITHUB_AUDIT_HTTP_403")
    assert state.runtime_state() == ("DEGRADED", "GITHUB_AUDIT_HTTP_403")


def test_render_cron_is_not_autodeployed_and_has_no_execution_authority():
    manifest = yaml.safe_load((ROOT / "render.yaml").read_text())
    svc = next(s for s in manifest["services"] if s.get("name") == "wow-sirt-external-sentinel")
    env = {e["key"]: e for e in svc["envVars"]}
    assert svc["type"] == "cron" and svc["schedule"] == "*/15 * * * *"
    assert svc["autoDeployTrigger"] == "off"
    assert env["WOW_CAN_EXECUTE"]["value"] == "false" and env["WOW_DRY_RUN_ONLY"]["value"] == "true"
    assert env["SUPABASE_SERVICE_KEY"]["sync"] is False
    assert "GITHUB" not in " ".join(env)

def test_github_sirt_workflow_is_manual_only_and_keeps_diagnostic_receipts():
    source = (ROOT / ".github/workflows/wow-sirt-independent-reliability-sentinel.yml").read_text()
    assert "workflow_dispatch:" in source
    assert "  schedule:" not in source
    assert "  push:" not in source
    assert "  workflow_run:" not in source
    assert "sirt-heartbeat.json" in source
    assert "sirt-failure-families.json" in source


def test_external_heartbeat_does_not_call_github_even_if_it_would_fail():
    class NoGitHub:
        def get(self, *args, **kwargs):
            pytest.fail("GitHub must not be queried for Render scheduler liveness")

    fresh = _Store(_beat(timedelta(minutes=1), last_error_code="BLOCKED"))
    assert reconcile_sirt_watchdog(fresh, session=NoGitHub(), now=NOW) == "WATCHDOG_EXTERNAL_OBSERVED"
    assert fresh.resolved and not fresh.opened
    missing = _Store(None)
    assert reconcile_sirt_watchdog(missing, session=NoGitHub(), now=NOW) == "WATCHDOG_HEARTBEAT_MISSING"
    assert missing.opened and missing.opened[0][1]["evidence"]["source_system"] == "RENDER_EXTERNAL_SENTINEL"
