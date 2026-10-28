"""Offline regression coverage for non-fabricating independent QA provider fallback."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parents[1]
sys.path.insert(0, str(SCRIPTS))
import independent_source_review_receipt as receipts  # noqa: E402
import independent_source_review_fallback as fallback  # noqa: E402

HEAD = "a" * 40
FILE = {
    "filename": "src/fix.py",
    "previous_filename": None,
    "status": "modified",
    "patch": "@@ -1 +1 @@\n-a\n+b",
    "additions": 1, "deletions": 1,
}
DIGEST = receipts.canonical_diff([FILE], expected_count=1)[1]


def captured():
    return {
        "pr": {"head": {"sha": HEAD}},
        "files": [FILE],
        "diff_sha256": DIGEST,
    }


def verdict(*, decision="PASS", head=HEAD):
    rejected = decision == "REJECT"
    return json.dumps({
        "decision": decision,
        "summary": "Verified every changed source path.",
        "reviewed_head_sha": head,
        "reviewed_diff_sha256": DIGEST,
        "reviewed_files": ["src/fix.py"],
        "assessment": {
            "source_review": "PASS",
            "security_review": "FAIL" if rejected else "PASS",
            "probability_integrity_review": "PASS",
        },
        "findings": ([{"severity": "high", "file": "src/fix.py",
                      "detail": "Unsafe change.", "evidence": "Observed in diff."}]
                     if rejected else []),
    })


def test_first_provider_quota_failure_uses_genuine_gemini_verdict(monkeypatch):
    seen = []
    def anthropic(*args):
        seen.append("anthropic")
        raise fallback.ReviewUnavailable("REVIEW_PROVIDER_HTTP_429")
    def gemini(*args):
        seen.append("gemini")
        return verdict()
    monkeypatch.setattr(fallback, "_anthropic", anthropic)
    monkeypatch.setattr(fallback, "_gemini", gemini)
    result, proof = fallback.review_fallback(
        captured(),
        anthropic_key="test", anthropic_scope="ANTHROPIC_API_KEY",
        gemini_key="test", gemini_scope="GEMINI_API_KEY",
    )
    assert seen == ["anthropic", "gemini"]
    assert result["decision"] == "PASS"
    assert proof == {"provider_identity": "gemini-generative-api",
                     "credential_scope": "GEMINI_API_KEY", "can_execute": False}


def test_real_reject_never_fails_over_to_more_permissive_reviewer(monkeypatch):
    monkeypatch.setattr(fallback, "_anthropic", lambda *args: verdict(decision="REJECT"))
    monkeypatch.setattr(
        fallback, "_gemini",
        lambda *args: pytest.fail("Must not seek a different model's PASS"),
    )
    result, proof = fallback.review_fallback(
        captured(), anthropic_key="test",
        anthropic_scope="WOW_QA_REVIEW_ANTHROPIC_API_KEY",
        gemini_key="test", gemini_scope="WOW_QA_REVIEW_GEMINI_API_KEY",
    )
    assert result["decision"] == "REJECT"
    assert proof["provider_identity"] == "anthropic-claude-api"


def test_false_head_cannot_be_accepted_as_review_of_current_source(monkeypatch):
    monkeypatch.setattr(fallback, "_anthropic", lambda *args: verdict(head="b" * 40))
    monkeypatch.setattr(fallback, "_gemini", lambda *args: verdict())
    result, proof = fallback.review_fallback(
        captured(), anthropic_key="test", anthropic_scope="ANTHROPIC_API_KEY",
        gemini_key="test", gemini_scope="GEMINI_API_KEY",
    )
    assert result["reviewed_head_sha"] == HEAD
    assert proof["provider_identity"] == "gemini-generative-api"


def test_no_credentials_cannot_produce_fabricated_pass():
    with pytest.raises(fallback.ReviewUnavailable, match="REVIEW_FALLBACK_CREDENTIALS_MISSING"):
        fallback.review_fallback(captured())


def test_no_provider_success_fails_closed(monkeypatch):
    def unavailable(*args):
        raise fallback.ReviewUnavailable("REVIEW_PROVIDER_HTTP_503")
    monkeypatch.setattr(fallback, "_anthropic", unavailable)
    monkeypatch.setattr(fallback, "_gemini", unavailable)
    with pytest.raises(fallback.ReviewUnavailable, match="REVIEW_ALL_PROVIDERS_UNAVAILABLE"):
        fallback.review_fallback(
            captured(), anthropic_key="test", anthropic_scope="ANTHROPIC_API_KEY",
            gemini_key="test", gemini_scope="GEMINI_API_KEY",
        )


def test_credential_scope_cannot_claim_other_provider(monkeypatch):
    monkeypatch.setattr(
        fallback, "_anthropic", lambda *args: pytest.fail("Invalid scope must hold"),
    )
    with pytest.raises(fallback.ReviewUnavailable, match="REVIEW_CREDENTIAL_SCOPE_UNTRUSTED"):
        fallback.review_fallback(
            captured(), anthropic_key="test",
            anthropic_scope="WOW_QA_REVIEW_GEMINI_API_KEY",
        )


def test_reject_schema_validation_guards_missing_evidence():
    bad = json.loads(verdict())
    del bad["reviewed_files"]
    with pytest.raises(fallback.ReviewUnavailable, match="REVIEW_REPLY_SCHEMA_OR_PROVENANCE_INVALID"):
        fallback._validate_reply(json.dumps(bad), captured())


def test_independent_qa_workflow_never_skips_provenance_or_security_on_fallback():
    wf = yaml.safe_load(
        (ROOT / ".github/workflows/wow-v17-independent-qa-check.yml").read_text()
    )
    steps = wf["jobs"]["qa"]["steps"]
    by_name = {s["name"]: s for s in steps}
    primary = by_name["Run isolated read-only adversarial review"]
    secondary = by_name["Run authenticated independent reviewer fallback"]
    selection = by_name["Select authentic reviewer verdict and credential provenance"]
    build = by_name["Build versioned exact-head review artifact"]
    assert primary["continue-on-error"] is True
    assert "steps.review.outcome != 'success'" in secondary["if"]
    assert "steps.review.outputs.structured_output == ''" in secondary["if"]
    assert "independent_source_review_fallback.py" in secondary["run"]
    assert "SOURCE_REVIEW_FALLBACK" not in secondary["run"]  # no separate authority
    assert "elif [ \"$FALLBACK_OUTCOME\" = success ]" in selection["run"]
    assert "QA_INDEPENDENT_REVIEW_ALL_PROVIDERS_FAILED" in selection["run"]
    assert "steps.reviewer_selection.outputs.provider_identity" in build["env"]["PROVIDER_IDENTITY"]
    assert "--provider-identity \"$PROVIDER_IDENTITY\"" in build["run"]
    assert "--credential-scope \"$CREDENTIAL_SCOPE\"" in build["run"]
    assert "independent_source_review_receipt.py" in build["run"]
    assert wf["jobs"]["qa"]["environment"] == "wow-qa"



def test_captured_source_digest_must_match_complete_authenticated_patch(tmp_path):
    source = captured()
    source["diff_sha256"] = "f" * 64
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(fallback.ReviewUnavailable, match="REVIEW_INPUT_DIFF_MISMATCH"):
        fallback._read_input(path)



def test_claude_native_json_schema_is_validated_after_real_transport(monkeypatch):
    seen = []
    def fake_post(url, headers, payload):
        seen.append((url, headers, payload))
        return {"content": [{"type": "text", "text": verdict()}],
                "stop_reason": "end_turn"}
    monkeypatch.setattr(fallback, "_post_json", fake_post)
    response = fallback._anthropic(fallback._prompt(captured()), "test-credential")
    assert json.loads(response)["decision"] == "PASS"
    assert len(seen) == 1
    _, headers, payload = seen[0]
    assert headers["x-api-key"] == "test-credential"
    assert payload["output_config"]["format"]["type"] == "json_schema"
    schema = payload["output_config"]["format"]["schema"]
    assert "reviewed_head_sha" in schema["required"]
    assert "reviewed_diff_sha256" in schema["required"]
    assert "findings" in schema["required"]
    assert schema["additionalProperties"] is False
