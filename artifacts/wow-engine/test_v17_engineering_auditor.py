from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from v17.engineering_auditor import (
    AuditEvent,
    code_health_blocker_packet,
    finding_fingerprint,
    is_code_health_failure,
    is_terminal_source_state,
    next_audit_at,
    severity_from_labels,
    work_fingerprint,
)
from v17.engineering_auditor_github import (
    bootstrap_open_github_work,
    issue_to_event,
    reconcile_github_updates,
    reconcile_sirt_watchdog,
    GitHubAuditUnavailable,
)

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-engineering-auditor-code-health.yml"
WORKER = ROOT / "artifacts/wow-engine/v17/engineering_auditor_worker.py"
GITHUB_RECONCILER = ROOT / "artifacts/wow-engine/v17/engineering_auditor_github.py"
CELERY = ROOT / "artifacts/wow-engine/agent_runtime/celery_app.py"
RENDER = ROOT / "render.yaml"
MIGRATION = ROOT / "artifacts/wow-engine/migrations/20260926152331_create_engineering_auditor_control_plane.sql"
SKILL = ROOT / ".agents/skills/wow-engineering-auditor/SKILL.md"


def test_fingerprints_are_deterministic_and_scoped():
    a = work_fingerprint("Owner/Repo", "github_issue", "42")
    b = work_fingerprint("owner/repo", "GITHUB_ISSUE", "42")
    assert a == b
    assert a != finding_fingerprint("STALE_WORK", a)
    assert len(a) == 64


def test_severity_labels_and_deadlines_are_explicit():
    now = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)
    assert severity_from_labels(["priority:P0"]) == "P0"
    assert severity_from_labels(["severity:p1"]) == "P1"
    assert severity_from_labels([], source_kind="GITHUB_PR") == "P2"
    assert severity_from_labels([], source_kind="GITHUB_ISSUE") == "P3"
    assert next_audit_at(now, "P0") == now + timedelta(minutes=30)
    assert next_audit_at(now, "P1") == now + timedelta(hours=2)
    assert next_audit_at(now, "P2") == now + timedelta(hours=8)
    assert next_audit_at(now, "P3") == now + timedelta(hours=24)
    assert next_audit_at(now, "P4") == now + timedelta(hours=72)
    assert next_audit_at(now, "P2", draft=True) == now + timedelta(hours=16)


def test_terminal_and_code_health_semantics_fail_closed():
    assert is_terminal_source_state("closed")
    assert is_terminal_source_state("MERGED")
    assert not is_terminal_source_state("open")
    assert not is_code_health_failure("success")
    assert not is_code_health_failure("neutral")
    assert is_code_health_failure("failure")
    assert is_code_health_failure(None)


def test_audit_event_rejects_non_object_details():
    with pytest.raises(ValueError, match="details must be an object"):
        AuditEvent.from_mapping(
            {
                "event_name": "issues",
                "repository": "owner/repo",
                "source_kind": "GITHUB_ISSUE",
                "source_ref": "1",
                "details": ["bad"],
            }
        )


def test_public_github_issue_reconcile_preserves_updated_at_as_progress_evidence():
    event = issue_to_event(
        {
            "number": 42,
            "title": "Open repair",
            "state": "open",
            "updated_at": "2026-09-26T15:30:00Z",
            "html_url": "https://github.com/example/issues/42",
            "labels": [{"name": "P1"}],
            "user": {"login": "owner"},
        }
    )
    assert event.source_kind == "GITHUB_ISSUE"
    assert event.updated_at == datetime(2026, 9, 26, 15, 30, tzinfo=timezone.utc)
    assert event.details["source_updated_at"] == "2026-09-26T15:30:00Z"


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _Session:
    def __init__(self, issues, runs):
        self.issues = issues
        self.runs = runs

    def get(self, url, **kwargs):
        if url.endswith("/issues"):
            return _Response(self.issues)
        if url.endswith("/actions/runs"):
            return _Response({"workflow_runs": self.runs})
        raise AssertionError(url)


class _Store:
    def __init__(self):
        self.events = []

    def ingest_event(self, event, now=None):
        self.events.append(event)
        return {"ok": True}


def test_public_github_reconciler_bootstraps_work_and_main_code_health():
    issues = [
        {
            "number": 7,
            "title": "Issue",
            "state": "open",
            "updated_at": "2026-09-26T15:30:00Z",
            "labels": [],
        }
    ]
    runs = [
        {
            "id": 99,
            "name": "wow-v17-engineering-auditor-code-health",
            "status": "completed",
            "conclusion": "failure",
            "head_branch": "main",
            "head_sha": "abc",
            "updated_at": "2026-09-26T15:31:00Z",
        },
        {
            "id": 100,
            "name": "wow-v17-engineering-auditor-code-health",
            "status": "completed",
            "conclusion": "success",
            "head_branch": "feature/not-main",
            "head_sha": "def",
        },
    ]
    store = _Store()
    session = _Session(issues, runs)
    assert bootstrap_open_github_work(store, session=session) == 1
    receipt = reconcile_github_updates(
        store,
        session=session,
        since=datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc),
        seen_workflow_runs=set(),
    )
    assert receipt == {"github_work_events": 1, "code_health_events": 1}
    assert any(event.source_kind == "CODE_HEALTH_RUN" and event.conclusion == "failure" for event in store.events)


def test_code_health_workflow_is_event_driven_and_never_scheduled():
    text = WORKFLOW.read_text()
    assert "schedule:" not in text
    assert "cron:" not in text
    assert "push:" in text
    assert "pull_request:" in text
    assert "workflow_dispatch:" in text
    assert "engineering_agent_team.py self-check" in text
    assert "test_v17_engineering_auditor.py" in text
    assert "test_v17_24h_completion_loop.py" in text
    assert "test_v17_lifecycle_closure_cell.py" in text
    assert "V17_TERMINAL_REDUCER" in text
    assert "can_execute: false" in text
    assert yaml.safe_load(text)["name"] == "wow-v17-engineering-auditor-code-health"


def test_resident_worker_has_no_external_scheduler_and_bootstraps_before_loop():
    text = WORKER.read_text()
    assert "schedule:" not in text
    assert "celery beat" in text.lower()
    assert "bootstrap_open_github_work(store)" in text
    assert "store.reconcile_backlog(now=now)" in text
    assert "store.reconcile_due(now=now)" in text
    assert text.index("bootstrap_open_github_work(store)") < text.index("while not stop_event.wait")
    assert "worker_ready" in text
    assert "worker_shutdown" in text


def test_github_observation_is_credential_free_and_bounded():
    text = GITHUB_RECONCILER.read_text()
    assert "api.github.com/repos/" in text
    assert "Authorization" not in text
    assert "GITHUB_TOKEN" not in text
    assert "per_page\": 50" in text
    assert "branch\": \"main\"" in text
    assert "can_execute" not in text or "can_execute" in text  # observation module has no execution path


def test_worker_is_wired_and_explicitly_enabled_without_execution_authority():
    celery = CELERY.read_text()
    render = RENDER.read_text()
    assert "install_celery_worker_hooks()" in celery
    assert "WOW_ENGINEERING_AUDITOR_ENABLED" in render
    assert 'value: "1"' in render
    assert "WOW_ENGINEERING_AUDITOR_GITHUB_INTERVAL_SECONDS" in render
    assert "WOW_CAN_EXECUTE" in render
    assert 'value: "false"' in render
    assert "WOW_DRY_RUN_ONLY" in render


def test_auditor_migration_is_service_role_only_and_non_executable():
    text = MIGRATION.read_text()
    for table in (
        "wow_engineering_audit_work_items",
        "wow_engineering_audit_findings",
        "wow_engineering_auditor_runtime",
    ):
        assert f"alter table public.{table} enable row level security" in text
        assert f"revoke all on table public.{table} from anon, authenticated" in text
        assert f"grant select, insert, update, delete on table public.{table} to service_role" in text
    assert text.count("can_execute = false") >= 3
    assert "V17_TERMINAL_REDUCER" in text
    assert "wow_predictions" not in text
    assert "wow_event_predictions" not in text


def test_auditor_skill_has_valid_frontmatter():
    text = SKILL.read_text()
    assert text.startswith("---\n")
    _, frontmatter, _ = text.split("---", 2)
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == "wow-engineering-auditor"
    assert isinstance(metadata["description"], str) and metadata["description"].strip()


def test_auditor_skill_is_independent_and_read_only():
    text = SKILL.read_text()
    assert "CODE_HEALTH_AUDITOR" in text
    assert "WORK_LIFECYCLE_AUDITOR" in text
    assert "does not become the implementation agent" in text
    assert "MUST NOT:" in text
    assert "edit production code" in text
    assert "set `can_execute=true`" in text
    assert "AUDIT_FINDING -> REPORTER/INTAKE" in text


class _WatchdogSession:
    def __init__(self, runs, code=200):
        self.runs, self.code = runs, code
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        assert url.endswith("/actions/workflows/wow-sirt-independent-reliability-sentinel.yml/runs")
        return _Response({"workflow_runs": self.runs}, status_code=self.code)


class _WatchdogStore:
    def __init__(self, row=None, raises=False):
        self.opened = []
        self.resolved = []
        self.row, self.raises = row, raises

    def read_runtime_row(self, auditor_id):
        assert auditor_id == "WOW_SIRT_EXTERNAL_SENTINEL"
        if self.raises:
            raise RuntimeError("read unavailable")
        return self.row

    def open_finding(self, kind, **kwargs):
        self.opened.append((kind, kwargs))
        return {"status": "OPEN"}

    def resolve_finding(self, kind, component, **kwargs):
        self.resolved.append((kind, component, kwargs))
        return True


def _watchdog_run(at, *, conclusion="failure", branch="main", status="completed", event="schedule"):
    return {
        "id": 12345, "created_at": at.isoformat(),
        "head_branch": branch, "status": status, "conclusion": conclusion,
        "event": event,
    }


def _live_external_pulse(now, **overrides):
    row = {
        "auditor_id": "WOW_SIRT_EXTERNAL_SENTINEL",
        "status": "RUNNING",
        "last_heartbeat_at": now.isoformat(),
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "last_error_code": "BLOCKED",
    }
    row.update(overrides)
    return row


def test_render_side_watchdog_opens_durable_p0_when_external_pulse_missing():
    now = datetime(2026, 10, 8, 18, 0, tzinfo=timezone.utc)
    store, session = _WatchdogStore(), _WatchdogSession([])
    assert reconcile_sirt_watchdog(store, session=session, now=now) == "WATCHDOG_HEARTBEAT_MISSING"
    assert not session.calls
    assert len(store.opened) == 1
    kind, fields = store.opened[0]
    assert kind == "AUDITOR_HEALTH" and fields["severity"] == "P0"
    assert fields["evidence"]["finding_code"] == "WATCHDOG_HEARTBEAT_MISSING"
    assert fields["evidence"]["source_system"] == "RENDER_EXTERNAL_SENTINEL"
    assert fields["evidence"]["can_execute"] is False
    assert not store.resolved


def test_render_side_watchdog_accepts_only_authenticated_fresh_external_pulse():
    now = datetime(2026, 10, 8, 18, 0, tzinfo=timezone.utc)
    for invalid in (
        _live_external_pulse(now, status="DEGRADED"),
        _live_external_pulse(now, terminal_authority="OTHER"),
        _live_external_pulse(now, can_execute=True),
        _live_external_pulse(now, auditor_id="FOREIGN"),
        _live_external_pulse(now, last_heartbeat_at=(now-timedelta(minutes=36)).isoformat()),
        _live_external_pulse(now, last_heartbeat_at=(now+timedelta(hours=1)).isoformat()),
        _live_external_pulse(now, last_heartbeat_at="invalid"),
    ):
        store = _WatchdogStore(invalid)
        assert reconcile_sirt_watchdog(store, now=now) == "WATCHDOG_HEARTBEAT_MISSING"
        assert store.opened and not store.resolved


def test_render_side_watchdog_fresh_blocked_assurance_still_proves_liveness():
    now = datetime(2026, 10, 8, 18, 0, tzinfo=timezone.utc)
    store = _WatchdogStore(_live_external_pulse(now))
    session = _WatchdogSession([], code=403)
    assert reconcile_sirt_watchdog(store, session=session, now=now) == "WATCHDOG_EXTERNAL_OBSERVED"
    assert not session.calls and not store.opened
    assert store.resolved[0][0] == "AUDITOR_HEALTH"


def test_render_side_watchdog_unreadable_runtime_fails_closed_without_github():
    now = datetime(2026, 10, 8, 18, 0, tzinfo=timezone.utc)
    store, session = _WatchdogStore(raises=True), _WatchdogSession([])
    assert reconcile_sirt_watchdog(store, session=session, now=now) == "WATCHDOG_HEARTBEAT_MISSING"
    assert not session.calls and store.opened


def test_render_worker_wires_independent_watchdog_with_typed_degraded_status():
    source = WORKER.read_text()
    assert "reconcile_sirt_watchdog(store)" in source
    assert "reconcile_sirt_watchdog(store, now=now)" in source
    assert "poll_health.runtime_state()" in source
    assert "poll_health.watchdog_unavailable()" in source
    assert "WOW_ENGINEERING_RESIDENT_DISPATCH_ENABLED" not in GITHUB_RECONCILER.read_text()


def test_watchdog_failure_sticks_across_fast_worker_ticks_until_new_observation():
    from v17.engineering_auditor_worker import IndependentPollHealth
    state = IndependentPollHealth()
    assert state.runtime_state() == ("DEGRADED", "SIRT_WATCHDOG_UNVERIFIED")
    state.report_github(None)
    state.report_watchdog("WATCHDOG_HEARTBEAT_MISSING")
    for _ in range(12):  # 12 ordinary 30-second ticks without another GitHub poll
        assert state.runtime_state() == ("DEGRADED", "SIRT_WATCHDOG_HEARTBEAT_MISSING")
    state.report_github("GITHUB_AUDIT_HTTP_403")
    assert state.runtime_state()[0] == "DEGRADED"
    state.report_github(None)
    assert state.runtime_state() == ("DEGRADED", "SIRT_WATCHDOG_HEARTBEAT_MISSING")
    state.report_watchdog("WATCHDOG_RUN_OBSERVED")
    assert state.runtime_state() == ("RUNNING", "CLEAR")


def test_watchdog_source_failure_is_independent_of_issue_reconciliation():
    from v17.engineering_auditor_worker import IndependentPollHealth, _github_interval_seconds
    state = IndependentPollHealth()
    state.report_github("GITHUB_AUDIT_HTTP_403")
    state.report_watchdog("WATCHDOG_RUN_OBSERVED")
    assert state.runtime_state() == ("DEGRADED", "GITHUB_AUDIT_HTTP_403")
    state.watchdog_unavailable()
    assert state.runtime_state() == ("DEGRADED", "SIRT_WATCHDOG_SOURCE_UNAVAILABLE")
    state.report_github(None)
    assert state.runtime_state() == ("DEGRADED", "SIRT_WATCHDOG_SOURCE_UNAVAILABLE")
    state.report_watchdog("WATCHDOG_RUN_OBSERVED")
    assert state.runtime_state() == ("RUNNING", "CLEAR")
    assert _github_interval_seconds() >= 300


def test_watchdog_and_github_polls_have_distinct_failure_boundaries():
    source = WORKER.read_text()
    assert '# Poll the watchdog separately even when ordinary GitHub' in source
    assert 'poll_health.report_watchdog(reconcile_sirt_watchdog(store, now=now))' in source
    assert 'poll_health.report_github(IndependentPollHealth.github_failure(exc))' in source


def test_github_scheduler_runs_cannot_mask_missing_render_pulse():
    now = datetime(2026, 10, 8, 18, 0, tzinfo=timezone.utc)
    store, session = _WatchdogStore(), _WatchdogSession([
        _watchdog_run(now - timedelta(minutes=1), conclusion="success"),
    ])
    assert reconcile_sirt_watchdog(store, session=session, now=now) == "WATCHDOG_HEARTBEAT_MISSING"
    assert not session.calls and store.opened


def test_watchdog_finding_type_respects_real_postgres_schema_constraint():
    """Avoid fake-store false greens when DB rejects a newly invented type."""
    migration = MIGRATION.read_text()
    assert "constraint wow_engineering_audit_findings_type" in migration
    assert "'AUDITOR_HEALTH'" in migration
    assert "'WATCHDOG_HEARTBEAT_MISSING'" not in migration
    store = _WatchdogStore()
    now = datetime(2026, 10, 8, 18, 0, tzinfo=timezone.utc)
    reconcile_sirt_watchdog(store, session=_WatchdogSession([]), now=now)
    assert store.opened[0][0] == "AUDITOR_HEALTH"
    assert store.opened[0][1]["evidence"]["finding_code"] == "WATCHDOG_HEARTBEAT_MISSING"


def test_lifecycle_audit_reads_live_exact_head_metadata_not_stale_event():
    """Metadata edits must not require a new PR code SHA or trust stale event data."""
    text = WORKFLOW.read_text()
    assert "PR_BODY: ${{ github.event.pull_request.body" not in text
    assert 'GH_TOKEN: ${{ github.token }}' in text
    assert 'gh api "repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}"' in text
    assert '.base.repo.full_name == $repo and .head.sha == $sha' in text
    assert 'LIFECYCLE_PR_STALE_OR_WRONG_TARGET' in text
    assert 'LIFECYCLE_PR_READ_FAILED' in text
    assert 'LIFECYCLE_PR_IDENTITY_INVALID' in text
    assert 'jq -r \'.body // ""\'' in text
    assert 'lifecycle-classify' in text
    assert 'git diff --name-only HEAD^1 HEAD^2' in text
    assert 'pull_request:' in text
    assert 'permission-checks: write' not in text


def test_blocker_packet_binds_verified_run_sha_without_new_execution_authority():
    event = AuditEvent(
        event_name="workflow_run",
        action="completed",
        repository="owner/repo",
        source_kind="CODE_HEALTH_RUN",
        source_ref="41247454667",
        title="Failed CI",
        state="OPEN",
        head_sha="a" * 40,
        conclusion="failure",
        details={"env": "SECRET=value", "stderr": "token=hidden", "check_name": "test"},
    )
    packet = code_health_blocker_packet(event)
    assert packet["failure_class"] == "CI_SEMANTIC_OR_INFRA_FAILURE"
    assert packet["workflow_run_id"] == "41247454667"
    assert packet["head_sha"] == "a" * 40
    assert packet["next_action"] == "READ_EXACT_RUN_LOGS_AND_IDENTIFY_CAUSE"
    assert packet["dispatch_authorized"] is False
    assert packet["merge_authorized"] is False
    assert packet["deploy_authorized"] is False
    assert packet["can_execute"] is False
    assert "SECRET" not in str(packet)
    assert "token" not in str(packet)


def test_blocker_packet_refuses_untrusted_identity_and_distinguishes_timeout():
    event = AuditEvent(
        event_name="workflow_run",
        action="completed",
        repository="owner/repo",
        source_kind="CODE_HEALTH_RUN",
        source_ref="https://not-our-host.invalid/run?secret=x",
        title="Timeout",
        state="OPEN",
        head_sha="unsafe",
        conclusion="timed_out",
    )
    packet = code_health_blocker_packet(event)
    assert packet["workflow_run_id"] is None
    assert packet["head_sha"] is None
    assert packet["failure_class"] == "CI_TIMEOUT"
    assert packet["next_action"] == "RECONCILE_AUTHORITATIVE_RUN_AND_HEAD_IDENTITY"


def test_blocker_packet_rejects_non_ci_sources():
    with pytest.raises(ValueError, match="BLOCKER_PACKET_SOURCE_INVALID"):
        code_health_blocker_packet(AuditEvent(
            event_name="issues", action="opened", repository="owner/repo",
            source_kind="GITHUB_ISSUE", source_ref="12", title="issue", state="OPEN",
        ))
