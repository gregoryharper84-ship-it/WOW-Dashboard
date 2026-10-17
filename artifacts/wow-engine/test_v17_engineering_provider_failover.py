import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "artifacts/wow-engine/v17/engineering_provider_failover.py"


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
    assert result.circuit_breaker_minutes == -1
    assert result.manual_recovery_required is True
    assert result.can_execute is False


def test_openai_auth_and_rate_limits_are_failover_eligible() -> None:
    module = _load_module()
    auth = module.classify_provider_failure("openai", "invalid_api_key")
    rate = module.classify_provider_failure("openai", "HTTP 429 Too Many Requests")
    assert auth.code == "OPENAI_API_AUTH_FAILED"
    assert auth.failover_eligible is True
    assert rate.code == "OPENAI_API_RATE_LIMITED"
    assert rate.failover_eligible is True


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
    assert module.circuit_breaker_active(
        "OPENAI_API_QUOTA_EXCEEDED",
        age_minutes=121,
        cooldown_minutes=120,
    )
    assert module.circuit_breaker_active(
        "OPENAI_API_QUOTA_EXCEEDED",
        age_minutes=60 * 24 * 40,
        cooldown_minutes=120,
    )
    assert not module.circuit_breaker_active(
        "OPENAI_API_RATE_LIMITED",
        age_minutes=121,
        cooldown_minutes=120,
    )
    assert module.circuit_breaker_active(
        "OPENAI_API_RATE_LIMITED",
        age_minutes=119,
        cooldown_minutes=120,
    )
    assert module.circuit_breaker_active(
        "OPENAI_API_AUTH_FAILED", age_minutes=600,
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


def test_credit_exhaustion_does_not_masquerade_as_throughput_limit() -> None:
    module = _load_module()
    credit = module.classify_provider_failure(
        "anthropic", "billing error: credit balance exhausted"
    )
    rate = module.classify_provider_failure("openai", "HTTP 429 Too Many Requests")
    assert credit.code == "ANTHROPIC_API_CREDIT_EXHAUSTED"
    assert credit.manual_recovery_required
    assert credit.circuit_breaker_minutes == -1
    assert rate.code == "OPENAI_API_RATE_LIMITED"
    assert not rate.manual_recovery_required
    assert rate.circuit_breaker_minutes == 120


def test_quota_failure_receipt_discloses_no_credential_or_execution() -> None:
    module = _load_module()
    value = module.classify_provider_failure("openai", "ERROR: Quota exceeded.")
    receipt = value.as_dict()
    assert receipt["manual_recovery_required"] is True
    assert receipt["can_execute"] is False
    assert receipt["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_echoed_shell_source_cannot_permanently_trip_quota_breaker() -> None:
    module = _load_module()
    source_only = (
        f"2026-10-14T23:59:29Z {chr(27)}[36;1mecho "
        "'ERROR: Quota exceeded. Check your plan and billing details.'"
        f"{chr(27)}[0m\n"
        "2026-10-14T23:59:31Z ##[error]Process completed with exit code 1."
    )
    result = module.classify_provider_failure("openai", source_only)
    assert result.code != "OPENAI_API_QUOTA_EXCEEDED"
    assert result.manual_recovery_required is False
    real = module.classify_provider_failure(
        "openai",
        source_only + "\n2026-10-14T23:59:31Z ERROR: Quota exceeded. Check your plan and billing details."
    )
    assert real.code == "OPENAI_API_QUOTA_EXCEEDED"
    assert real.manual_recovery_required is True


def test_standalone_api_quota_signal_still_trips_breaker() -> None:
    module = _load_module()
    result = module.classify_provider_failure(
        "openai", "##[error]provider failure\ninsufficient_quota"
    )
    assert result.code == "OPENAI_API_QUOTA_EXCEEDED"
