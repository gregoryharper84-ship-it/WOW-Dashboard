import importlib.util
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "artifacts/wow-engine/v17/engineering_provider_failover.py"


def test_resident_admission_retains_persistent_breaker_but_does_not_infer_transient_recovery():
    module = _load_module()
    now = datetime.now(timezone.utc)
    route = {
        "action": "SURVIVAL", "provider": "deterministic",
        "reason": "CIRCUIT_BREAKER:OPENAI_API_QUOTA_EXCEEDED:SECONDARY_UNAVAILABLE_OR_FAILED",
        "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER",
    }
    old = (now - timedelta(days=30)).isoformat()
    assert module.resident_provider_admission(route, updated_at=old, now=now) == "PROVIDER_QUOTA_BLOCKED"
    route["reason"] = "CIRCUIT_BREAKER:OPENAI_API_RATE_LIMITED:SECONDARY_UNAVAILABLE_OR_FAILED"
    assert module.resident_provider_admission(route, updated_at=now.isoformat(), now=now) == "PROVIDER_THROTTLED"
    assert module.resident_provider_admission(route, updated_at=old, now=now) == "PROVIDER_RECOVERY_UNVERIFIED"
    route.update(action="DISPATCH", provider="openai")
    assert module.resident_provider_admission(route, updated_at=old, now=now) == "PROVIDER_RECOVERY_UNVERIFIED"
    assert module.resident_provider_admission(route, updated_at=now.isoformat(), now=now) == "PROVIDER_ADMITTED"
    for changed in ({**route, "can_execute": True}, {**route, "action": "invalid"},
                    {**route, "reason": None}, {**route, "terminal_authority": "OTHER"}):
        assert module.resident_provider_admission(changed, updated_at=old, now=now) == "PROVIDER_STATUS_UNVERIFIED"
    assert module.resident_provider_admission(route, updated_at="invalid", now=now) == "PROVIDER_STATUS_UNVERIFIED"


def _load_module():
    sys.path.insert(0, str(MODULE_PATH.parent))
    spec = importlib.util.spec_from_file_location("engineering_provider_failover", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_openai_quota_is_failover_eligible() -> None:
    module = _load_module()
    result = module.classify_provider_failure(
        "openai",
        "ERROR: Quota exceeded. Check your plan and billing details.",
    )
    assert result.code == "OPENAI_API_QUOTA_EXCEEDED"
    assert result.failover_eligible is True
    assert result.circuit_breaker_minutes == 120
    assert result.can_execute is False


def test_openai_auth_and_rate_limits_are_failover_eligible() -> None:
    module = _load_module()
    auth = module.classify_provider_failure("openai", "invalid_api_key")
    rate = module.classify_provider_failure("openai", "HTTP 429 Too Many Requests")
    assert auth.code == "OPENAI_API_AUTH_FAILED"
    assert auth.failover_eligible is True
    assert rate.code == "OPENAI_API_RATE_LIMITED"
    assert rate.failover_eligible is True


def test_http_402_and_429_failures_never_retry_an_unhealthy_provider() -> None:
    module = _load_module()
    openai_402 = module.classify_provider_failure("openai", "HTTP 402 payment required")
    openai_429 = module.classify_provider_failure("openai", "HTTP 429 Too Many Requests")
    claude_402 = module.classify_provider_failure("claude", "status 402 payment required")
    claude_429 = module.classify_provider_failure("claude", "HTTP 429 rate limit")

    assert openai_402.code == "OPENAI_API_BILLING_REQUIRED"
    assert openai_402.failover_eligible is True
    assert module.circuit_breaker_active(openai_402.code, age_minutes=10_000)
    assert openai_429.code == "OPENAI_API_RATE_LIMITED"
    assert openai_429.failover_eligible is True
    assert module.circuit_breaker_active(openai_429.code, age_minutes=30)
    assert not module.circuit_breaker_active(openai_429.code, age_minutes=121)
    assert claude_402.code == "ANTHROPIC_API_BILLING_REQUIRED"
    assert claude_429.code == "ANTHROPIC_API_RATE_LIMITED"
    assert all(result.can_execute is False for result in (openai_402, openai_429, claude_402, claude_429))


def test_repository_permission_failure_is_not_provider_failover() -> None:
    module = _load_module()
    result = module.classify_provider_failure(
        "openai",
        "EACCES: permission denied, open '/tmp/schema.json'",
    )
    assert result.code == "NON_PROVIDER_FAILURE"
    assert result.failover_eligible is False


def test_unknown_failure_is_fail_closed() -> None:
    module = _load_module()
    result = module.classify_provider_failure("openai", "mystery failure")
    assert result.code == "UNCLASSIFIED_PROVIDER_OR_WORKFLOW_FAILURE"
    assert result.failover_eligible is False


def test_circuit_breaker_only_applies_to_known_openai_provider_failures() -> None:
    module = _load_module()
    assert module.circuit_breaker_active(
        "OPENAI_API_QUOTA_EXCEEDED",
        age_minutes=30,
        cooldown_minutes=120,
    )
    # Exhausted account/quota and invalid credentials cannot heal on a timer.
    assert module.circuit_breaker_active(
        "OPENAI_API_QUOTA_EXCEEDED",
        age_minutes=121,
        cooldown_minutes=120,
    )
    assert module.circuit_breaker_active(
        "OPENAI_API_QUOTA_EXCEEDED",
        age_minutes=60 * 24 * 30,
        cooldown_minutes=120,
    )
    assert module.circuit_breaker_active(
        "OPENAI_API_AUTH_FAILED", age_minutes=10000,
    )
    assert module.circuit_breaker_active(
        "OPENAI_API_KEY_MISSING", age_minutes=10000,
    )
    assert not module.circuit_breaker_active(
        "OPENAI_API_RATE_LIMITED", age_minutes=121,
    )
    assert module.circuit_breaker_active(
        "OPENAI_API_RATE_LIMITED", age_minutes=30,
    )
    assert not module.circuit_breaker_active(
        "OPENAI_API_QUOTA_EXCEEDED", age_minutes=-1,
    )
    assert not module.circuit_breaker_active(
        "NON_PROVIDER_FAILURE",
        age_minutes=1,
        cooldown_minutes=120,
    )


def test_survival_receipt_preserves_v17_authority(tmp_path: Path) -> None:
    module = _load_module()
    ledger = tmp_path / "incident-ledger.json"
    ledger.write_text(
        """
{
  "records": [
    {
      "postmortem_id": "PM-TEST-001",
      "severity": "P0",
      "state": "OPEN",
      "created_utc": "2026-09-29T00:00:00Z"
    }
  ]
}
""".strip()
    )
    receipt = module.survival_receipt(
        reason="OPENAI_API_QUOTA_EXCEEDED",
        parent_run_id="123",
        ledger_path=ledger,
    )
    assert receipt["status"] == "DEGRADED_DETERMINISTIC_SURVIVAL"
    assert receipt["priority"]["incident_id"] == "PM-TEST-001"
    assert receipt["provider_generation_allowed"] is False
    assert receipt["deterministic_checks_allowed"] is True
    assert receipt["probability_authority"] is False
    assert receipt["can_execute"] is False
    assert receipt["terminal_authority"] == "V17_TERMINAL_REDUCER"


def _gemini_child_fixture(*, conclusion: str = "failure"):
    module = _load_module()
    sha = "a" * 40
    repository = "gregoryharper84-ship-it/WOW-Dashboard"
    run_id = 123456
    attempt = 1
    repo_id = 1240256887
    run = {
        "id": run_id,
        "run_attempt": attempt,
        "event": "workflow_dispatch",
        "path": module.GEMINI_WORKER_WORKFLOW_PATH,
        "status": "completed",
        "conclusion": conclusion,
        "head_branch": "main",
        "head_sha": sha,
        "repository": {"id": repo_id, "full_name": repository},
        "head_repository": {"id": repo_id, "full_name": repository},
    }
    artifacts = {
        "total_count": 1,
        "artifacts": [{
            "id": 777,
            "name": f"wow-gemini-candidate-{run_id}-{attempt}",
            "expired": False,
            "digest": "sha256:" + "b" * 64,
            "workflow_run": {
                "id": run_id,
                "repository_id": repo_id,
                "head_repository_id": repo_id,
                "head_branch": "main",
                "head_sha": sha,
            },
        }],
    }
    manifest = {
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "restoration": [{
            "issue_number": 1951,
            "severity": "P0",
            "priority_rank": 1,
            "execution_lane": "RAPID",
            "rapid_stream": "NCAAF_RESTORE",
            "lease_group": "P0_NCAAF_RESTORE",
            "conflict_keys": ["NCAAF_MONEYLINE"],
        }],
        "acceleration": [],
    }
    issues = [{
        "number": 1951,
        "title": "NCAAF restoration",
        "body": "Issue text is not routing authority.",
        "state": "OPEN",
    }]
    from engineering_gemini_headless import select_incident
    selection = select_incident(
        manifest, issues, target_incident="1951",
        requested_lease="P0_NCAAF_RESTORE",
    )
    receipts = {
        "admission.json": {
            "status": "ADMITTED", "code": "ADMITTED", "provider": "gemini",
            "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER",
        },
        "selection.json": selection,
        "workspace.json": {
            "status": "TRUSTED", "code": "GEMINI_WORKSPACE_TRUSTED",
            "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER",
        },
        "model-receipt.json": {
            "status": "HELD", "code": "GEMINI_API_UPSTREAM_UNAVAILABLE",
            "exit_code": 247, "source_head_sha": sha,
            "retry_allowed": False, "can_execute": False,
            "terminal_authority": "V17_TERMINAL_REDUCER",
        },
    }
    return module, run, artifacts, receipts, manifest, issues, repository, run_id, attempt


def _verify_gemini_child(fixture):
    module, run, artifacts, receipts, manifest, issues, repository, run_id, attempt = fixture
    return module.verify_gemini_worker_completion(
        run, artifacts, receipts, expected_run_id=str(run_id),
        expected_run_attempt=str(attempt), repository=repository,
        manifest=manifest, issues=issues,
    )


def test_gemini_child_failure_requires_exact_run_artifact_and_open_issue_lease():
    fixture = _gemini_child_fixture()
    result = _verify_gemini_child(fixture)
    assert result["status"] == "VERIFIED_PROVIDER_FAILURE"
    assert result["code"] == "GEMINI_API_UPSTREAM_UNAVAILABLE"
    assert result["fallback_eligible"] is True
    assert result["target_incident"] == "1951"
    assert result["lease_group"] == "P0_NCAAF_RESTORE"
    assert result["can_execute"] is False
    assert result["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_gemini_child_success_is_only_a_verified_tested_candidate():
    fixture = _gemini_child_fixture(conclusion="success")
    receipts = fixture[3]
    sha = fixture[1]["head_sha"]
    receipts["model-receipt.json"] = {
        "status": "COMPLETED", "code": "GEMINI_CLI_COMPLETE",
        "exit_code": 0, "source_head_sha": sha, "retry_allowed": False,
        "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER",
    }
    receipts["patch-inspection.json"] = {
        "status": "CANDIDATE_ONLY", "can_execute": False,
    }
    receipts["regression-receipt.json"] = {
        "status": "TESTED_CANDIDATE", "code": "GEMINI_REGRESSION_PASS",
        "exit_code": 0, "can_execute": False,
    }
    receipts["files.json"] = ["artifacts/wow-engine/v17/example.py"]
    receipts["candidate.patch"] = "diff --git a/example.py b/example.py\n"
    result = _verify_gemini_child(fixture)
    assert result["status"] == "VERIFIED_TESTED_CANDIDATE"
    assert result["fallback_eligible"] is False
    assert result["code"] == "GEMINI_TESTED_CANDIDATE_REQUIRES_INDEPENDENT_REVIEW"
    assert result["can_execute"] is False
    assert result["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_gemini_child_failure_is_not_fallback_for_other_codes_or_exit_codes():
    fixture = _gemini_child_fixture()
    model = fixture[3]["model-receipt.json"]
    model.update(code="GEMINI_API_RATE_LIMITED", exit_code=247)
    assert _verify_gemini_child(fixture)["fallback_eligible"] is False
    model.update(code="GEMINI_API_UPSTREAM_UNAVAILABLE", exit_code=1)
    assert _verify_gemini_child(fixture)["fallback_eligible"] is False


def test_gemini_child_provenance_replay_and_missing_artifact_fail_closed():
    fixture = _gemini_child_fixture()
    module, run, artifacts, receipts, manifest, issues, repository, run_id, attempt = fixture
    cases = []

    wrong_run = dict(run, id=run_id + 1)
    cases.append((wrong_run, artifacts, receipts, issues))
    wrong_repo = dict(run, repository={"id": 1, "full_name": "attacker/repo"})
    cases.append((wrong_repo, artifacts, receipts, issues))
    wrong_event = dict(run, event="push")
    cases.append((wrong_event, artifacts, receipts, issues))
    wrong_path = dict(run, path=".github/workflows/unrelated.yml")
    cases.append((wrong_path, artifacts, receipts, issues))
    wrong_attempt = dict(run, run_attempt=attempt + 1)
    cases.append((wrong_attempt, artifacts, receipts, issues))
    wrong_head = dict(run, head_sha="c" * 40)
    cases.append((wrong_head, artifacts, receipts, issues))
    replay = {
        **artifacts,
        "artifacts": [{
            **artifacts["artifacts"][0],
            "workflow_run": {**artifacts["artifacts"][0]["workflow_run"], "id": run_id - 1},
        }],
    }
    cases.append((run, replay, receipts, issues))
    expired = {
        **artifacts,
        "artifacts": [{**artifacts["artifacts"][0], "expired": True}],
    }
    cases.append((run, expired, receipts, issues))
    missing = {"total_count": 0, "artifacts": []}
    cases.append((run, missing, receipts, issues))
    closed = [dict(issues[0], state="CLOSED")]
    cases.append((run, artifacts, receipts, closed))
    wrong_lease = dict(receipts["selection.json"], lease_group="P0_OTHER_RESTORE")
    cases.append((run, artifacts, {**receipts, "selection.json": wrong_lease}, issues))

    for source_run, source_artifacts, source_receipts, source_issues in cases:
        result = module.verify_gemini_worker_completion(
            source_run, source_artifacts, source_receipts,
            expected_run_id=str(run_id), expected_run_attempt=str(attempt),
            repository=repository, manifest=manifest, issues=source_issues,
        )
        assert result["status"] == "HELD"
        assert result["fallback_eligible"] is False
        assert result["can_execute"] is False
        assert result["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_gemini_failure_missing_typed_receipt_cannot_be_inferred_from_titles_or_logs():
    fixture = _gemini_child_fixture()
    receipts = {key: value for key, value in fixture[3].items() if key != "model-receipt.json"}
    result = fixture[0].verify_gemini_worker_completion(
        fixture[1], fixture[2], receipts,
        expected_run_id=str(fixture[7]), expected_run_attempt=str(fixture[8]),
        repository=fixture[6], manifest=fixture[4], issues=fixture[5],
    )
    assert result["status"] == "HELD"
    assert result["fallback_eligible"] is False
    assert result["code"] == "GEMINI_CHILD_ARTIFACT_RECEIPT_MISSING"
