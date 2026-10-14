"""Adversarial regression tests for issue #1693 autonomous CI startup repair."""
from __future__ import annotations

import pytest

from v17.ci_registry_retry import REPO, RetryBlocked, classify, verify_live_run

SHA = "f" * 40
POSTGRES_JOB = "WOW governed backend shard / 0"
LOG = (
    "Starting postgres service container\n"
    "docker pull public.ecr.aws/docker/library/postgres:16\n"
    "Error response from daemon: toomanyrequests: Data limit exceeded\n"
    "Docker pull failed with exit code 1\n"
)


def event(*, name="wow-engine-verify", attempt=1, conclusion="failure",
          head_repo=REPO, event_name="pull_request", **run_overrides):
    run = {
        "id": 38343561368, "name": name,
        "path": ".github/workflows/" + name + ".yml",
        "head_repository": {"full_name": head_repo},
        "head_sha": SHA, "status": "completed",
        "conclusion": conclusion, "event": event_name,
        "run_attempt": attempt,
    }
    run.update(run_overrides)
    return {"repository": {"full_name": REPO}, "workflow_run": run}


def infra_steps(*, run_tests=False):
    return [
        {"name": "Set up job", "status": "completed", "conclusion": "success"},
        {"name": "Initialize containers", "status": "completed", "conclusion": "failure"},
        {"name": "Checkout", "status": "completed",
         "conclusion": "success" if run_tests else "skipped"},
        {"name": "Install backend validation dependencies", "status": "completed",
         "conclusion": "skipped"},
        {"name": "Run full-coverage shard", "status": "completed",
         "conclusion": "skipped"},
        {"name": "Stop containers", "status": "completed", "conclusion": "success"},
        {"name": "Complete job", "status": "completed", "conclusion": "success"},
    ]


def job(name=POSTGRES_JOB, *, failed=True, steps=None, id=115119760821):
    return {"id": id, "name": name, "status": "completed",
            "conclusion": "failure" if failed else "success",
            "steps": steps if steps is not None else infra_steps()}


def inventory(*rows):
    return {"total_count": len(rows), "jobs": list(rows)}


def check(ev=None, jobs=None, log=LOG):
    return classify(
        ev or event(),
        jobs or inventory(job(), job("WOW governed probability backend", id=115120196676)),
        lambda _: log,
    )


def test_real_runner_service_startup_error_allows_single_same_run_retry():
    result = check()
    assert result.receipt() == {
        "state": "RETRY_ALLOWED",
        "reason": "CI_INFRA_IMAGE_PULL_RATE_LIMIT",
        "run_id": 38343561368, "head_sha": SHA,
        "failed_job_ids": [115119760821],
        "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER",
    }


def test_same_run_attempt_two_never_retries_even_if_throttle_repeats():
    result = check(ev=event(attempt=2))
    assert result.state == "RETRY_EXHAUSTED"
    assert result.failed_job_ids == ()


def test_passing_workflow_does_not_retry():
    result = check(ev=event(conclusion="success"))
    assert result.state == "NO_ACTION"


@pytest.mark.parametrize("changes,code", [
    ({"path": ".github/workflows/attacker.yml"}, "WORKFLOW_PROVENANCE_INVALID"),
    ({"name": "wow-v17-render-production-deploy"}, "WORKFLOW_PROVENANCE_INVALID"),
    ({"head_sha": "bad"}, "RUN_IDENTITY_INVALID"),
    ({"head_repository": {"full_name": "outsider/fork"}}, "WORKFLOW_PROVENANCE_INVALID"),
    ({"run_attempt": None}, "RUN_ATTEMPT_UNVERIFIED"),
    ({"event": "workflow_dispatch"}, "WORKFLOW_TRIGGER_UNTRUSTED"),
    ({"status": "in_progress"}, "WORKFLOW_NOT_COMPLETED"),
])
def test_untrusted_or_incomplete_workflow_identity_fails_closed(changes, code):
    with pytest.raises(RetryBlocked, match=code):
        check(ev=event(**changes))


@pytest.mark.parametrize("bad_log", [
    "", "docker pull postgres:16\ntoomanyrequests: Data limit exceeded\n",
    "docker pull public.ecr.aws/docker/library/postgres:16\nFAILED test\n",
    "docker pull public.ecr.aws/docker/library/postgres:16\n"
    "toomanyrequests: Data limit exceeded\n",  # no verified Docker exit
])
def test_logs_must_prove_service_provisioning_failure(bad_log):
    with pytest.raises(RetryBlocked, match="THROTTLE_EVIDENCE_MISSING|DOCKER_SERVICE_START_FAILURE_UNVERIFIED"):
        check(log=bad_log)


def test_workflow_with_real_test_failure_is_never_retried():
    real_failure = job("WOW governed backend shard / 1", id=2, steps=[
        {"name": "Set up job", "status": "completed", "conclusion": "success"},
        {"name": "Initialize containers", "status": "completed", "conclusion": "success"},
        {"name": "Run full-coverage shard", "status": "completed", "conclusion": "failure"},
    ])
    with pytest.raises(RetryBlocked, match="CONTAINER_INITIALIZATION_FAILURE_MISSING"):
        check(jobs=inventory(job(), real_failure))


def test_malicious_test_cannot_print_provider_throttle_to_bypass_gate():
    malicious = job(steps=infra_steps(run_tests=True))
    with pytest.raises(RetryBlocked, match="EXECUTED_TEST_OR_MALFORMED_STEPS"):
        check(jobs=inventory(malicious), log=LOG)


def test_unexpected_failed_job_blocks_entire_run_retry():
    with pytest.raises(RetryBlocked, match="NON_INFRA_FAILURE"):
        check(jobs=inventory(job(), job("WOW independent QA exact-head", id=4)))


def test_truncated_or_missing_job_inventory_fails_closed():
    with pytest.raises(RetryBlocked, match="JOB_INVENTORY_TRUNCATED"):
        check(jobs={"total_count": 2, "jobs": [job()]})
    with pytest.raises(RetryBlocked, match="JOBS_RESPONSE_INVALID"):
        check(jobs={"jobs": [job()]})


def test_missing_container_steps_do_not_trigger_retry():
    with pytest.raises(RetryBlocked, match="JOB_STEPS_UNVERIFIED"):
        check(jobs=inventory({"id": 1, "name": POSTGRES_JOB,
                              "status": "completed", "conclusion": "failure"}))


def test_failure_from_both_workflow_families_is_supported():
    ev = event(name="wow-verify")
    j = job("WOW required-three shard / gate-engine")
    assert check(ev=ev, jobs=inventory(j, job("WOW required-three regression", id=99))).state == "RETRY_ALLOWED"


def test_quota_can_never_trigger_more_than_four_image_job_retries():
    jobs = inventory(*(job("WOW governed backend shard / 0", id=i + 1) for i in range(5)))
    with pytest.raises(RetryBlocked, match="IMAGE_FAILURE_COUNT_INVALID"):
        check(jobs=jobs)


def test_non_failure_no_status_fabrication():
    assert check().receipt()["can_execute"] is False
    assert "success" not in check().receipt().values()


def test_changed_exact_head_and_attempt_never_retry():
    prior = event()["workflow_run"]
    verified = dict(prior)
    verify_live_run(prior, verified)
    for key, altered in (("run_attempt", 2), ("head_sha", "e" * 40),
                         ("status", "in_progress"), ("path", "foreign.yml")):
        changed = dict(verified, **{key: altered})
        with pytest.raises(RetryBlocked, match="RUN_MOVED_OR_RETRIED:" + key):
            verify_live_run(prior, changed)


def test_missing_current_run_evidence_fails_closed():
    with pytest.raises(RetryBlocked, match="CURRENT_RUN_UNREADABLE"):
        verify_live_run(event()["workflow_run"], None)


def test_watchdog_runs_only_on_protected_main_and_preserves_mandatory_gates():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/wow-v17-ci-postgres-throttle-retry.yml").read_text()
    assert "ref: main" in workflow
    assert "persist-credentials: false" in workflow
    assert "workflow_run:" in workflow
    assert "types: [completed]" in workflow
    assert "--event \"$GITHUB_EVENT_PATH\" --execute" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "actions: write" in workflow
    assert "gh pr merge" not in workflow
    assert "deployment" not in workflow.lower()
    assert "issue_comment" not in workflow
    assert "can_execute == false" in workflow
    assert "V17_TERMINAL_REDUCER" in workflow
    backend = (root / ".github/workflows/wow-engine-verify.yml").read_text()
    required = (root / ".github/workflows/wow-verify.yml").read_text()
    assert "WOW governed probability backend" in backend
    assert "WOW required-three regression" in required



def test_matching_second_attempt_is_terminal_no_action_not_watchdog_failure():
    rerun = event(attempt=2)["workflow_run"]
    verify_live_run(rerun, dict(rerun))
    assert check(ev=event(attempt=2)).receipt()["state"] == "RETRY_EXHAUSTED"


# --- Claude adversarial-review additions (proposal on top of 5fb6e17) -------------------

import json as _json
import subprocess as _subprocess
from pathlib import Path as _Path

import yaml as _yaml

from v17 import ci_registry_retry as _crr
from v17.ci_registry_retry import NotEligible, PULL_EVIDENCE

_ROOT = _Path(__file__).resolve().parents[2]
DOCKERHUB_LOG = (
    "Starting postgres service container\n"
    "/usr/bin/docker pull postgres:16\n"
    "Error response from daemon: toomanyrequests: You have reached your pull rate limit. "
    "You may increase the limit by authenticating and upgrading\n"
    "Docker pull failed with exit code 1\n"
)


def test_dockerhub_postgres_throttle_on_current_main_image_is_recovered():
    """Counterexample: main moved to Docker Hub postgres:16 (ed00153a); 5fb6e17 never retried it."""
    verdict = check(log=DOCKERHUB_LOG)
    assert verdict.state == "RETRY_ALLOWED"
    assert verdict.reason == "CI_INFRA_IMAGE_PULL_RATE_LIMIT"


def test_throttle_wording_must_match_registry_of_pulled_image():
    mixed = LOG.replace("toomanyrequests: Data limit exceeded",
                        "something else failed")
    with pytest.raises(NotEligible, match="THROTTLE_EVIDENCE_MISSING"):
        check(log=mixed)


@pytest.mark.parametrize("workflow", ["wow-verify.yml", "wow-engine-verify.yml"])
def test_required_workflow_postgres_images_are_covered_by_pull_evidence(workflow):
    """Drift guard: changing the service image without updating the matcher must fail CI."""
    wf = _yaml.safe_load((_ROOT / ".github/workflows" / workflow).read_text())
    images = {svc["image"] for job in wf["jobs"].values()
              for svc in (job.get("services") or {}).values()
              if svc.get("image", "").rsplit("/", 1)[-1].split(":", 1)[0] == "postgres"}
    assert images, workflow
    covered = {pull.removeprefix("docker pull ") for pull, _ in PULL_EVIDENCE}
    assert images <= covered, f"uncovered postgres images {images - covered}"


def test_real_test_failure_is_typed_not_eligible_not_blocked():
    jobs = inventory(job(steps=[dict(s, conclusion="failure") if s["name"] == "Run full-coverage shard"
                                else dict(s, conclusion="success") if s["name"] in ("Initialize containers", "Checkout")
                                else s for s in infra_steps()]))
    with pytest.raises(NotEligible):
        check(jobs=jobs)


def test_malformed_step_record_stays_blocked_not_not_eligible():
    steps = infra_steps() + [{"name": "Set up job", "status": "completed", "conclusion": "weird"}]
    with pytest.raises(RetryBlocked) as exc:
        check(jobs=inventory(job(steps=steps)))
    assert not isinstance(exc.value, NotEligible)


def _main_with(monkeypatch, tmp_path, jobs, log=LOG):
    ev = event()
    path = tmp_path / "event.json"
    path.write_text(_json.dumps(ev))
    calls = []

    def fake_gh(*args):
        calls.append(args)
        if args[:2] == ("api", f"repos/{REPO}/actions/runs/{ev['workflow_run']['id']}"):
            return _json.dumps(ev["workflow_run"])
        if args[0] == "api" and "/jobs?" in args[1]:
            return _json.dumps(jobs)
        if args[:2] == ("run", "view"):
            return log
        if "--method" in args:
            return "{}"
        raise AssertionError(args)

    monkeypatch.setattr(_crr, "gh", fake_gh)
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setattr("sys.argv", ["ci_registry_retry.py", "--event", str(path), "--execute"])
    return _crr.main(), calls


def test_main_real_test_failure_exits_zero_with_no_action_not_eligible(monkeypatch, tmp_path, capsys):
    failed = job(steps=[dict(s, conclusion="failure") if s["name"] == "Run full-coverage shard"
                        else dict(s, conclusion="success") if s["name"] == "Checkout" else s
                        for s in infra_steps()])
    rc, calls = _main_with(monkeypatch, tmp_path, inventory(failed))
    receipt = _json.loads(capsys.readouterr().out)
    assert rc == 0
    assert receipt["state"] == "NO_ACTION_NOT_ELIGIBLE"
    assert receipt["reason"].startswith("EXECUTED_TEST_OR_MALFORMED_STEPS")
    assert not any("--method" in c for c in calls), "must never rerun a real test failure"


def test_main_dockerhub_throttle_dispatches_exactly_one_rerun(monkeypatch, tmp_path, capsys):
    rc, calls = _main_with(monkeypatch, tmp_path, inventory(job()), log=DOCKERHUB_LOG)
    assert rc == 0 and _json.loads(capsys.readouterr().out)["state"] == "RETRY_DISPATCHED"
    assert sum("--method" in c for c in calls) == 1


def _run_step(tmp_path, stub_stdout: str, stub_rc: int):
    wf = _yaml.safe_load((_ROOT / ".github/workflows/wow-v17-ci-postgres-throttle-retry.yml").read_text())
    steps = wf["jobs"]["retry-provisioning-only"]["steps"]
    script = next(s for s in steps if s.get("id") == "retry")["run"]
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "python"
    stub.write_text(f"#!/bin/sh\ncat <<'EOF'\n{stub_stdout}\nEOF\nexit {stub_rc}\n")
    stub.chmod(0o755)
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "RUNNER_TEMP": str(tmp_path),
           "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"), "GITHUB_EVENT_PATH": "/dev/null"}
    proc = _subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, cwd=tmp_path)
    return proc, tmp_path / "wow-ci-registry-retry-receipt.json", tmp_path / "summary.md"


@pytest.mark.skipif(_subprocess.run(["which", "jq"], capture_output=True).returncode, reason="jq required")
def test_workflow_blocked_path_still_writes_receipt_and_summary_and_stays_red(tmp_path):
    blocked = '{"can_execute": false, "reason": "GITHUB_API_OPERATION_FAILED", "state": "BLOCKED_WITH_EXACT_REASON", "terminal_authority": "V17_TERMINAL_REDUCER"}'
    proc, receipt, summary = _run_step(tmp_path, blocked, 1)
    assert proc.returncode == 1
    assert _json.loads(receipt.read_text())["state"] == "BLOCKED_WITH_EXACT_REASON"
    assert "BLOCKED_WITH_EXACT_REASON" in summary.read_text()


@pytest.mark.skipif(_subprocess.run(["which", "jq"], capture_output=True).returncode, reason="jq required")
def test_workflow_not_eligible_is_green_and_can_execute_true_is_red(tmp_path):
    ok = '{"can_execute": false, "reason": "NON_INFRA_FAILURE:x", "state": "NO_ACTION_NOT_ELIGIBLE", "terminal_authority": "V17_TERMINAL_REDUCER"}'
    (tmp_path / "ok").mkdir(); (tmp_path / "bad").mkdir()
    proc, _, _ = _run_step(tmp_path / "ok", ok, 0)
    assert proc.returncode == 0
    bad = ok.replace('"can_execute": false', '"can_execute": true')
    proc, _, _ = _run_step(tmp_path / "bad", bad, 0)
    assert proc.returncode == 1


def test_receipt_upload_always_runs_with_error_on_missing_file():
    wf = _yaml.safe_load((_ROOT / ".github/workflows/wow-v17-ci-postgres-throttle-retry.yml").read_text())
    upload = next(s for s in wf["jobs"]["retry-provisioning-only"]["steps"]
                  if str(s.get("uses", "")).startswith("actions/upload-artifact@"))
    assert upload["if"] == "always()"
    assert upload["with"]["if-no-files-found"] == "error"
