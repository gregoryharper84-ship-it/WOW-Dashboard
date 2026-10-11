"""Fail-closed Gemini standby integration tests; no external network."""
import importlib.util
import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

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
    }
    settings.update(overrides)
    return settings


def run(**kwargs):
    return worker.run_challenger("code_review", "Public code review fixture",
        data_classification="PUBLIC", env=config(), **kwargs)


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
    return FakeResponse(json.dumps({"candidates": [
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
