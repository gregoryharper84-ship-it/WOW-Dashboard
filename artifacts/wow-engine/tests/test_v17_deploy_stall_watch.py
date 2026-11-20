"""Stall detection: authorized main must begin covering deploys within 15/30 min."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from v17 import deploy_stall_watch as stall
from v17.agent_identity_policy import REQUIRED_REGRESSION_CHECKS
from v17.lean_production_readback import REPO, SERVICES

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "wow-v17-deploy-stall-watch.yml"
SHA = "a" * 40
OLD = "c" * 40
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def _service(role: str, trigger: str = "off") -> dict:
    return {
        "id": SERVICES[role],
        "branch": "main",
        "repo": "https://github.com/" + REPO,
        "autoDeployTrigger": trigger,
    }


def _check(name: str, *, completed_at: str, ident: int = 1) -> dict:
    return {
        "id": ident,
        "name": name,
        "head_sha": SHA,
        "status": "completed",
        "conclusion": "success",
        "completed_at": completed_at,
        "app": {"id": 15368},
    }


def _checks(*, age_seconds: int) -> list[dict]:
    stamp = (NOW - timedelta(seconds=age_seconds)).isoformat().replace("+00:00", "Z")
    return [_check(n, completed_at=stamp, ident=i + 1)
            for i, n in enumerate(REQUIRED_REGRESSION_CHECKS)]


def test_within_grace_is_not_a_stall():
    out = stall.classify_stall(
        main_sha=SHA,
        checks_state="success",
        age_seconds=5 * 60,
        authorized_at="2026-11-20T11:55:00Z",
        services={role: _service(role) for role in SERVICES},
        progress={"web": "missing", "worker": "missing"},
    )
    assert out["status"] == "AUTHORIZED_WITHIN_GRACE"
    assert out["can_execute"] is False


def test_alert_at_fifteen_minutes_without_covering_deploy():
    out = stall.classify_stall(
        main_sha=SHA,
        checks_state="success",
        age_seconds=16 * 60,
        authorized_at="2026-11-20T11:44:00Z",
        services={role: _service(role) for role in SERVICES},
        progress={"web": "missing", "worker": "in_progress"},
    )
    assert out["status"] == "DEPLOY_STALL_ALERT"
    assert out["missing_services"] == ["web"]


def test_escalation_at_thirty_minutes():
    out = stall.classify_stall(
        main_sha=SHA,
        checks_state="success",
        age_seconds=31 * 60,
        authorized_at="2026-11-20T11:29:00Z",
        services={role: _service(role) for role in SERVICES},
        progress={"web": "missing", "worker": "missing"},
    )
    assert out["status"] == "DEPLOY_STALL_ESCALATION"


def test_covering_deploy_clears_stall_even_when_old():
    out = stall.classify_stall(
        main_sha=SHA,
        checks_state="success",
        age_seconds=45 * 60,
        authorized_at="2026-11-20T11:15:00Z",
        services={role: _service(role) for role in SERVICES},
        progress={"web": "live", "worker": "in_progress"},
    )
    assert out["status"] == "DEPLOY_COVERING_OBSERVED"


def test_not_authorized_is_not_a_stall():
    out = stall.classify_stall(
        main_sha=SHA,
        checks_state="pending",
        age_seconds=None,
        authorized_at=None,
        services={role: _service(role) for role in SERVICES},
        progress={"web": "missing", "worker": "missing"},
    )
    assert out["status"] == "NOT_AUTHORIZED_NO_STALL"


def test_dual_writer_residue_is_flagged_while_checks_pass_remains():
    out = stall.classify_stall(
        main_sha=SHA,
        checks_state="success",
        age_seconds=60,
        authorized_at="2026-11-20T11:59:00Z",
        services={role: _service(role, trigger="checksPass") for role in SERVICES},
        progress={"web": "live", "worker": "live"},
    )
    assert out["dual_writer_residue"] is True
    assert out["dual_writer_status"] == "DUAL_WRITER_NATIVE_STILL_ENABLED"
    assert out["native_auto_deploy_retired"] is False


def test_foreign_trigger_holds():
    with pytest.raises(stall.StallHold, match="RENDER_SINGLE_WRITER_SERVICE_CONFIGURATION_MISMATCH:web"):
        stall.classify_stall(
            main_sha=SHA,
            checks_state="success",
            age_seconds=60,
            authorized_at="2026-11-20T11:59:00Z",
            services={"web": _service("web", trigger="commit"), "worker": _service("worker")},
            progress={"web": "missing", "worker": "missing"},
        )


def test_authorization_age_uses_latest_required_check_completion():
    checks = _checks(age_seconds=20 * 60)
    checks.append(_check(REQUIRED_REGRESSION_CHECKS[0], completed_at="2026-11-20T11:50:00Z", ident=99))
    state, age, authorized = stall.authorization_age_seconds(checks, sha=SHA, now=NOW)
    assert state == "success"
    assert age == 10 * 60
    assert authorized == "2026-11-20T11:50:00Z"


def test_run_wires_github_and_render_read_only():
    checks = _checks(age_seconds=20 * 60)
    deploys = {
        role: [{"id": f"dep-old-{role}", "status": "live", "commit": {"id": OLD}}]
        for role in SERVICES
    }
    calls: list[str] = []

    def fetch(method, url, body):
        assert method == "GET" and body is None
        calls.append(url)
        if url.endswith("/branches/main"):
            return {"commit": {"sha": SHA}}
        if "/check-runs" in url:
            name = url.split("check_name=", 1)[1]
            from urllib.parse import unquote
            return {"check_runs": [c for c in checks if c["name"] == unquote(name)]}
        role = next(r for r, sid in SERVICES.items() if sid in url)
        if url.endswith("/deploys?limit=20"):
            return [{"deploy": d} for d in deploys[role]]
        return _service(role)

    out = stall.run(fetch=fetch, now=NOW)
    assert out["status"] == "DEPLOY_STALL_ALERT"
    assert out["missing_services"] == ["web", "worker"]
    assert all(not url.startswith("https://api.render.com/") or "PATCH" not in url for url in calls)


def test_workflow_is_optional_main_scoped_and_read_only():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    assert "schedule" in workflow[True]
    assert workflow["permissions"]["contents"] == "read"
    job = workflow["jobs"]["stall-watch"]
    assert job["environment"] == "wow-release"
    assert job["env"]["WOW_CAN_EXECUTE"] == "false"
    for step in job["steps"]:
        assert step.get("continue-on-error") is True
    scripts = "\n".join(str(step.get("run", "")) for step in job["steps"])
    assert "GITHUB_STEP_SUMMARY" in scripts
    assert "DEPLOY_STALL_WATCH_INCOMPLETE" in scripts or "can_execute=false" in scripts
    assert "v17.deploy_stall_watch" in scripts


def test_main_emits_error_annotations_to_stdout(monkeypatch, tmp_path, capsys):
    receipt = tmp_path / "receipt.json"
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("WOW_CAN_EXECUTE", "false")
    monkeypatch.setenv("WOW_DRY_RUN_ONLY", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GH_TOKEN", "ghs_x")
    monkeypatch.setenv("RENDER_API_KEY", "rnd_x")
    monkeypatch.setenv("STALL_RECEIPT", str(receipt))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(
        stall,
        "run",
        lambda **_: {
            "status": "DEPLOY_STALL_ESCALATION",
            "reason": "NO_COVERING_DEPLOY_AFTER_ESCALATION_WINDOW",
            "dual_writer_residue": True,
            "can_execute": False,
        },
    )
    assert stall.main() == 0
    stdout = capsys.readouterr().out
    assert "::error::DEPLOY_STALL_DEPLOY_STALL_ESCALATION:" in stdout
    assert "::error::DUAL_WRITER_NATIVE_STILL_ENABLED:" in stdout
    assert "::error::DEPLOY_STALL_DEPLOY_STALL_ESCALATION:" in summary.read_text()
