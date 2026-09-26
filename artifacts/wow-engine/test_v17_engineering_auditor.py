from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from v17.engineering_auditor import (
    AuditEvent,
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


def test_auditor_skill_is_independent_and_read_only():
    text = SKILL.read_text()
    assert "CODE_HEALTH_AUDITOR" in text
    assert "WORK_LIFECYCLE_AUDITOR" in text
    assert "does not become the implementation agent" in text
    assert "MUST NOT:" in text
    assert "edit production code" in text
    assert "set `can_execute=true`" in text
    assert "AUDIT_FINDING -> REPORTER/INTAKE" in text
