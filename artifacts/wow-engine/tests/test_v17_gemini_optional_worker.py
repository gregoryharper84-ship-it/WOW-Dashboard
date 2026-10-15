"""Offline Gemini challenger tests; never call the real provider."""
import io
import json
from http.client import HTTPException
from urllib.error import HTTPError, URLError

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
        data_classification="PUBLIC", evidence_refs=["public:fixture"],
        env=enabled(), opener=opener)
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


@pytest.mark.parametrize("bad_response", [
    {"candidates": ["not-a-candidate-object"]},
    {"candidates": [{"finishReason": "STOP", "content": {"parts": [42]}}]},
    {"candidates": [{"finishReason": "STOP", "content": {"parts": ["invalid"]}}]},
])
def test_malformed_provider_shape_is_typed_hold(bad_response):
    with pytest.raises(worker.GeminiWorkerError, match="GEMINI_RESPONSE_INVALID"):
        worker.run_challenger(
            "code_review", "public fixture", data_classification="PUBLIC",
            env=enabled(), opener=lambda *a, **k: Response(json.dumps(bad_response).encode()),
        )


@pytest.mark.parametrize("error,expected", [
    (URLError("offline synthetic failure"), "GEMINI_TRANSPORT_FAILED"),
    (TimeoutError("offline synthetic timeout"), "GEMINI_TRANSPORT_FAILED"),
    (HTTPException("offline synthetic incomplete response"), "GEMINI_TRANSPORT_FAILED"),
    (HTTPError("https://example.invalid", 503, "unavailable", {}, None), "GEMINI_TRANSPORT_FAILED"),
])
def test_transport_error_is_typed_and_no_retry(error, expected):
    call_count = []
    def opener(*args, **kwargs):
        call_count.append(True)
        raise error
    with pytest.raises(worker.GeminiWorkerError, match=expected):
        worker.run_challenger("code_review", "public fixture",
            data_classification="PUBLIC", env=enabled(), opener=opener)
    assert len(call_count) == 1


@pytest.mark.parametrize("prompt,refs", [
    ("", None),
    ("x" * (worker.MAX_INPUT_CHARS + 1), None),
    (None, None),
    ("public", [None]),
    ("public", "not-list"),
    ("public", ["x" * 1025]),
    ("public", ["ok"] * 21),
])
def test_invalid_input_rejected_before_network(prompt, refs):
    calls = []
    with pytest.raises(worker.GeminiWorkerError, match="GEMINI_INPUT_INVALID"):
        worker.run_challenger("code_review", prompt,
            data_classification="PUBLIC", evidence_refs=refs,
            env=enabled(), opener=lambda *a, **k: calls.append(True))
    assert not calls


def test_oversized_response_is_typed_hold():
    with pytest.raises(worker.GeminiWorkerError, match="GEMINI_RESPONSE_INVALID"):
        worker.run_challenger("code_review", "public fixture",
            data_classification="PUBLIC", env=enabled(),
            opener=lambda *a, **k: Response(b"x" * (worker.MAX_RESPONSE_BYTES + 1)))


def test_cli_status_is_redacted_and_offline(monkeypatch, capsys):
    def network_forbidden(*args, **kwargs):
        raise AssertionError("unexpected live HTTP")
    monkeypatch.setattr(worker, "urlopen", network_forbidden)
    for name in ["WOW_GEMINI_ENABLED", "GEMINI_API_KEY", "GEMINI_MODEL"]:
        monkeypatch.delenv(name, raising=False)
    assert worker.main(["--status"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["configured"] is False
    assert printed["can_execute"] is False
    assert "api_key" not in printed


def test_cli_no_auth_is_typed_and_offline(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("WOW_GEMINI_ENABLED", "false")
    text_file = tmp_path / "public.txt"
    text_file.write_text("synthetic public fixture")
    assert worker.main(["--task", "code_review", "--input-file", str(text_file),
                        "--data-classification", "PUBLIC"]) == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed == {"status": "GEMINI_DISABLED", "can_execute": False}


@pytest.mark.parametrize("refs", [
    ["spoof:not-approved"],
    ["public:fixture", "public:fixture"],
    ["bad space"],
    ["bad|pipe"],
])
def test_model_reference_spoofing_or_invalid_ids_blocked(refs):
    answer = json.dumps({"summary": "reviewed", "findings": ["example finding"],
                         "evidence_refs": refs})
    with pytest.raises(worker.GeminiWorkerError, match="GEMINI_EVIDENCE_REF_INVALID"):
        worker.run_challenger(
            "code_review", "public test fixture", data_classification="PUBLIC",
            evidence_refs=["public:fixture"], env=enabled(),
            opener=lambda *a, **k: fixture(answer))


def test_model_findings_without_registered_evidence_blocked():
    answer = json.dumps({"summary": "reviewed", "findings": ["unjustified finding"],
                         "evidence_refs": []})
    with pytest.raises(worker.GeminiWorkerError, match="GEMINI_EVIDENCE_REF_INVALID"):
        worker.run_challenger(
            "code_review", "public test fixture", data_classification="PUBLIC",
            evidence_refs=["public:fixture"], env=enabled(),
            opener=lambda *a, **k: fixture(answer))


def test_authorized_multi_source_references_pass_but_remain_unverified():
    approved = ["public:fixture", "KALSHI_RULE:case-001"]
    answer = json.dumps({"summary": "reviewed", "findings": ["needs confirmation"],
                         "evidence_refs": approved})
    seen = []
    def opener(req, timeout):
        seen.append(req)
        return fixture(answer)
    result = worker.run_challenger(
        "code_review", "public test fixture", data_classification="PUBLIC",
        evidence_refs=approved, env=enabled(), opener=opener)
    assert result["evidence_refs"] == approved
    assert result["verification_state"] == "UNVERIFIED_CHALLENGER_OUTPUT"
    assert result["can_execute"] is False
    assert len(seen) == 1
    payload = json.loads(seen[0].data)
    instruction = payload["contents"][0]["parts"][0]["text"]
    assert "REGISTERED_EVIDENCE_IDS" in instruction
    assert all(ref in instruction for ref in approved)


@pytest.mark.parametrize("registered", [
    ["public:fixture", "public:fixture"],
    ["contains white space"],
    [""],
    ["x" * 201],
    ["a|pipe"],
])
def test_invalid_registered_refs_blocked_before_http(registered):
    sent = []
    with pytest.raises(worker.GeminiWorkerError, match="GEMINI_INPUT_INVALID"):
        worker.run_challenger(
            "code_review", "public test fixture", data_classification="PUBLIC",
            evidence_refs=registered, env=enabled(),
            opener=lambda *a, **k: sent.append(True))
    assert not sent


def test_entire_serialized_body_is_bounded_before_http():
    sent = []
    with pytest.raises(worker.GeminiWorkerError, match="GEMINI_INPUT_INVALID"):
        worker.run_challenger(
            "code_review", "x" * (worker.MAX_INPUT_CHARS - 400),
            data_classification="PUBLIC", evidence_refs=["public:fixture"],
            env=enabled(), opener=lambda *a, **k: sent.append(True))
    assert not sent


def test_unicode_json_expansion_is_bounded_before_http():
    sent = []
    with pytest.raises(worker.GeminiWorkerError, match="GEMINI_INPUT_INVALID"):
        worker.run_challenger(
            "code_review", "\u2603" * 2100,
            data_classification="PUBLIC", evidence_refs=["public:fixture"],
            env=enabled(), opener=lambda *a, **k: sent.append(True))
    assert not sent


def test_valid_complete_request_size_does_not_exceed_limit():
    def opener(req, timeout):
        assert len(req.data) <= worker.MAX_INPUT_CHARS
        return fixture(json.dumps({"summary": "no findings", "findings": [],
                                   "evidence_refs": []}))
    result = worker.run_challenger(
        "code_review", "public test fixture", data_classification="PUBLIC",
        evidence_refs=[], env=enabled(), opener=opener)
    assert result["can_execute"] is False
