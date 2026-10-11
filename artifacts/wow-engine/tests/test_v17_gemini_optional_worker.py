"""Offline Gemini challenger tests; never call the real provider."""
import io
import json
from urllib.error import HTTPError

import pytest
from v17 import gemini_optional_worker as worker


def enabled(**override):
    env = {
        "WOW_GEMINI_ENABLED": "true",
        "WOW_GEMINI_ACTIVE_TASKS": "code_review",
        "WOW_GEMINI_PAID_API_PROJECT_VERIFIED": "true",
        "WOW_GEMINI_MANUAL_CHALLENGE_APPROVED": "true",
        "GEMINI_API_KEY": "fake-offline-test-only",
        "GEMINI_MODEL": "gemini-offline-test",
    }
    env.update(override)
    return env


def test_defaults_fail_closed_without_network():
    calls = []
    with pytest.raises(worker.GeminiWorkerError, match="GEMINI_DISABLED"):
        worker.run_challenger("code_review", "public fixture",
            data_classification="PUBLIC", env={}, opener=lambda *a, **k: calls.append(True))
    assert not calls
    assert worker.status({})["configured"] is False


@pytest.mark.parametrize("override,expected", [
    ({"WOW_GEMINI_ACTIVE_TASKS": ""}, "GEMINI_TASK_DISABLED"),
    ({"WOW_GEMINI_PAID_API_PROJECT_VERIFIED": "false"}, "GEMINI_BILLING_UNVERIFIED"),
    ({"WOW_GEMINI_MANUAL_CHALLENGE_APPROVED": "false"}, "GEMINI_MANUAL_APPROVAL_REQUIRED"),
    ({"GEMINI_API_KEY": ""}, "GEMINI_CONFIG_MISSING"),
    ({"GEMINI_MODEL": "../../other"}, "GEMINI_CONFIG_MISSING"),
])
def test_gates_prevent_network(override, expected):
    calls = []
    with pytest.raises(worker.GeminiWorkerError, match=expected):
        worker.run_challenger("code_review", "public fixture",
            data_classification="PUBLIC", env=enabled(**override),
            opener=lambda *a, **k: calls.append(True))
    assert not calls


def test_private_input_and_unapproved_task_fail_closed():
    for task,cls,code in [
        ("code_review","PRIVATE","GEMINI_INPUT_CLASS_BLOCKED"),
        ("probability_scoring","PUBLIC","GEMINI_TASK_DISABLED"),
    ]:
        with pytest.raises(worker.GeminiWorkerError, match=code):
            worker.run_challenger(task, "fixture", data_classification=cls, env=enabled())


def test_sensitive_material_fail_closed():
    calls = []
    with pytest.raises(worker.GeminiWorkerError, match="GEMINI_SENSITIVE_MATERIAL_REJECTED"):
        worker.run_challenger("code_review", "contact person@example.com",
            data_classification="SANITIZED", env=enabled(),
            opener=lambda *a, **k: calls.append(True))
    assert not calls


class Response(io.BytesIO):
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


def fixture(content, finish="STOP"):
    return Response(json.dumps({"candidates":[{"finishReason":finish,"content":{
        "parts":[{"text":content}]}}]}).encode())


def test_authorized_manual_evidence_is_unverified():
    calls = []
    def opener(req, timeout):
        calls.append((req, timeout))
        return fixture(json.dumps({"summary":"fixture","findings":["review this"],
            "evidence_refs":["public:fixture"]}))
    result = worker.run_challenger("code_review", "public harmless fixture",
        data_classification="PUBLIC", env=enabled(), opener=opener)
    assert result["verification_state"] == "UNVERIFIED_CHALLENGER_OUTPUT"
    assert result["can_execute"] is False and result["probability_authority"] is False
    assert calls[0][1] == 15
    assert calls[0][0].get_method() == "POST"


@pytest.mark.parametrize("answer,finish", [
    ("not-json", "STOP"),
    (json.dumps({"summary":"partial"}), "STOP"),
    (json.dumps({"summary":"ok","findings":[],"evidence_refs":[]}), "MAX_TOKENS"),
    (json.dumps({"summary":"ok","findings":[],"evidence_refs":[],"qa_decision":"PASS"}), "STOP"),
])
def test_invalid_outputs_fail_closed(answer, finish):
    with pytest.raises(worker.GeminiWorkerError, match="GEMINI_RESPONSE_INVALID"):
        worker.run_challenger("code_review", "public fixture",
            data_classification="PUBLIC", env=enabled(),
            opener=lambda *a, **k: fixture(answer, finish))


def test_rate_limit_typed_hold():
    def opener(*args, **kwargs):
        raise HTTPError("https://example.invalid", 429, "limited", {}, None)
    with pytest.raises(worker.GeminiWorkerError, match="GEMINI_RATE_LIMITED"):
        worker.run_challenger("code_review", "public fixture",
            data_classification="PUBLIC", env=enabled(), opener=opener)


def test_registry_never_changes_scoring_golden_paths():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    registry = json.loads((root / "v17/ecosystem-registry.json").read_text())
    c = registry["components"]["GEMINI_STANDBY_CHALLENGER"]
    assert c["enabled_by_default"] is False
    assert all(c[k] is False for k in ("probability_authority",
        "terminal_authority", "safe_hold_authority", "can_execute"))
    assert not any("GEMINI" in x for route in registry["golden_paths"].values() for x in route)
