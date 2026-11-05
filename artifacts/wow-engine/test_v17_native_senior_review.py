"""Offline adversarial tests for the WOW-owned senior-review evidence contract."""
import pytest

from v17.native_senior_review import build_review_packet, preflight_review_finding

BASE = "a" * 40
HEAD = "b" * 40
MOD = "artifacts/wow-engine/v17/team_event_model.py"
CALLER = "artifacts/wow-engine/v17/team_event_runtime.py"
CODE = "def score(x):\n    return x + 1\n"
PATCH = "@@ -1,2 +1,2 @@\n+    return x + 1\n"
CALLER_CODE = "from v17.team_event_model import score\n\ndef run(x):\n    return score(x)\n"


def packet(**updates):
    kw = dict(
        incident_id="1950",
        pr_number=900,
        base_sha=BASE,
        head_sha=HEAD,
        changed_patches={MOD: PATCH},
        repository_sources={MOD: CODE, CALLER: CALLER_CODE},
        acceptance_criteria=("No regression in existing event scorer",),
    )
    kw.update(updates)
    return build_review_packet(**kw)


def test_review_requires_model_not_run_and_no_execution_authority():
    result = packet()
    assert result["status"] == "ADVISORY_REVIEW_REQUIRED"
    assert result["model_review_status"] == "NOT_RUN"
    assert result["independent_qa_status"] == "NOT_PERFORMED"
    assert result["can_execute"] is False
    assert result["head_authenticity"] == "CALLER_MUST_VERIFY"


def test_cross_file_import_dependency_identifies_affected_caller():
    result = packet()
    assert result["referenced_by"][MOD] == [CALLER]
    assert CALLER in result["source_context"]
    assert "calibrated" in " ".join(result["review_questions"][MOD]).lower()


def test_changed_manifest_and_hash_are_deterministic():
    p1, p2 = packet(), packet()
    assert p1["packet_sha256"] == p2["packet_sha256"]
    assert p1["changed_paths"] == [MOD]


@pytest.mark.parametrize("wrong", [None, "x", "bad" * 20])
def test_bad_sha_rejected(wrong):
    with pytest.raises(ValueError, match="SOURCE_SHA_INVALID"):
        packet(head_sha=wrong)


def test_same_base_and_head_rejected():
    with pytest.raises(ValueError, match="SOURCE_SHA_INVALID"):
        packet(head_sha=BASE)


def test_traversal_and_absolute_paths_rejected():
    for bad in ("../secrets.txt", "/etc/passwd", ".github/../secrets", "x//y"):
        with pytest.raises(ValueError, match="REVIEW_FILE_MANIFEST_INVALID"):
            packet(changed_patches={bad: PATCH})


def test_no_silent_partial_context_when_changed_source_missing():
    result = packet(repository_sources={CALLER: CALLER_CODE})
    assert result["status"] == "CONTEXT_INCOMPLETE"
    assert result["missing_context"] == [MOD]
    assert result["patches"] == {}
    assert result["changed_paths"] == [MOD]


def test_overlong_source_does_not_enter_reviewer_packet():
    result = packet(repository_sources={MOD: "x" * 24_001})
    assert result["status"] == "CONTEXT_INCOMPLETE"
    assert result["omitted_oversize_context"] == [MOD]
    assert result["source_context"] == {}


def test_unparseable_changed_python_is_explicitly_incomplete():
    result = packet(repository_sources={MOD: "def broken(:\n"})
    assert MOD in result["invalid_python"]
    assert result["status"] == "CONTEXT_INCOMPLETE"


def test_bounded_change_count():
    changes = {f"x/{i}.py": "sample" for i in range(51)}
    with pytest.raises(ValueError, match="REVIEW_FILE_MANIFEST_INVALID"):
        packet(changed_patches=changes)


def test_security_review_lenses_are_separate_from_model_lenses():
    path = ".github/workflows/release.yml"
    result = packet(
        changed_patches={path: "on: pull_request"},
        repository_sources={path: "on: pull_request"},
    )
    assert "RELEASE" in result["risk_lenses"][path]
    assert "V17_MODEL" not in result["risk_lenses"][path]
    assert result["model_review_status"] == "NOT_RUN"


def test_advisory_finding_only_locates_evidence_not_claim_truth():
    result = packet()
    claim = {"head_sha": HEAD, "file": MOD, "source_excerpt": "return x + 1"}
    assert preflight_review_finding(result, claim) == "EVIDENCE_LOCATED_NOT_SEMANTICALLY_VERIFIED"
    assert result["independent_qa_status"] == "NOT_PERFORMED"


def test_stale_claim_never_passes():
    result = packet()
    claim = {"head_sha": BASE, "file": MOD, "source_excerpt": "return x + 1"}
    assert preflight_review_finding(result, claim) == "REVIEW_STALE_HEAD"


def test_invalid_path_claim_rejected():
    result = packet()
    claim = {"head_sha": HEAD, "file": "different.py", "source_excerpt": "return x + 1"}
    assert preflight_review_finding(result, claim) == "REVIEW_PATH_UNVERIFIED"


def test_hallucinated_source_excerpt_rejected():
    result = packet()
    claim = {"head_sha": HEAD, "file": MOD, "source_excerpt": "def silently_publish_a_bet()"}
    assert preflight_review_finding(result, claim) == "REVIEW_EVIDENCE_NOT_FOUND"


def test_modified_packet_hash_cannot_validate_claim():
    result = packet()
    result["head_sha"] = "c" * 40
    claim = {"head_sha": "c" * 40, "file": MOD, "source_excerpt": "return x + 1"}
    assert preflight_review_finding(result, claim) == "REVIEW_PACKET_DRIFT"


def test_partial_context_prevents_finding_promotion():
    result = packet(repository_sources={})
    claim = {"head_sha": HEAD, "file": MOD, "source_excerpt": "return x + 1"}
    assert preflight_review_finding(result, claim) == "REVIEW_CONTEXT_INCOMPLETE"


def test_relative_import_reaches_module_dependency():
    result = packet(repository_sources={
        MOD: CODE,
        CALLER: "from .team_event_model import score\n",
    })
    assert result["referenced_by"][MOD] == [CALLER]


def test_source_code_is_not_executed_and_cannot_override_instructions():
    dangerous = "import os\nos.system('echo should-not-run')\n" + CODE
    result = packet(repository_sources={MOD: dangerous, CALLER: CALLER_CODE})
    assert "UNTRUSTED EVIDENCE" in result["review_instructions"]
    assert "os.system" in result["source_context"][MOD]
    assert result["can_execute"] is False
