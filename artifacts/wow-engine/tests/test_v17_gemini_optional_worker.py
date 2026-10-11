"""Fail-closed Gemini standby integration tests; no external network."""
import importlib.util
import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest
from v17.gemini_budget_gateway import GeminiBudgetGateway

class FakeLedger(GeminiBudgetGateway):
    def __init__(self):
        self.events=[]
    def reserve(self, **kw):
        self.events.append("RESERVED")
        return {"reservation_id":"b3505cb5-93a1-4da7-ad8e-121eb318d068"}
    def record(self, **kw):
        self.events.append(kw["stage"])
        return {"status":kw["stage"]}

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("gemini_optional_worker", ROOT / "v17" / "gemini_optional_worker.py")
assert SPEC and SPEC.loader
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)
REGISTRY = json.loads((ROOT / "v17" / "ecosystem-registry.json").read_text())


def config(**overrides):
    settings = {
        "WOW_GEMINI_ENABLED": "true",
        "WOW_GEMINI_ACTIVE_TASKS": "code_review,research_challenger",
        "GEMINI_API_KEY": "fake-test-key",
        "GEMINI_MODEL": "gemini-test-model",
        "WOW_GEMINI_PAID_API_PROJECT_VERIFIED": "true",
        "WOW_GEMINI_STANDARD_API_ENABLED": "true",
        "WOW_GEMINI_PROJECT_KEY": "test_project",
    }
    settings.update(overrides)
    return settings


def run(**kwargs):
    return worker.run_challenger("code_review", "Public code review fixture",
        data_classification="PUBLIC", env=config(), budget_gateway=FakeLedger(),
        reserved_microusd=2000, approval_ref="review:fixture",
        tariff_sha256="a"*64, **kwargs)


def test_disabled_by_default_no_network():
    called = []
    with pytest.raises(worker.GeminiWorkerError, match="GEMINI_DISABLED"):
        worker.run_challenger("code_review", "text", data_classification="PUBLIC",
                             env={}, opener=lambda *a, **k: called.append(True))
    assert not called
    assert worker.status({})["enabled"] is False


@pytest.mark.parametrize("settings,code", [
    ({"WOW_GEMINI_ACTIVE_TASKS": ""}, "GEMINI_TASK_DISABLED"),
    ({"GEMINI_API_KEY": ""}, "GEMINI_CONFIG_MISSING"),
    ({"GEMINI_MODEL": ""}, "GEMINI_CONFIG_MISSING"),
    ({"GEMINI_MODEL": "../../unsafe"}, "GEMINI_CONFIG_MISSING"),
])
def test_no_network_when_not_authorized(settings, code):
    called = []
    with pytest.raises(worker.GeminiWorkerError, match=code):
        worker.run_challenger("code_review", "public data", data_classification="PUBLIC",
                             env=config(**settings), opener=lambda *a, **k: called.append(True))
    assert not called


def test_private_inputs_blocked():
    with pytest.raises(worker.GeminiWorkerError, match="GEMINI_INPUT_CLASS_BLOCKED"):
        worker.run_challenger("code_review", "private", data_classification="PRIVATE",
                             env=config(), opener=lambda *a, **k: 1)


def test_cannot_route_unapproved_task():
    with pytest.raises(worker.GeminiWorkerError, match="GEMINI_TASK_DISABLED"):
        worker.run_challenger("probability_scoring", "fixture", data_classification="PUBLIC", env=config())


def test_oversized_input_blocked():
    with pytest.raises(worker.GeminiWorkerError, match="GEMINI_INPUT_INVALID"):
        worker.run_challenger("code_review", "x" * 12001, data_classification="PUBLIC", env=config())


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


def response(text, finish="STOP"):
    return FakeResponse(json.dumps({"responseId":"test_response_1","candidates": [
        {"finishReason": finish, "content": {"parts": [{"text": text}]}}
    ]}).encode())


def test_approved_challenger_returns_unverified_evidence_only():
    seen = []
    def opener(request, timeout):
        seen.append((request, timeout))
        return response(json.dumps({"summary": "One concern", "findings": ["missing test"],
                                    "evidence_refs": ["file:test.py"]}))
    result = run(opener=opener)
    assert result["verification_state"] == "UNVERIFIED_CHALLENGER_OUTPUT"
    assert result["probability_authority"] is False and result["can_execute"] is False
    assert seen[0][1] == 15
    assert seen[0][0].full_url.startswith(worker.API_ROOT)
    payload = json.loads(seen[0][0].data)
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    assert result["cost_usd"] is None and result["usage_metadata_status"] == "NOT_REPORTED"


@pytest.mark.parametrize("content,finish", [
    ("not json", "STOP"),
    ('{"summary":"only"}', "STOP"),
    ('{"summary":"ok","findings":[],"evidence_refs":[]}', "MAX_TOKENS"),
])
def test_invalid_model_output_fails_closed(content, finish):
    with pytest.raises(worker.GeminiWorkerError, match="GEMINI_RESPONSE_INVALID"):
        run(opener=lambda *a, **k: response(content, finish))


def test_throttling_fails_closed_without_fallback():
    def opener(*args, **kwargs):
        raise HTTPError("https://example.invalid", 429, "limit", {}, None)
    with pytest.raises(worker.GeminiWorkerError, match="GEMINI_RATE_LIMITED"):
        run(opener=opener)


def test_registry_is_optional_and_never_scoring_authority():
    provider = REGISTRY["components"]["GEMINI_STANDBY_CHALLENGER"]
    assert provider["enabled_by_default"] is False
    assert provider["probability_authority"] is False
    assert provider["terminal_authority"] is False
    assert provider["safe_hold_authority"] is False
    assert provider["can_execute"] is False
    assert not any("GEMINI" in x for route in REGISTRY["golden_paths"].values() for x in route)
    for name, handoff in REGISTRY["handoffs"].items():
        if "GEMINI" in name:
            assert handoff["critical"] is False
            assert handoff["required_verifier"] == "NONE"


def test_public_fixture_requires_paid_project_attestation_before_network():
    invoked = []
    with pytest.raises(worker.GeminiWorkerError, match="GEMINI_BILLING_UNVERIFIED"):
        worker.run_challenger(
            "code_review", "Public code review fixture", data_classification="PUBLIC",
            env=config(WOW_GEMINI_PAID_API_PROJECT_VERIFIED="false"),
            opener=lambda *a, **kw: invoked.append(True),
        )
    assert invoked == []
    assert worker.status(config(WOW_GEMINI_PAID_API_PROJECT_VERIFIED="false"))["configured"] is False


def test_response_usage_tracks_thinking_without_inventing_cost():
    def opener(*args, **kwargs):
        body = {"responseId":"test_response_2", "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps({
            "summary": "ok", "findings": [], "evidence_refs": []})}]}}],
            "usageMetadata": {"promptTokenCount": 200, "candidatesTokenCount": 35,
                              "thoughtsTokenCount": 410, "cachedContentTokenCount": 90,
                              "totalTokenCount": 645}}
        return FakeResponse(json.dumps(body).encode())
    result = run(opener=opener)
    assert result["usage_tokens"] == {
        "prompt_tokens": 200,
        "visible_output_tokens": 35,
        "thinking_tokens": 410,
        "cached_input_tokens": 90,
        "total_reported_tokens": 645,
    }
    assert result["cost_usd"] is None


def test_standard_without_authoritative_budget_gateway_cannot_call_provider():
    calls=[]
    with pytest.raises(worker.GeminiWorkerError,match="GEMINI_DURABLE_LEDGER_REQUIRED"):
        worker.run_challenger("code_review","public source",data_classification="PUBLIC",
            env=config(),opener=lambda *a,**k:calls.append(True))
    assert calls==[]


def test_standard_disabled_even_with_paid_project_and_budget():
    calls=[]
    with pytest.raises(worker.GeminiWorkerError,match="GEMINI_STANDARD_API_DISABLED"):
        worker.run_challenger("code_review","public source",data_classification="PUBLIC",
            env=config(WOW_GEMINI_STANDARD_API_ENABLED="false"),
            budget_gateway=FakeLedger(),opener=lambda *a,**k:calls.append(True))
    assert calls==[]


def test_standard_fence_precedes_post_and_completion_is_readback():
    ledger=FakeLedger()
    attempts=[]
    def fake(req,timeout):
        attempts.append(list(ledger.events))
        return response('{"summary":"ok","findings":[],"evidence_refs":[]}')
    result=worker.run_challenger("code_review","public source",
        data_classification="PUBLIC",env=config(),budget_gateway=ledger,
        reserved_microusd=2000,approval_ref="review:fixture",
        tariff_sha256="a"*64,opener=fake)
    assert attempts==[["RESERVED","OUTCOME_UNKNOWN"]]
    assert ledger.events==["RESERVED","OUTCOME_UNKNOWN","PROVIDER_CONFIRMED","RECONCILED"]
    assert result["provider_response_id"]=="test_response_1"
