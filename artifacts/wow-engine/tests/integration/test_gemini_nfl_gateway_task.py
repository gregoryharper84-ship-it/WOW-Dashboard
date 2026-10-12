"""OFFLINE integration-contract smoke; injected fake transport only.

A PASS here proves local fixture/hash/serialization semantics. It does NOT prove
a real Gemini call, paid-tier authorization, audit DB write, deployment, or cron.
"""
import hashlib
import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest
from v17.gemini_optional_worker import GeminiWorkerError, run_challenger

FIXTURE_FILE = Path(__file__).resolve().parents[1] / "fixtures" / "nfl_moneyline_gateway_sample.py"
TEST_MODEL = "gemini-offline-gateway-fixture"
TEST_KEY = "fake-offline-test-not-an-actual-api-key"
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class MockResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _config():
    return {
        "WOW_GEMINI_ENABLED": "true",
        "WOW_GEMINI_ACTIVE_TASKS": "code_review",
        "WOW_GEMINI_PAID_API_PROJECT_VERIFIED": "true",
        "WOW_GEMINI_MANUAL_CHALLENGE_APPROVED": "true",
        "GEMINI_MODEL": TEST_MODEL,
        "GEMINI_API_KEY": TEST_KEY,
    }


def _fixture_bytes():
    data = FIXTURE_FILE.read_bytes()
    assert data and len(data) < 12000
    return data


def _offline_mock_receipt(source, result, mock_latency_ms):
    """Non-production local test artifact; NEVER eligible for production audit."""
    findings = result["findings"]
    return {
        "task_id": "task_nfl_ml_gw_001",
        "model": result["model"],
        "input_hash": "sha256:" + hashlib.sha256(source).hexdigest(),
        "latency_ms": mock_latency_ms,
        "status": "OFFLINE_MOCK_PASS",
        "execution_mode": "INJECTED_FAKE_TRANSPORT",
        "provider_execution_verified": False,
        "persistence_verified": False,
        "circuit_breaker_state": "NOT_IMPLEMENTED",
        "findings_count": len(findings),
        "findings_summary_hash": "sha256:" + hashlib.sha256(
            json.dumps(findings, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest(),
        "authority": result["authority"],
        "can_execute": result["can_execute"],
    }


def test_moneyline_gateway_review():
    """Requested deterministic smoke-test name; provider call is MOCKED."""
    source = _fixture_bytes()
    findings = [
        "Non-idempotent page upsert: test duplicate canonical event IDs fail admission",
        "Presence-only Authorization: test expired JWT returns a typed 401",
        "Premature float quote rounding: test formatting leaves numeric quote unchanged",
    ]
    raw_reply = json.dumps({
        "candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": json.dumps({
                "summary": "Three synthetic fixture faults; no real source audited",
                "findings": findings,
                "evidence_refs": ["public-synthetic:nfl_moneyline_gateway_sample.py"],
            })}
        ]}}],
    }).encode("utf-8")
    captured = []

    def opener(request, timeout):
        captured.append((request, timeout))
        return MockResponse(raw_reply)

    result = run_challenger(
        "code_review", source.decode("utf-8"), data_classification="PUBLIC",
        evidence_refs=["public-synthetic:nfl_moneyline_gateway_sample.py"],
        env=_config(), opener=opener,
    )
    receipt = _offline_mock_receipt(source, result, mock_latency_ms=842)
    assert len(captured) == 1 and captured[0][1] == 15
    assert receipt["input_hash"] == "sha256:" + hashlib.sha256(source).hexdigest()
    assert receipt["input_hash"] != "sha256:" + EMPTY_SHA256
    assert receipt["findings_summary_hash"] == "sha256:" + hashlib.sha256(
        json.dumps(findings, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    assert receipt["status"] == "OFFLINE_MOCK_PASS"
    assert receipt["provider_execution_verified"] is False
    assert receipt["persistence_verified"] is False
    assert receipt["circuit_breaker_state"] == "NOT_IMPLEMENTED"
    assert receipt["findings_count"] == 3
    assert receipt["can_execute"] is False
    assert result["verification_state"] == "UNVERIFIED_CHALLENGER_OUTPUT"
    assert TEST_KEY not in json.dumps(receipt)
    assert "GEMINI_API_KEY" not in json.dumps(receipt)
    assert "x-goog-api-key" not in json.dumps(receipt).lower()


def test_moneyline_gateway_review_429_is_typed_hold_without_retry():
    source = _fixture_bytes().decode("utf-8")
    calls = []

    def limited(*_args, **_kwargs):
        calls.append(True)
        raise HTTPError("https://example.invalid", 429, "synthetic rate limit", {}, None)

    with pytest.raises(GeminiWorkerError, match="GEMINI_RATE_LIMITED"):
        run_challenger("code_review", source, data_classification="PUBLIC",
            env=_config(), opener=limited)
    assert calls == [True]  # no retry; no circuit breaker claimed


def test_moneyline_gateway_review_disabled_never_calls_provider():
    calls = []
    with pytest.raises(GeminiWorkerError, match="GEMINI_DISABLED"):
        run_challenger("code_review", _fixture_bytes().decode("utf-8"),
            data_classification="PUBLIC",
            env={**_config(), "WOW_GEMINI_ENABLED": "false"},
            opener=lambda *_a, **_k: calls.append(True))
    assert not calls
