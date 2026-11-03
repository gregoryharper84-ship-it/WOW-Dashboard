"""Offline cross-stage Claude task-only admission tests; no paid API calls."""
import datetime as dt
import importlib.util
import json
from pathlib import Path
import sys

SRC = Path(__file__).resolve().parent / "v17" / "engineering_provider_worker_admission.py"
sys.path.insert(0, str(SRC.parent))
spec = importlib.util.spec_from_file_location("worker_delivery_reentry", SRC)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
CLOCK = dt.datetime(2026, 11, 3, 5, 30, tzinfo=dt.timezone.utc)
REPO = "gregoryharper84-ship-it/WOW-Dashboard"
FLOW = "wow-v17-claude-engineering-worker.yml"


def _read_factory(*, previous="1150", target="1021", mode="valid"):
    steps = [
        {"name": name, "status": "completed", "conclusion": result}
        for name, result in module.DELIVERY_ONLY_STEPS
    ]
    if mode == "bad-step":
        steps[0]["conclusion"] = "failure"
    job = {"run_id": 71, "name": module.WORKFLOWS["anthropic"][1],
           "status": "completed", "conclusion": "failure", "steps": steps,
           "completed_at": "2026-10-23T07:41:06Z"}
    lines = [
        f"2026-10-23T07:41:06Z ##[error]ACTIONABLE_REPAIR_TRIAGE_NOT_REPAIRABLE: incident={previous}; triage repairable=false."
    ]
    if mode == "provider":
        lines.append("##[error]rate_limit_error")
    if mode == "duplicate":
        lines.append(lines[0])
    if mode == "bad-log":
        lines = ["echo '::error::ACTIONABLE_REPAIR_TRIAGE_NOT_REPAIRABLE: incident=1150'"]

    def read(cmd):
        path = cmd[2] if len(cmd) > 2 else ""
        if "/actions/workflows/" in path:
            return json.dumps({"total_count": 2, "workflow_runs": [
                {"id": 72, "run_attempt": 1, "status": "in_progress",
                 "display_title": f"wow-v17-claude-engineering-worker lease=GLOBAL incident={target}"},
                {"id": 71, "run_attempt": 1, "status": "completed", "conclusion": "failure",
                 "head_branch": "main", "event": "workflow_dispatch",
                 "path": f".github/workflows/{FLOW}"}
            ]})
        if "/actions/runs/71/attempts/1/jobs?" in path:
            if mode == "unverified":
                raise ValueError("job API unavailable")
            return json.dumps({"total_count": 1, "jobs": [job]})
        if cmd[:3] == ["gh", "run", "view"]:
            return "\n".join(lines)
        raise AssertionError("Unexpected authenticated GitHub request: " + repr(cmd))
    return read


def test_new_target_after_authenticated_task_refusal():
    decision = module.admission("anthropic", 72, _read_factory(),
                                target_incident="1021", now=CLOCK)
    assert decision["status"] == "ADMITTED"
    assert decision["code"] == "PROVIDER_TASK_ONLY_DIFFERENT_INCIDENT"
    assert decision["can_execute"] is False


def test_same_or_missing_or_invalid_target_is_held():
    for value in ("1150", "", "AUTO", "0", "1150;malicious"):
        decision = module.admission(
            "anthropic", 72, _read_factory(target="1021" if not value else value),
            target_incident=value, now=CLOCK,
        )
        assert decision["status"] == "HELD", value


def test_provider_failure_and_incomplete_provenance_never_reenter():
    for mode in ("bad-step", "provider", "duplicate", "bad-log", "unverified"):
        decision = module.admission("anthropic", 72, _read_factory(mode=mode),
                                    target_incident="1021", now=CLOCK)
        assert decision["status"] == "HELD", mode


def test_source_job_target_binding_is_mandatory():
    decision = module.admission("anthropic", 72, _read_factory(target="1150"),
                                target_incident="1021", now=CLOCK)
    assert decision["status"] == "HELD"


def test_claude_workflow_passes_its_own_exact_target():
    workflow = (Path(__file__).resolve().parents[2] /
                ".github/workflows/wow-v17-claude-engineering-worker.yml").read_text()
    block = workflow.split(
        "- name: Recheck anthropic provider breaker under the global writer mutex", 1
    )[1].split("- name: Engineering Lead agent", 1)[0]
    assert "TARGET_INCIDENT:" in block
    assert "inputs.target_incident" in block
    assert "--target-incident" in block
    assert "engineering_provider_worker_admission.py" in block
