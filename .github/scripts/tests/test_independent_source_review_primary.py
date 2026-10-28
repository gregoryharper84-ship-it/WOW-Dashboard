"""Security regressions for typed read-only OpenAI QA primary transport."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import independent_source_review_fallback as fallback  # noqa: E402
import independent_source_review_primary as primary  # noqa: E402
import independent_source_review_receipt as receipts  # noqa: E402

HEAD = "a" * 40
FILE = {
    "filename": "src/fix.py", "previous_filename": None,
    "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b",
    "additions": 1, "deletions": 1,
}
DIGEST = receipts.canonical_diff([FILE], expected_count=1)[1]


def captured():
    return {"pr": {"head": {"sha": HEAD}}, "files": [FILE], "diff_sha256": DIGEST}


def verdict(decision="PASS"):
    return {
        "decision": decision,
        "summary": "Reviewed full exact-head source.",
        "reviewed_head_sha": HEAD, "reviewed_diff_sha256": DIGEST,
        "reviewed_files": ["src/fix.py"],
        "assessment": {"source_review": "PASS", "security_review": "PASS",
                       "probability_integrity_review": "PASS"},
        "findings": [],
    }


def model_response(result=None):
    return {"status": "completed", "output": [
        {"type": "message", "role": "assistant", "status": "completed",
         "content": [{"type": "output_text", "text": json.dumps(result or verdict())}]}
    ]}


def test_primary_is_real_readonly_structured_review(monkeypatch):
    calls = []
    def fake_post(url, headers, payload):
        calls.append((url, headers, payload))
        return model_response()
    monkeypatch.setattr(fallback, "_post_json", fake_post)
    output, proof = primary.execute_primary(captured(), key="stub-qa-only-key")
    assert output["decision"] == "PASS"
    assert proof == {"provider_identity": "openai-responses-api",
                     "credential_scope": "WOW_QA_REVIEW_OPENAI_API_KEY",
                     "can_execute": False}
    assert len(calls) == 1
    url, headers, payload = calls[0]
    assert url == "https://api.openai.com/v1/responses"
    assert headers["authorization"] == "Bearer stub-qa-only-key"
    assert payload["store"] is False
    assert payload["tools"] == []
    assert payload["text"]["format"]["strict"] is True
    schema = payload["text"]["format"]["schema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["assessment"]["additionalProperties"] is False
    assert schema["properties"]["findings"]["items"]["additionalProperties"] is False


@pytest.mark.parametrize("bad", [
    {"status": "incomplete", "output": []},
    {"status": "completed", "output": []},
    {"status": "completed", "output": [{"type": "message", "role": "assistant",
      "status": "completed", "content": [{"type": "refusal", "refusal": "Cannot review"}]}]},
    {"status": "completed", "output": [{"type": "message", "role": "assistant",
      "status": "completed", "content": [{"type": "output_text", "text": "bad json"}]}]},
])
def test_missing_refused_or_malformed_primary_never_passes(monkeypatch, bad):
    monkeypatch.setattr(fallback, "_post_json", lambda *args: bad)
    with pytest.raises(fallback.ReviewUnavailable):
        primary.execute_primary(captured(), key="stub")


@pytest.mark.parametrize("reason,expected", [
    ("REVIEW_PROVIDER_HTTP_429", True),
    ("REVIEW_PROVIDER_HTTP_401", True),
    ("REVIEW_PROVIDER_TRANSPORT_FAILURE", True),
    ("REVIEW_PROVIDER_HTTP_403", False),
    ("REVIEW_PROVIDER_HTTP_400", False),
    ("REVIEW_REPLY_SCHEMA_OR_PROVENANCE_INVALID", False),
    ("OPENAI_REVIEW_OUTPUT_INVALID", False),
    ("PRIMARY_CREDENTIAL_MISSING", True),
])
def test_only_authenticated_transport_or_missing_key_may_retry(reason, expected):
    receipt = primary._failure_receipt(reason, captured())
    assert primary.validate_failure_receipt(receipt, captured()) is expected
    assert receipt["review_approved"] is False and receipt["can_execute"] is False


def test_forged_sha_provider_and_boolean_flag_rejected():
    record = primary._failure_receipt("REVIEW_PROVIDER_HTTP_429", captured())
    for k, value in (("reviewed_head_sha", "b" * 40),
                     ("provider_identity", "engineering-app"),
                     ("fallback_eligible", False),
                     ("review_approved", True),
                     ("reviewed_diff_sha256", "e" * 64)):
        changed = {**record, k: value}
        with pytest.raises(fallback.ReviewUnavailable, match="PRIMARY_FAILURE_RECEIPT_MISMATCH"):
            primary.validate_failure_receipt(changed, captured())


def test_no_primary_key_is_never_fake_success():
    with pytest.raises(fallback.ReviewUnavailable, match="PRIMARY_CREDENTIAL_MISSING"):
        primary.execute_primary(captured(), key="")


def _coherent_reject():
    # A REJECT must carry evidence; the shared validator refuses a REJECT with
    # all-PASS assessments and no findings (MODEL_REVIEW_REJECTION_EVIDENCE_MISSING).
    v = verdict("REJECT")
    v["assessment"]["security_review"] = "FAIL"
    v["findings"] = [{"severity": "high", "file": "src/fix.py",
                      "detail": "Unsafe change.", "evidence": "line 1"}]
    return v


def test_review_reject_remains_terminal(monkeypatch):
    monkeypatch.setattr(fallback, "_post_json", lambda *args: model_response(_coherent_reject()))
    output, _ = primary.execute_primary(captured(), key="stub")
    assert output["decision"] == "REJECT"


def test_main_persists_typed_hold_without_result(monkeypatch, tmp_path):
    f = tmp_path / "input.json"
    # no network, exact trusted canonical captured patch
    f.write_text(json.dumps(captured()))
    failure = tmp_path / "failure.json"
    monkeypatch.setenv("OPENAI_REVIEW_KEY", "")
    assert primary.main(["--input", str(f), "--result", str(tmp_path / "pass.json"),
                         "--provenance", str(tmp_path / "proof.json"),
                         "--failure", str(failure)]) == 2
    row = json.loads(failure.read_text())
    assert row["fallback_eligible"] is True
    assert not (tmp_path / "pass.json").exists()
    assert primary.main(["--input", str(f), "--failure", str(failure),
                         "--verify-failure"]) == 0


def test_primary_attempt_receipt_is_sha_bound_and_never_claims_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "40786179429")
    path = tmp_path / "primary-attempt.json"
    primary._write_attempt(path, captured(), "REVIEW_PROVIDER_HTTP_429")
    receipt = json.loads(path.read_text())
    assert receipt["source_run_id"] == "40786179429"
    assert receipt["head_sha"] == HEAD
    assert receipt["diff_sha256"] == DIGEST
    assert receipt["provider"] == "openai-responses-api"
    assert receipt["role"] == "independent_security_review"
    assert receipt["retry_count"] == 0
    assert receipt["typed_result"] == "REVIEW_PROVIDER_HTTP_429"
    assert receipt["review_approved"] is False and receipt["can_execute"] is False
    assert "credential" not in str(receipt)


def test_incoherent_reject_is_typed_hold_never_pass_or_fallback(monkeypatch):
    # REJECT with all-PASS assessments and no findings is unusable output.
    # It must neither become a PASS nor be classified as fallback-eligible.
    monkeypatch.setattr(fallback, "_post_json", lambda *args: model_response(verdict("REJECT")))
    with pytest.raises(fallback.ReviewUnavailable,
                       match="REVIEW_REPLY_SCHEMA_OR_PROVENANCE_INVALID") as exc:
        primary.execute_primary(captured(), key="stub")
    assert fallback.is_retryable_provider_failure(exc.value) is False
