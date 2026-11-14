"""Regression guard for domain-scoped manual V17 provider admission.

2026-10-27: four distinct sport dispatches with force_provider=auto shared
the AUTO concurrency key; GitHub cancelled pending entries before worker admission.
The production remote-writer lease fence is a separate, mandatory protection.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-engineering-provider-dispatcher.yml"


def _group_source():
    text = WORKFLOW.read_text()
    return text.split("\nconcurrency:\n", 1)[1].split("\njobs:\n", 1)[0]


def test_explicit_manual_sport_leases_precede_auto_bucket():
    group = _group_source()
    lease_clause = (
        "github.event_name == 'workflow_dispatch' &&\n"
        "    inputs.lease_group != '' && inputs.lease_group != 'GLOBAL' &&\n"
        "    inputs.lease_group"
    )
    auto_clause = "(inputs.force_provider == 'auto' ||\n    inputs.force_provider == 'gemini') && 'AUTO'"
    assert lease_clause in group
    assert auto_clause in group
    assert group.index(lease_clause) < group.index(auto_clause)
    assert "cancel-in-progress: false" in group


def test_unscoped_automatic_intake_and_cursor_remain_serialized():
    group = _group_source()
    assert "github.event_name == 'schedule'" in group
    assert "github.event.workflow_run.name == 'wow-v17-nightly-engineering-scan'" in group
    assert "github.event.workflow_run.name != 'wow-v17-nightly-engineering-scan'" in group
    assert "inputs.force_provider == 'cursor' && 'CURSOR_SINGLE_AGENT'" in group
    assert "inputs.lease_group || 'GLOBAL'" in group


def test_remote_writer_fence_remains_a_separate_mandatory_dispatch_gate():
    text = WORKFLOW.read_text()
    assert "python artifacts/wow-engine/v17/engineering_remote_fallback.py guard" in text
    assert "steps.remote_fence.outcome == 'success'" in text
    assert "REMOTE_WRITER_RECONCILIATION_REQUIRED" in text
    assert "can_execute: false" in text or "can_execute: (false)" in text or "can_execute: false" in text


def test_unavailable_failure_log_can_never_disable_provider_breaker():
    """A failed GitHub log fetch must not masquerade as an empty failure log."""
    text = WORKFLOW.read_text()
    classifier = text.split("          classify_run() {", 1)[1].split("          }", 1)[0]
    assert 'if ! gh run view "$run_id"' in classifier
    assert "--log-failed" in classifier
    assert "PROVIDER_FAILURE_LOG_FETCH_UNVERIFIED" in classifier
    assert "return 1" in classifier
    assert "|| true" not in classifier
    routing = text.split("          route_existing_provider() {", 1)[1]
    assert "OPENAI_FAILURE_CLASSIFICATION_UNVERIFIED" in routing
    assert 'if ! classification=$(classify_run "$latest_id" openai); then' in routing


def test_dispatcher_job_rejects_unmerged_manual_ref():
    """No provider control-plane credentials or actions on unmerged refs."""
    text = WORKFLOW.read_text()
    provider = text.split("\n  provider-control:\n", 1)[1].split("\n    steps:\n", 1)[0]
    assert "github.repository == 'gregoryharper84-ship-it/WOW-Dashboard'" in provider
    assert "github.ref == 'refs/heads/main'" in provider
    assert "runs-on: ubuntu-latest" in provider


def test_openai_and_claude_post_lock_breaker_remains_mandatory():
    root = ROOT / ".github/workflows"
    for workflow, provider in (
        ("wow-v17-chatgpt-engineering-worker.yml", "openai"),
        ("wow-v17-claude-engineering-worker.yml", "anthropic"),
    ):
        content = (root / workflow).read_text(encoding="utf-8")
        assert "github.ref == 'refs/heads/main'" in content
        assert "wow-v17-engineering-implementation-single-writer" in content
        guard = "Recheck " + provider + " provider breaker under the global writer mutex"
        assert guard in content
        assert content.index(guard) < content.index("- name: Engineering Lead agent")
        assert "engineering_provider_worker_admission.py" in content


def test_queued_provider_gate_holds_quota_and_same_run_reruns():
    import datetime as dt
    import importlib.util
    import json
    import sys

    src = ROOT / "artifacts/wow-engine/v17/engineering_provider_worker_admission.py"
    sys.path.insert(0, str(src.parent))
    spec = importlib.util.spec_from_file_location("post_lock_provider", src)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def read(command):
        target = command[2] if len(command) > 2 else ""
        if "/actions/workflows/" in target:
            return json.dumps({"total_count": 1, "workflow_runs": [
                {"id": 71, "run_attempt": 2, "status": "in_progress"}
            ]})
        if "/attempts/1/jobs?" in target:
            return json.dumps({"total_count": 1, "jobs": [{
                "run_id": 71, "status": "completed",
                "name": "WOW V17 ChatGPT engineering team",
                "conclusion": "failure",
                "steps": [{"name": "Engineering Lead agent", "conclusion": "failure",
                           "completed_at": "2026-10-29T00:00:00Z"}],
            }]})
        if command[:3] == ["gh", "run", "view"]:
            return "insufficient_quota"
        raise AssertionError("Unexpected GitHub evidence")

    clock = dt.datetime(2026, 10, 29, 0, 30, tzinfo=dt.timezone.utc)
    denied = module.admission("openai", 71, read, run_attempt=2, now=clock)
    assert denied["status"] == "HELD"
    assert "CIRCUIT_BREAKER" in denied["code"]


def test_provider_worker_read_failure_never_grants_admission():
    import importlib.util
    import sys

    src = ROOT / "artifacts/wow-engine/v17/engineering_provider_worker_admission.py"
    sys.path.insert(0, str(src.parent))
    spec = importlib.util.spec_from_file_location("post_lock_provider_failure", src)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def unavailable(_command):
        raise ValueError("authenticated provider inventory unreadable")

    result = module.admission("anthropic", 71, unavailable)
    assert result["status"] == "HELD"
    assert result["can_execute"] is False


def test_no_provider_worker_run_provenance_cannot_be_treated_as_first_use():
    import importlib.util
    import json
    import sys
    src = ROOT / "artifacts/wow-engine/v17/engineering_provider_worker_admission.py"
    sys.path.insert(0, str(src.parent))
    spec = importlib.util.spec_from_file_location("post_lock_empty_inventory", src)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def not_indexed(_cmd):
        return json.dumps({"total_count": 0, "workflow_runs": []})

    denied = module.admission("openai", 71, not_indexed)
    assert denied == {
        "status": "HELD",
        "code": "PROVIDER_WORKER_CURRENT_RUN_UNVERIFIED",
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }
