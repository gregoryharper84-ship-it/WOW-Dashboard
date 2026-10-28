"""Regression contract: CISA token pressure adds telemetry, not an approval gate."""
import importlib.util
import sys
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODULE_PATH = ROOT / "v17" / "engineering_provider_failover.py"
WORKFLOW = ROOT.parents[1] / ".github/workflows/wow-v17-engineering-provider-dispatcher.yml"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("engineering_provider_failover", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

NOW = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)


def route(action="DISPATCH", provider="openai", reason="OPENAI_PRIMARY_AVAILABLE"):
    return {
        "action": action, "provider": provider, "reason": reason,
        "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER",
    }


def measured(used, limit=10000, provider="openai", captured=None):
    return {
        "provider": provider, "used_tokens": used, "limit_tokens": limit,
        "captured_at": (captured or NOW).isoformat(),
    }


def test_missing_usage_is_unknown_not_a_zero_spend_claim():
    result = module.assess_resource_pressure(route(), now=NOW)
    assert result["usage_status"] == "UNAVAILABLE"
    assert result["token_utilization_pct"] is None
    assert result["pressure_band"] == "UNKNOWN"
    assert result["may_approve_spend"] is False
    assert result["can_execute"] is False


def test_cisa_threshold_bands_are_advisory_only():
    for tokens, expected in (
        (0, "NORMAL"), (6999, "NORMAL"), (7000, "INFORMATIONAL"),
        (8000, "WARNING"), (9000, "CRITICAL"), (11000, "CRITICAL"),
    ):
        result = module.assess_resource_pressure(route(), usage=measured(tokens), now=NOW)
        assert result["pressure_band"] == expected
        assert result["usage_status"] == "FRESH_REPORTED_NOT_ATTESTED"
        assert result["may_override_dispatch"] is False
        assert result["may_reset_circuit_breaker"] is False
        assert result["may_release_writer_lease"] is False
        assert result["can_execute"] is False
        if tokens >= 8000:
            assert result["advisory_action"] == "DEFER_OPTIONAL_WORK_RETAIN_P0_RECOVERY"


def test_invalid_stale_or_mismatched_usage_never_changes_route():
    for usage, status in (
        (measured(True), "INVALID"),
        (measured(-1), "INVALID"),
        (measured(5000, 0), "INVALID"),
        (measured(5000, provider="anthropic"), "PROVIDER_MISMATCH"),
        (measured(5000, captured=NOW - timedelta(minutes=16)), "STALE_OR_FUTURE"),
        (measured(5000, captured=NOW + timedelta(minutes=5)), "STALE_OR_FUTURE"),
        ({"provider": "openai", "used_tokens": 1}, "INVALID"),
    ):
        result = module.assess_resource_pressure(route(), usage=usage, now=NOW)
        assert result["usage_status"] == status
        assert result["pressure_band"] == "UNKNOWN"
        assert result["token_utilization_pct"] is None
        assert result["may_override_dispatch"] is False


def test_provider_failure_exposed_only_as_allowlisted_code():
    result = module.assess_resource_pressure(
        route(action="SURVIVAL", provider="deterministic",
              reason="CIRCUIT_BREAKER:OPENAI_API_QUOTA_EXCEEDED:SECONDARY_FAILED:secret=do-not-log"),
        now=NOW,
    )
    assert result["observed_provider_failure_code"] == "OPENAI_API_QUOTA_EXCEEDED"
    assert "secret" not in json.dumps(result)
    assert result["usage_status"] == "UNAVAILABLE"


def test_untrusted_or_wrong_provider_receipt_cannot_be_accepted():
    bad = route()
    bad["terminal_authority"] = "LOCAL_BYPASS"
    try:
        module.assess_resource_pressure(bad, now=NOW)
    except ValueError:
        pass
    else:
        raise AssertionError("bad terminal authority accepted")
    try:
        module.assess_resource_pressure(route("DISPATCH", "deterministic"), now=NOW)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid dispatch provider accepted")


def test_survival_and_no_action_never_claim_usage_or_execution():
    for action, provider in (("SURVIVAL", "deterministic"), ("NO_ACTION", "none")):
        result = module.assess_resource_pressure(
            route(action, provider), usage=measured(9000), now=NOW,
        )
        assert result["usage_status"] == "PROVIDER_MISMATCH"
        assert result["can_execute"] is False
        assert result["pressure_band"] == "UNKNOWN"


def test_workflow_produces_receipt_without_new_dispatch_gate():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "Record AI resource pressure" in text
    assert "engineering_provider_failover.py resource-pressure" in text
    assert "${{ runner.temp }}/wow-ai-resource-pressure.json" in text
    section = text.split("- name: Record AI resource pressure", 1)[1].split("- name: Fence unresolved remote writers", 1)[0]
    assert "continue-on-error: true" in section
    assert "may_override_dispatch" not in text
    assert "steps.resource_pressure.outcome" not in text
