from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
from urllib.error import URLError
from io import BytesIO
from zipfile import ZipFile

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "independent_source_review_receipt.py"
WORKFLOW = Path(__file__).resolve().parents[2] / "workflows" / "wow-v17-independent-qa-check.yml"
spec = importlib.util.spec_from_file_location("independent_source_review_receipt", SCRIPT)
review = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = review
spec.loader.exec_module(review)

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
HEAD = "a" * 40
MAIN = "b" * 40
DIFF = [{
    "filename": "src/fix.py", "previous_filename": None, "status": "modified",
    "patch": "@@ -1 +1 @@\n-a\n+b", "additions": 1, "deletions": 1,
}]
COMMIT_METADATA = {
    "sha": HEAD,
    "commit": {"tree": {"sha": "d" * 40}, "message": "Fix the narrow source issue"},
    "parents": [{"sha": "e" * 40}],
    "author": {"login": "copilot-swe-agent"},
    "committer": {"login": "copilot-swe-agent"},
}
RESULT = {
    "decision": "PASS",
    "summary": "Reviewed the complete diff and found no blocking issue.",
    "reviewed_head_sha": HEAD,
    "reviewed_diff_sha256": review.canonical_diff(DIFF, expected_count=1)[1],
    "reviewed_files": ["src/fix.py"],
    "assessment": {
        "source_review": "PASS",
        "security_review": "PASS",
        "probability_integrity_review": "PASS",
    },
    "findings": [],
}
PRODUCER_RUN = {
    "id": 12345,
    "run_attempt": 1,
    "name": review.WORKFLOW_NAME,
    "workflow_id": review.WORKFLOW_ID,
    "path": review.WORKFLOW_PATH,
    "event": "workflow_dispatch",
    "head_branch": "main",
    "repository": review.CANONICAL_REPOSITORY,
    "head_sha": MAIN,
}
CONSUMER_RUN = {
    **PRODUCER_RUN,
    "name": f"{review.WORKFLOW_NAME} event=workflow_dispatch pr=1822",
    "workflow_id": review.WORKFLOW_ID,
    "repository": {"full_name": review.CANONICAL_REPOSITORY},
    "head_repository": {"full_name": review.CANONICAL_REPOSITORY},
    "status": "completed",
    "conclusion": "success",
}


def pr():
    return {
        "number": 1822,
        "state": "open",
        "draft": False,
        "merged_at": None,
        "changed_files": 1,
        "user": {"id": 123, "login": "copilot-swe-agent"},
        "head": {
            "sha": HEAD,
            "ref": "copilot/fix",
            "repo": {"full_name": review.CANONICAL_REPOSITORY},
        },
        "base": {"ref": "main", "repo": {"full_name": review.CANONICAL_REPOSITORY}},
    }


def artifact(*, now=NOW, **overrides):
    value = review.build_artifact(
        repository=review.CANONICAL_REPOSITORY,
        pr=pr(),
        files=DIFF,
        commit=COMMIT_METADATA,
        review_result=RESULT,
        run=PRODUCER_RUN,
        now=now,
    )
    value.update(overrides)
    return value


@pytest.mark.parametrize(
    ("patch", "additions", "deletions", "reason"),
    [
        ("@@ -1,2 +1,2 @@\n-old\n+new", 1, 1, "PATCH_HUNK_INCOMPLETE"),
        ("@@ -1 +1 @@\n-old\n+new\n+omitted", 2, 1, "PATCH_HUNK_OVERFLOW"),
        ("@@ -1 +1 @@\n-old\n+new", 2, 1, "PATCH_COUNTS_MISMATCH"),
        ("@@ -1 +1 @@\n-old", 0, 1, "PATCH_HUNK_INCOMPLETE"),
        ("@@ -1 +1 @@\n-old\n+new\n", 1, 0, "PATCH_COUNTS_MISMATCH"),
        ("diff --git a/src/fix.py b/src/fix.py\n@@ -1 +1 @@\n-old\n+new", 1, 1, "PATCH_HUNK_INVALID"),
    ],
)
def test_patch_hunks_must_match_complete_api_change_counts(patch, additions, deletions, reason):
    file = {**DIFF[0], "patch": patch, "additions": additions, "deletions": deletions}
    with pytest.raises(review.SourceReviewError, match=reason):
        review.canonical_diff([file], expected_count=1)


def test_capture_normalized_commit_metadata_is_accepted_by_build_cli(tmp_path):
    captured = {
        "pr": pr(),
        "files": DIFF,
        "commit": review._commit_metadata(COMMIT_METADATA, expected_head_sha=HEAD),
        "diff_sha256": review.canonical_diff(DIFF, expected_count=1)[1],
    }
    input_path = tmp_path / "captured.json"
    result_path = tmp_path / "result.json"
    run_path = tmp_path / "run.json"
    output_path = tmp_path / "receipt.json"
    input_path.write_text(json.dumps(captured), encoding="utf-8")
    result_path.write_text(json.dumps(RESULT), encoding="utf-8")
    run_path.write_text(json.dumps(PRODUCER_RUN), encoding="utf-8")

    assert review.main([
        "--build", "--input", str(input_path), "--result", str(result_path),
        "--run", str(run_path), "--output", str(output_path),
        "--repository", review.CANONICAL_REPOSITORY, "--pr-number", "1822",
        "--head-sha", HEAD, "--head-ref", "copilot/fix",
    ]) == 0
    receipt = json.loads(output_path.read_text(encoding="utf-8"))
    assert receipt["commit"]["tree_sha"] == "d" * 40
    assert receipt["review"]["decision"] == "PASS"


def test_capture_build_and_local_qa_decision_use_live_producer_context(monkeypatch, tmp_path):
    responses = [
        pr(),
        [{"filename": "src/fix.py", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b",
          "additions": 1, "deletions": 1}],
        COMMIT_METADATA,
        pr(),
    ]

    class Response:
        def __init__(self, payload):
            self.payload = json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _limit):
            return self.payload

    monkeypatch.setattr(review, "urlopen", lambda *_args, **_kwargs: Response(responses.pop(0)))
    captured = review.capture_pr_evidence(
        repository=review.CANONICAL_REPOSITORY,
        pr_number=1822,
        expected_head_sha=HEAD,
        token="test-read-only-token",
    )
    input_path = tmp_path / "captured.json"
    result_path = tmp_path / "review.json"
    run_path = tmp_path / "producer-run.json"
    receipt_path = tmp_path / "receipt.json"
    input_path.write_text(json.dumps(captured), encoding="utf-8")
    result_path.write_text(json.dumps({
        **RESULT,
        "reviewed_diff_sha256": captured["diff_sha256"],
        "reviewed_files": ["src/fix.py"],
    }), encoding="utf-8")
    run_path.write_text(json.dumps(PRODUCER_RUN), encoding="utf-8")

    assert review.main([
        "--build", "--input", str(input_path), "--result", str(result_path),
        "--run", str(run_path), "--output", str(receipt_path),
        "--repository", review.CANONICAL_REPOSITORY, "--pr-number", "1822",
        "--head-sha", HEAD, "--head-ref", "copilot/fix",
    ]) == 0
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    qa_path = SCRIPT.parents[2] / "artifacts" / "wow-engine" / "v17" / "independent_release_checks.py"
    qa_spec = importlib.util.spec_from_file_location("independent_release_checks_capture_test", qa_path)
    qa = importlib.util.module_from_spec(qa_spec)
    assert qa_spec and qa_spec.loader
    qa_spec.loader.exec_module(qa)
    evidence = {
        "repository": review.CANONICAL_REPOSITORY,
        "pr_number": 1822,
        "expected_head_sha": HEAD,
        "evidence_errors": [],
        "owner_login": "repository-owner",
        "pr": {
            "state": "open", "draft": False, "merged": False, "head_sha": HEAD,
            "head_ref": "copilot/fix", "head_repo": review.CANONICAL_REPOSITORY,
            "base_ref": "main", "author": "copilot-swe-agent", "author_id": 123,
        },
        "files": ["src/fix.py"],
        "source_review_paths": ["src/fix.py"],
        "source_review": {
            "artifact_id": 77, "artifact": receipt, "run": PRODUCER_RUN,
            "producer_context": True,
        },
        "qa_review_policy": {
            "mode": "independent",
            "target": {"pr_number": 1822, "head_sha": HEAD, "head_ref": "copilot/fix"},
            "canary_scope": None,
        },
        "independent_review_active": True,
        "current_review_active": False,
        "check_runs": [
            {"id": index, "name": name, "head_sha": HEAD, "status": "completed",
             "conclusion": "success", "app_id": 15368, "completed_at": "2026-10-20T12:00:00Z"}
            for index, name in enumerate(qa.QA_EVIDENCE_CHECKS, start=1)
        ],
        "reviews": [],
    }

    assert qa.decision("qa", qa.qa_findings(evidence), evidence)["conclusion"] == "success"
    evidence["source_review"].pop("producer_context")
    assert any(
        finding.startswith("QA_INDEPENDENT_SOURCE_REVIEW_INVALID:")
        for finding in qa.qa_findings(evidence)
    )


def verify(value, *, run=None, now=NOW, head=HEAD, attempt=1):
    return review.verify_artifact(
        value,
        run=CONSUMER_RUN if run is None else run,
        expected_repository=review.CANONICAL_REPOSITORY,
        expected_pr_number=1822,
        expected_head_sha=head,
        expected_head_ref="copilot/fix",
        expected_attempt=attempt,
        now=now,
    )


def test_source_review_artifact_binds_exact_head_complete_diff_and_trusted_run():
    receipt = artifact()

    verify(receipt)

    assert receipt["schema_version"] == 1
    assert receipt["review"]["decision"] == "PASS"
    assert receipt["diff"]["sha256"] == RESULT["reviewed_diff_sha256"]
    assert receipt["source"]["provider_identity"] == "openai-codex-action"
    assert receipt["source"]["credential_scope"] == "WOW_QA_REVIEW_OPENAI_API_KEY"


def test_workflow_run_triggered_review_keeps_protected_main_provenance():
    workflow_run = {**PRODUCER_RUN, "event": "workflow_run"}
    receipt = review.build_artifact(
        repository=review.CANONICAL_REPOSITORY,
        pr=pr(),
        files=DIFF,
        commit=COMMIT_METADATA,
        review_result=RESULT,
        run=workflow_run,
        now=NOW,
    )

    verify(receipt, run={
        **CONSUMER_RUN,
        "name": f"{review.WORKFLOW_NAME} event=workflow_run pr=AUTO",
        "event": "workflow_run",
    })

    with pytest.raises(review.SourceReviewError, match="PROVENANCE_MISMATCH|WORKFLOW_RUN_UNTRUSTED"):
        verify(receipt, run={**CONSUMER_RUN, "event": "pull_request"})


def test_current_producer_context_is_validated_without_weakening_completed_run_authentication():
    receipt = artifact()
    review.verify_artifact(
        receipt,
        run=PRODUCER_RUN,
        expected_repository=review.CANONICAL_REPOSITORY,
        expected_pr_number=1822,
        expected_head_sha=HEAD,
        expected_head_ref="copilot/fix",
        producer_context=True,
        now=NOW,
    )
    with pytest.raises(review.SourceReviewError, match="SOURCE_REVIEW_WORKFLOW_RUN_UNTRUSTED"):
        verify(receipt, run=PRODUCER_RUN)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda value: value["target"].update(head_sha="c" * 40), "HEAD_MISMATCH"),
        (lambda value: value["target"].update(head_ref="other"), "HEAD_REF_MISMATCH"),
        (lambda value: value["target"].update(pr_number=999), "PR_NUMBER_MISMATCH"),
        (lambda value: value["source"].update(run_id="999"), "RUN_ID_MISMATCH"),
        (lambda value: value["source"].update(run_attempt=2), "RUN_ATTEMPT_MISMATCH"),
        (lambda value: value["source"].update(provider_identity="owner-claim"), "PROVENANCE_MISMATCH"),
        (lambda value: value["source"].update(workflow_path=".github/workflows/other.yml"), "PROVENANCE_MISMATCH"),
        (lambda value: value["review"].update(decision="REJECT"), "REJECTION_EVIDENCE_MISSING"),
        (lambda value: value["diff"].update(sha256="not-a-digest"), "DIGEST_INVALID"),
        (lambda value: value["review"].update(reviewed_files=[]), "FILE_COVERAGE_INCOMPLETE"),
    ],
)
def test_source_review_artifact_mismatches_hold(mutation, reason):
    receipt = artifact()
    mutation(receipt)

    with pytest.raises(review.SourceReviewError, match=reason):
        verify(receipt)


def test_foreign_or_failed_workflow_run_holds():
    for field, value in [
        ("event", "pull_request_target"),
        ("head_branch", "attacker"),
        ("path", ".github/workflows/untrusted.yml"),
        ("workflow_id", review.WORKFLOW_ID + 1),
        ("workflow_id", None),
        ("workflow_id", True),
        ("status", "in_progress"),
        ("conclusion", "failure"),
    ]:
        run = dict(CONSUMER_RUN)
        run[field] = value
        with pytest.raises(review.SourceReviewError):
            verify(artifact(), run=run)

    run = {**CONSUMER_RUN, "head_repository": {"full_name": "fork/WOW-Dashboard"}}
    with pytest.raises(review.SourceReviewError, match="WORKFLOW_RUN_UNTRUSTED"):
        verify(artifact(), run=run)


def test_protected_main_source_checkout_drift_cannot_match_receipt():
    receipt = artifact()
    changed_main = {**CONSUMER_RUN, "head_sha": "c" * 40}
    with pytest.raises(review.SourceReviewError, match="PROVENANCE_MISMATCH"):
        verify(receipt, run=changed_main)


def test_expired_and_future_source_reviews_hold():
    with pytest.raises(review.SourceReviewError, match="EXPIRED_OR_FUTURE"):
        verify(artifact(), now=NOW + timedelta(minutes=30))
    with pytest.raises(review.SourceReviewError, match="EXPIRED_OR_FUTURE"):
        verify(artifact(), now=NOW - timedelta(seconds=1))


def test_replayed_or_duplicate_artifact_inventory_holds():
    name = f"wow-v17-independent-source-review-1822-{HEAD}-12345-1"
    single = [{"id": 77, "name": name, "expired": False, "created_at": NOW.isoformat()}]
    review.verify_unique_artifact_inventory(
        single, prefix=f"wow-v17-independent-source-review-1822-{HEAD}-", expected_artifact_id=77, now=NOW
    )
    with pytest.raises(review.SourceReviewError, match="ARTIFACT_DUPLICATE_OR_MISSING"):
        review.verify_unique_artifact_inventory(
            single + [{**single[0], "id": 78}],
            prefix=f"wow-v17-independent-source-review-1822-{HEAD}-",
            expected_artifact_id=77,
            now=NOW,
        )
    with pytest.raises(review.SourceReviewError, match="ARTIFACT_REPLAYED"):
        review.verify_unique_artifact_inventory(
            single, prefix=f"wow-v17-independent-source-review-1822-{HEAD}-",
            expected_artifact_id=78, now=NOW,
        )
    with pytest.raises(review.SourceReviewError, match="ARTIFACT_DUPLICATE_OR_MISSING"):
        review.verify_unique_artifact_inventory(
            [{"id": 77, "name": name, "expired": True, "created_at": NOW.isoformat()}],
            prefix=f"wow-v17-independent-source-review-1822-{HEAD}-",
            expected_artifact_id=77,
            now=NOW,
        )
    review.verify_unique_artifact_inventory(
        single + [{"id": 78, "name": name + "-old", "expired": False,
                   "created_at": (NOW - timedelta(minutes=30)).isoformat()}],
        prefix=f"wow-v17-independent-source-review-1822-{HEAD}-",
        expected_artifact_id=77,
        now=NOW,
    )


def test_current_review_resolution_verifies_zip_run_and_unique_fresh_artifact():
    receipt = artifact(now=NOW)
    artifact_bytes = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode()
    checksum = review.hashlib.sha256(artifact_bytes).hexdigest()
    archive = BytesIO()
    with ZipFile(archive, "w") as zip_file:
        zip_file.writestr("wow-v17-independent-source-review.json", artifact_bytes)
        zip_file.writestr("wow-v17-independent-source-review.json.sha256", checksum + "\n")
    name = f"wow-v17-independent-source-review-1822-{HEAD}-12345-1"
    record = {"id": 77, "name": name, "expired": False, "created_at": NOW.isoformat()}
    metadata = {
        "id": 77,
        "name": name,
        "expired": False,
        "workflow_run": {"id": 12345},
    }
    routes = {
        "/actions/artifacts?per_page=100&page=1": {"artifacts": [record]},
        "/actions/artifacts/77": metadata,
        "/actions/runs/12345": CONSUMER_RUN,
    }

    resolved = review.resolve_current_source_review(
        repository=review.CANONICAL_REPOSITORY,
        pr_number=1822,
        head_sha=HEAD,
        head_ref="copilot/fix",
        token="read-only",
        now=NOW,
        get_json=routes.__getitem__,
        download_artifact=lambda artifact_id: archive.getvalue() if artifact_id == 77 else b"",
    )
    assert resolved["artifact_id"] == 77
    assert resolved["artifact"] == receipt
    assert resolved["run"] == CONSUMER_RUN

    routes["/actions/artifacts?per_page=100&page=1"] = {
        "artifacts": [record, {**record, "id": 78}]
    }
    with pytest.raises(review.SourceReviewError, match="ARTIFACT_DUPLICATE_OR_MISSING"):
        review.resolve_current_source_review(
            repository=review.CANONICAL_REPOSITORY,
            pr_number=1822,
            head_sha=HEAD,
            head_ref="copilot/fix",
            token="read-only",
            now=NOW,
            get_json=routes.__getitem__,
            download_artifact=lambda artifact_id: archive.getvalue(),
        )


def test_review_resolution_ignores_prior_distinct_run_artifacts():
    latest = review.build_artifact(
        repository=review.CANONICAL_REPOSITORY,
        pr=pr(),
        files=DIFF,
        commit=COMMIT_METADATA,
        review_result=RESULT,
        run={**PRODUCER_RUN, "id": 12346},
        now=NOW,
    )
    payload = (json.dumps(latest, sort_keys=True, indent=2) + "\n").encode()
    archive = BytesIO()
    with ZipFile(archive, "w") as zip_file:
        zip_file.writestr("wow-v17-independent-source-review.json", payload)
        zip_file.writestr(
            "wow-v17-independent-source-review.json.sha256",
            review.hashlib.sha256(payload).hexdigest() + "\n",
        )
    records = [
        {"id": 77, "name": f"wow-v17-independent-source-review-1822-{HEAD}-12345-1",
         "expired": False, "created_at": (NOW - timedelta(minutes=2)).isoformat()},
        {"id": 78, "name": f"wow-v17-independent-source-review-1822-{HEAD}-12346-1",
         "expired": False, "created_at": NOW.isoformat()},
    ]
    run = {**CONSUMER_RUN, "id": 12346}
    routes = {
        "/actions/artifacts?per_page=100&page=1": {"artifacts": records},
        "/actions/artifacts/78": {
            "id": 78, "name": records[1]["name"], "expired": False,
            "workflow_run": {"id": 12346},
        },
        "/actions/runs/12346": run,
    }

    resolved = review.resolve_current_source_review(
        repository=review.CANONICAL_REPOSITORY,
        pr_number=1822,
        head_sha=HEAD,
        head_ref="copilot/fix",
        token="read-only",
        now=NOW,
        get_json=routes.__getitem__,
        download_artifact=lambda artifact_id: archive.getvalue() if artifact_id == 78 else b"",
    )

    assert resolved["artifact_id"] == 78
    assert resolved["artifact"] == latest


def test_review_requires_complete_untruncated_diff_and_exact_file_coverage():
    with pytest.raises(review.SourceReviewError, match="PATCH_MISSING"):
        review.canonical_diff(
            [{
                "filename": "src/fix.py", "previous_filename": None, "status": "modified",
                "patch": None, "additions": 1, "deletions": 1,
            }],
            expected_count=1,
        )
    with pytest.raises(review.SourceReviewError, match="DIFF_INCOMPLETE"):
        review.canonical_diff([], expected_count=1)
    with pytest.raises(review.SourceReviewError, match="DIFF_LIMIT_EXCEEDED"):
        review.canonical_diff(DIFF, expected_count=review.MAX_DIFF_FILES + 1)
    bad_result = {**RESULT, "reviewed_files": ["not-the-reviewed-file"]}
    with pytest.raises(review.SourceReviewError, match="FILE_COVERAGE_INCOMPLETE"):
        review.build_artifact(
            repository=review.CANONICAL_REPOSITORY,
            pr=pr(),
            files=DIFF,
            commit=COMMIT_METADATA,
            review_result=bad_result,
            run=PRODUCER_RUN,
            now=NOW,
        )


def test_critical_finding_requires_reject():
    result = {
        **RESULT,
        "findings": [{
            "severity": "high", "file": "src/fix.py",
            "detail": "Potential blocker.", "evidence": "Observed in diff.",
        }],
    }
    with pytest.raises(review.SourceReviewError, match="HIGH_FINDING_NOT_REJECTED"):
        review.build_artifact(
            repository=review.CANONICAL_REPOSITORY,
            pr=pr(),
            files=DIFF,
            commit=COMMIT_METADATA,
            review_result=result,
            run=PRODUCER_RUN,
            now=NOW,
        )


def test_authenticated_rejection_receipt_remains_verifiable_for_downstream_failure():
    rejected = {
        **RESULT,
        "decision": "REJECT",
        "summary": "The security assessment found a blocker.",
        "assessment": {
            "source_review": "PASS",
            "security_review": "FAIL",
            "probability_integrity_review": "PASS",
        },
        "findings": [{
            "severity": "high", "file": "src/fix.py",
            "detail": "Security invariant weakened.", "evidence": "The patch disables a check.",
        }],
    }
    result = review.build_artifact(
        repository=review.CANONICAL_REPOSITORY,
        pr=pr(),
        files=DIFF,
        commit=COMMIT_METADATA,
        review_result=rejected,
        run=PRODUCER_RUN,
        now=NOW,
    )
    assert result["review"]["decision"] == "REJECT"
    verify(result)


def test_artifact_checksum_is_verified_by_cli(tmp_path):
    decision = artifact(now=datetime.now(timezone.utc))
    path = tmp_path / "decision.json"
    review.write_artifact(decision, path)
    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps(CONSUMER_RUN), encoding="utf-8")

    result = review.main([
        "--verify", "--artifact", str(path), "--run", str(run_path),
        "--repository", review.CANONICAL_REPOSITORY, "--pr-number", "1822",
        "--head-sha", HEAD, "--head-ref", "copilot/fix", "--attempt", "1",
    ])
    assert result == 0

    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    result = review.main([
        "--verify", "--artifact", str(path), "--run", str(run_path),
        "--repository", review.CANONICAL_REPOSITORY, "--pr-number", "1822",
        "--head-sha", HEAD, "--head-ref", "copilot/fix", "--attempt", "1",
    ])
    assert result == 1


def qa_policy(mode="independent", *, now=NOW, event=None):
    run = PRODUCER_RUN if event is None else {**PRODUCER_RUN, "event": event}
    return review.build_qa_review_policy(
        repository=review.CANONICAL_REPOSITORY,
        pr_number=1822,
        head_sha=HEAD,
        head_ref="copilot/fix",
        mode=mode,
        run=run,
        now=now,
    )


def qa_policy_archive(policy):
    archive = BytesIO()
    payload = (json.dumps(policy, sort_keys=True, indent=2) + "\n").encode()
    with ZipFile(archive, "w") as bundle:
        bundle.writestr(review.QA_POLICY_ARTIFACT_NAME, payload)
        bundle.writestr(
            review.QA_POLICY_ARTIFACT_NAME + ".sha256",
            __import__("hashlib").sha256(payload).hexdigest() + "\n",
        )
    return archive.getvalue()


def qa_policy_routes(policy=None):
    policy = policy or qa_policy(now=datetime.now(timezone.utc) - timedelta(minutes=1))
    created = policy["created_at_utc"]
    artifact_id = 778
    name = f"wow-v17-qa-policy-{PRODUCER_RUN['id']}-{PRODUCER_RUN['run_attempt']}"
    api_run = {
        **CONSUMER_RUN,
        "id": PRODUCER_RUN["id"],
        "run_attempt": PRODUCER_RUN["run_attempt"],
    }
    metadata = {
        "id": artifact_id,
        "name": name,
        "expired": False,
        "created_at": created,
        "workflow_run": {"id": PRODUCER_RUN["id"]},
    }
    return {
        f"/actions/runs/{PRODUCER_RUN['id']}": api_run,
        f"/actions/runs/{PRODUCER_RUN['id']}/artifacts?per_page=100&page=1": {
            "artifacts": [metadata],
        },
        f"/actions/artifacts/{artifact_id}": metadata,
        "/pulls/1822": pr(),
    }, qa_policy_archive(policy)


def test_qa_policy_artifact_authenticates_exact_run_mode_target_and_freshness():
    routes, archive = qa_policy_routes()
    resolved = review.resolve_qa_review_policy(
        repository=review.CANONICAL_REPOSITORY,
        run_id=PRODUCER_RUN["id"],
        run_attempt=1,
        token="read-only",
        expected_pr_number=1822,
        now=datetime.now(timezone.utc),
        get_json=routes.__getitem__,
        download_artifact=lambda artifact_id: archive if artifact_id == 778 else b"",
    )
    assert resolved["artifact_id"] == 778
    assert resolved["policy"]["mode"] == "independent"
    assert resolved["policy"]["target"] == {
        "pr_number": 1822, "head_sha": HEAD, "head_ref": "copilot/fix",
    }


def test_qa_policy_cli_writes_bounded_exact_target_receipt_and_checksum(tmp_path):
    policy = qa_policy("canary")
    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps(PRODUCER_RUN), encoding="utf-8")
    output = tmp_path / review.QA_POLICY_ARTIFACT_NAME
    result = review.main([
        "--write-policy", "--run", str(run_path), "--output", str(output),
        "--repository", review.CANONICAL_REPOSITORY, "--pr-number", "1822",
        "--head-sha", HEAD, "--head-ref", "copilot/fix", "--attempt", "1",
        "--policy-mode", "canary",
    ])
    assert result == 0
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["mode"] == "canary" and written["canary_scope"] == policy["canary_scope"]
    assert output.with_suffix(output.suffix + ".sha256").read_text().strip()


@pytest.mark.parametrize(("field", "value", "reason"), [
    ("workflow_id", 1, "QA_POLICY_RUN_PROVENANCE_MISMATCH"),
    ("workflow_id", None, "QA_POLICY_RUN_PROVENANCE_MISMATCH"),
    ("workflow_id", True, "QA_POLICY_RUN_PROVENANCE_MISMATCH"),
    ("path", ".github/workflows/other.yml", "QA_POLICY_RUN_PROVENANCE_MISMATCH"),
    ("event", "pull_request_target", "QA_POLICY_RUN_PROVENANCE_MISMATCH"),
    ("head_sha", "c" * 40, "QA_POLICY_RUN_PROVENANCE_MISMATCH"),
    ("repository", {"full_name": "fork/WOW-Dashboard"}, "QA_POLICY_RUN_PROVENANCE_MISMATCH"),
    ("head_repository", {"full_name": "fork/WOW-Dashboard"}, "QA_POLICY_RUN_PROVENANCE_MISMATCH"),
    ("head_branch", "feature", "QA_POLICY_RUN_PROVENANCE_MISMATCH"),
    ("conclusion", "cancelled", "QA_POLICY_RUN_PROVENANCE_MISMATCH"),
    ("run_attempt", 2, "QA_POLICY_RUN_ATTEMPT_MISMATCH"),
])
def test_qa_policy_rejects_foreign_failed_or_replayed_producer_run(field, value, reason):
    routes, archive = qa_policy_routes()
    routes[f"/actions/runs/{PRODUCER_RUN['id']}"][field] = value
    with pytest.raises(review.SourceReviewError, match=reason):
        review.resolve_qa_review_policy(
            repository=review.CANONICAL_REPOSITORY,
            run_id=PRODUCER_RUN["id"],
            run_attempt=1,
            token="read-only",
            now=datetime.now(timezone.utc),
            get_json=routes.__getitem__,
            download_artifact=lambda artifact_id: archive,
        )


@pytest.mark.parametrize(
    ("event", "display_name"),
    [
        ("workflow_dispatch", f"{review.WORKFLOW_NAME} event=workflow_dispatch pr=1963"),
        ("workflow_run", f"{review.WORKFLOW_NAME} event=workflow_run pr=AUTO"),
    ],
)
def test_qa_policy_accepts_dynamic_run_name_for_trusted_workflow(event, display_name):
    routes, archive = qa_policy_routes(
        qa_policy(now=datetime.now(timezone.utc) - timedelta(minutes=1), event=event)
    )
    run = routes[f"/actions/runs/{PRODUCER_RUN['id']}"]
    run["name"] = display_name
    run["event"] = event
    resolved = review.resolve_qa_review_policy(
        repository=review.CANONICAL_REPOSITORY,
        run_id=PRODUCER_RUN["id"],
        run_attempt=1,
        token="read-only",
        expected_pr_number=1822,
        now=datetime.now(timezone.utc),
        get_json=routes.__getitem__,
        download_artifact=lambda artifact_id: archive,
    )
    assert resolved["run"]["name"] == display_name
    assert resolved["policy"]["source"]["head_sha"] == MAIN


@pytest.mark.parametrize(("field", "value"), [
    ("workflow_name", "foreign-workflow"),
    ("workflow_path", ".github/workflows/other.yml"),
    ("event", "pull_request_target"),
    ("head_sha", "c" * 40),
    ("repository", "fork/WOW-Dashboard"),
    ("run_id", "99999"),
])
def test_qa_policy_rejects_fabricated_source_provenance(field, value):
    policy = qa_policy(now=datetime.now(timezone.utc) - timedelta(minutes=1))
    policy["source"][field] = value
    routes, archive = qa_policy_routes(policy)
    with pytest.raises(review.SourceReviewError, match="QA_POLICY_RUN_PROVENANCE_MISMATCH"):
        review.resolve_qa_review_policy(
            repository=review.CANONICAL_REPOSITORY,
            run_id=PRODUCER_RUN["id"],
            run_attempt=1,
            token="read-only",
            now=datetime.now(timezone.utc),
            get_json=routes.__getitem__,
            download_artifact=lambda artifact_id: archive,
        )


def test_qa_policy_can_be_resolved_from_completed_failed_run_to_report_qa_failure():
    routes, archive = qa_policy_routes()
    routes[f"/actions/runs/{PRODUCER_RUN['id']}"]["conclusion"] = "failure"

    resolved = review.resolve_qa_review_policy(
        repository=review.CANONICAL_REPOSITORY,
        run_id=PRODUCER_RUN["id"],
        run_attempt=1,
        token="read-only",
        expected_pr_number=1822,
        now=datetime.now(timezone.utc),
        get_json=routes.__getitem__,
        download_artifact=lambda artifact_id: archive if artifact_id == 778 else b"",
    )

    assert resolved["policy"]["mode"] == "independent"
    assert resolved["run"]["conclusion"] == "failure"


@pytest.mark.parametrize(("mutation", "reason"), [
    (lambda routes, record: routes.update({
        f"/actions/runs/{PRODUCER_RUN['id']}/artifacts?per_page=100&page=1": {"artifacts": []},
    }), "QA_POLICY_ARTIFACT_DUPLICATE_OR_MISSING"),
    (lambda routes, record: routes.update({
        f"/actions/runs/{PRODUCER_RUN['id']}/artifacts?per_page=100&page=1": {"artifacts": [record, record]},
    }), "QA_POLICY_ARTIFACT_DUPLICATE_OR_MISSING"),
    (lambda routes, record: record.update(expired=True), "QA_POLICY_ARTIFACT_EXPIRED"),
])
def test_qa_policy_rejects_missing_duplicate_or_expired_artifacts(mutation, reason):
    routes, archive = qa_policy_routes()
    key = f"/actions/runs/{PRODUCER_RUN['id']}/artifacts?per_page=100&page=1"
    record = routes[key]["artifacts"][0]
    mutation(routes, record)
    with pytest.raises(review.SourceReviewError, match=reason):
        review.resolve_qa_review_policy(
            repository=review.CANONICAL_REPOSITORY,
            run_id=PRODUCER_RUN["id"],
            run_attempt=1,
            token="read-only",
            now=datetime.now(timezone.utc),
            get_json=routes.__getitem__,
            download_artifact=lambda artifact_id: archive,
        )


def test_qa_policy_rejects_pr_movement_checksum_and_canary_scope_replay():
    routes, archive = qa_policy_routes()
    moved_pr = pr()
    moved_pr["head"]["sha"] = MAIN
    routes["/pulls/1822"] = moved_pr
    with pytest.raises(review.SourceReviewError, match="QA_POLICY_TARGET_NO_LONGER_CURRENT"):
        review.resolve_qa_review_policy(
            repository=review.CANONICAL_REPOSITORY,
            run_id=PRODUCER_RUN["id"],
            run_attempt=1,
            token="read-only",
            now=datetime.now(timezone.utc),
            get_json=routes.__getitem__,
            download_artifact=lambda artifact_id: archive,
        )

    canary = qa_policy("canary")
    canary["canary_scope"]["head_sha"] = MAIN
    with pytest.raises(review.SourceReviewError, match="QA_POLICY_CANARY_SCOPE_MISMATCH"):
        review.verify_qa_review_policy(
            canary,
            run=CONSUMER_RUN,
            expected_repository=review.CANONICAL_REPOSITORY,
            expected_pr_number=1822,
            expected_head_sha=HEAD,
            expected_attempt=1,
            now=NOW,
        )


def test_workflow_integrates_read_only_review_in_protected_qa_boundary():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    parsed = __import__("yaml").load(workflow, Loader=__import__("yaml").BaseLoader)
    steps = parsed["jobs"]["qa"]["steps"]
    recheck = next(
        step for step in steps
        if step.get("name") == "Recheck PR head and diff after review"
    )
    review_step = next(step for step in steps if step.get("name") == "Run isolated read-only adversarial review")
    publisher_step = next(step for step in steps if step.get("name") == "Mint checks-only App token")

    assert "github.ref == 'refs/heads/main'" in workflow
    assert parsed["on"]["workflow_run"]["workflows"] == [
        "wow-verify", "wow-engine-verify", "wow-v17-rapid-repair", "wow-v17-change-impact-gate",
        "wow-v17-trusted-governance-gate",
    ]
    assert parsed["on"]["workflow_run"]["types"] == ["completed"]
    assert "actions: read" in workflow
    assert "environment: wow-qa" in workflow
    assert "WOW_QA_REVIEW_OPENAI_API_KEY" in workflow
    assert "QA_REVIEW_PROVIDER_CREDENTIAL_MISSING" in workflow
    assert 'permission_profile: ":read-only"' in workflow
    assert "ref: ${{ github.sha }}" in workflow
    assert '--arg repository "$GITHUB_REPOSITORY" --arg head_sha "$GITHUB_SHA"' in workflow
    assert "WOW_QA_INDEPENDENT_REVIEW_ACTIVE" in workflow
    assert "WOW_QA_INDEPENDENT_REVIEW_CANARY_PR" in workflow
    assert "WOW_QA_INDEPENDENT_REVIEW_CANARY_SHA" in workflow
    assert "Select exact-head review policy" in workflow
    assert "wow-v17-qa-policy-${{ github.run_id }}-${{ github.run_attempt }}" in workflow
    assert "independent_review_canary" in workflow
    assert "WOW_REVIEW_OPENAI_API_KEY" not in workflow
    assert "wow-review" not in workflow
    assert review_step["with"]["openai_api_key"] == "${{ secrets.WOW_QA_REVIEW_OPENAI_API_KEY }}"
    assert "WOW_QA_APP_PRIVATE_KEY" not in review_step["with"].values()
    assert "WOW_QA_REVIEW_OPENAI_API_KEY" not in str(publisher_step)
    assert "contents: write" not in workflow
    assert "pull-requests: write" not in workflow
    assert "actions: write" not in workflow
    assert "checkout.*inputs" not in workflow
    assert "retention-days: 1" in workflow
    assert "github.run_id" in workflow and "github.run_attempt" in workflow
    assert recheck["env"]["PR_NUMBER"] == "${{ steps.target.outputs.pr_number }}"
    assert recheck["env"]["EXPECTED_HEAD_SHA"] == "${{ steps.target.outputs.head_sha }}"


def test_capture_uses_read_only_api_and_rejects_head_movement(monkeypatch):
    responses = [
        {
            "number": 1822, "state": "open", "draft": False, "merged_at": None,
            "changed_files": 1, "user": {"id": 123, "login": "copilot-swe-agent"},
            "head": {"sha": HEAD, "ref": "copilot/fix", "repo": {"full_name": review.CANONICAL_REPOSITORY}},
            "base": {"ref": "main", "repo": {"full_name": review.CANONICAL_REPOSITORY}},
        },
        [{
            "filename": "src/fix.py", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b",
            "additions": 1, "deletions": 1,
            "sha": "not-used", "blob_url": "https://example.invalid",
        }],
        {
            "sha": HEAD, "commit": {"tree": {"sha": "d" * 40}, "message": "Fix"},
            "parents": [{"sha": "e" * 40}], "author": {"login": "copilot-swe-agent"},
            "committer": {"login": "copilot-swe-agent"},
        },
        {
            "number": 1822, "state": "open", "draft": False, "merged_at": None,
            "changed_files": 1, "user": {"id": 123, "login": "copilot-swe-agent"},
            "head": {"sha": HEAD, "ref": "copilot/fix", "repo": {"full_name": review.CANONICAL_REPOSITORY}},
            "base": {"ref": "main", "repo": {"full_name": review.CANONICAL_REPOSITORY}},
        },
    ]
    stable_pr = responses[0]
    requests = []

    class Response:
        def __init__(self, payload):
            self.payload = json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _limit):
            return self.payload

    def fake_urlopen(request, timeout):
        requests.append(request)
        assert timeout == 30
        assert request.get_method() == "GET"
        assert request.headers["Authorization"] == "token test-read-only-token"
        return Response(responses.pop(0))

    monkeypatch.setattr(review, "urlopen", fake_urlopen)
    captured = review.capture_pr_evidence(
        repository=review.CANONICAL_REPOSITORY,
        pr_number=1822,
        expected_head_sha=HEAD,
        token="test-read-only-token",
    )
    assert len(requests) == 4
    assert requests[2].full_url.endswith(f"/commits/{HEAD}")
    assert captured["files"][0]["previous_filename"] is None

    moved = {
        "number": 1822, "state": "open", "draft": False, "merged_at": None,
        "changed_files": 1, "user": {"id": 123, "login": "copilot-swe-agent"},
        "head": {"sha": "c" * 40, "ref": "copilot/fix", "repo": {"full_name": review.CANONICAL_REPOSITORY}},
        "base": {"ref": "main", "repo": {"full_name": review.CANONICAL_REPOSITORY}},
    }
    responses.extend([stable_pr, [{
        "filename": "src/fix.py", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b",
        "additions": 1, "deletions": 1,
    }], COMMIT_METADATA, moved])
    with pytest.raises(review.SourceReviewError, match="PR_IDENTITY_MISMATCH"):
        review.capture_pr_evidence(
            repository=review.CANONICAL_REPOSITORY,
            pr_number=1822,
            expected_head_sha=HEAD,
            token="test-read-only-token",
        )


def test_capture_api_unavailability_is_typed_and_does_not_echo_credentials(monkeypatch, capsys):
    def unavailable(_request, timeout):
        raise URLError("test failure")

    monkeypatch.setattr(review, "urlopen", unavailable)
    with pytest.raises(review.SourceReviewError, match="GITHUB_API_UNAVAILABLE"):
        review.capture_pr_evidence(
            repository=review.CANONICAL_REPOSITORY,
            pr_number=1822,
            expected_head_sha=HEAD,
            token="must-not-be-logged",
        )
    assert "must-not-be-logged" not in capsys.readouterr().out


def test_all_receipt_consumers_can_rederive_source_run_and_artifact_provenance(monkeypatch):
    source_artifact = artifact()
    artifact_id = 77
    artifact_name = f"wow-v17-independent-source-review-1822-{HEAD}-12345-1"
    responses = [
        CONSUMER_RUN,
        {
            "id": artifact_id,
            "expired": False,
            "name": artifact_name,
            "workflow_run": {"id": 12345},
        },
        {
            "artifacts": [{
                "id": artifact_id,
                "expired": False,
                "name": artifact_name,
                "created_at": NOW.isoformat(),
            }],
        },
    ]

    class Response:
        def __init__(self, payload):
            self.payload = json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _limit):
            return self.payload

    def fake_urlopen(request, timeout):
        assert request.headers["Authorization"] == "token scoped-read-only"
        assert timeout == 30
        return Response(responses.pop(0))

    monkeypatch.setattr(review, "urlopen", fake_urlopen)
    result = review.resolve_source_review_run(
        source_artifact,
        artifact_id=artifact_id,
        repository=review.CANONICAL_REPOSITORY,
        token="scoped-read-only",
        now=NOW,
    )
    assert result == CONSUMER_RUN
    assert not responses


def test_source_run_resolution_holds_on_foreign_or_duplicate_artifact(monkeypatch):
    source_artifact = artifact()
    artifact_id = 77
    artifact_name = f"wow-v17-independent-source-review-1822-{HEAD}-12345-1"
    responses = [
        CONSUMER_RUN,
        {
            "id": artifact_id,
            "expired": False,
            "name": artifact_name,
            "workflow_run": {"id": 99999},
        },
    ]

    class Response:
        def __init__(self, payload):
            self.payload = json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _limit):
            return self.payload

    monkeypatch.setattr(review, "urlopen", lambda *_args, **_kwargs: Response(responses.pop(0)))
    with pytest.raises(review.SourceReviewError, match="ARTIFACT_PROVENANCE_MISMATCH"):
        review.resolve_source_review_run(
            source_artifact,
            artifact_id=artifact_id,
            repository=review.CANONICAL_REPOSITORY,
            token="scoped-read-only",
            now=NOW,
        )


@pytest.mark.parametrize("display_name", [
    review.WORKFLOW_NAME,
    f"{review.WORKFLOW_NAME} event=workflow_run pr=AUTO head=AUTO",
    "human-facing title changed but authenticated ID stays pinned",
])
def test_qa_producer_identity_is_independent_of_mutable_display_name(display_name):
    producer = {**PRODUCER_RUN, "name": display_name}
    policy = review.build_qa_review_policy(
        repository=review.CANONICAL_REPOSITORY, pr_number=1822,
        head_sha=HEAD, head_ref="copilot/fix", mode="independent",
        run=producer, now=NOW,
    )
    assert policy["source"]["workflow_name"] == review.WORKFLOW_NAME
    artifact = review.build_artifact(
        repository=review.CANONICAL_REPOSITORY, pr=pr(), files=DIFF,
        commit=COMMIT_METADATA, review_result=RESULT,
        run=producer, now=NOW,
    )
    review.verify_artifact(
        artifact, run=producer, producer_context=True,
        expected_repository=review.CANONICAL_REPOSITORY,
        expected_pr_number=1822, expected_head_sha=HEAD,
        expected_head_ref="copilot/fix", now=NOW,
    )


@pytest.mark.parametrize("field,value", [
    ("workflow_id", review.WORKFLOW_ID + 1),
    ("workflow_id", None),
    ("workflow_id", True),
    ("path", ".github/workflows/untrusted.yml"),
])
def test_qa_producer_fails_closed_on_wrong_workflow_identity(field, value):
    producer = {**PRODUCER_RUN, field: value}
    with pytest.raises(review.SourceReviewError, match="QA_POLICY_RUN_IDENTITY_INVALID"):
        review.build_qa_review_policy(
            repository=review.CANONICAL_REPOSITORY, pr_number=1822,
            head_sha=HEAD, head_ref="copilot/fix", mode="independent",
            run=producer, now=NOW,
        )
    with pytest.raises(review.SourceReviewError, match="SOURCE_REVIEW_WORKFLOW_IDENTITY_MISMATCH"):
        review.build_artifact(
            repository=review.CANONICAL_REPOSITORY, pr=pr(), files=DIFF,
            commit=COMMIT_METADATA, review_result=RESULT,
            run=producer, now=NOW,
        )


def test_both_qa_producer_payloads_use_pinned_workflow_id():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert source.count("--argjson workflow_id 380562324") == 2
    assert source.count("workflow_id:$workflow_id") == 2
    script = SCRIPT.read_text(encoding="utf-8")
    assert 'run.get("name") == WORKFLOW_NAME' not in script
    assert 'and workflow_id == WORKFLOW_ID' in script
    assert 'run.get("path") == WORKFLOW_PATH' in script


@pytest.mark.parametrize("provider,scope", [
    ("openai-codex-action", "WOW_QA_REVIEW_OPENAI_API_KEY"),
    ("anthropic-claude-api", "WOW_QA_REVIEW_ANTHROPIC_API_KEY"),
    ("anthropic-claude-api", "ANTHROPIC_API_KEY"),
    ("gemini-generative-api", "WOW_QA_REVIEW_GEMINI_API_KEY"),
    ("gemini-generative-api", "GEMINI_API_KEY"),
])
def test_only_registered_producer_provider_credentials_can_build_and_verify(provider, scope):
    authentic = review.build_artifact(
        repository=review.CANONICAL_REPOSITORY,
        pr=pr(), files=DIFF, commit=COMMIT_METADATA, review_result=RESULT,
        run=PRODUCER_RUN, now=NOW,
        provider_identity=provider, credential_scope=scope,
    )
    assert authentic["source"]["provider_identity"] == provider
    assert authentic["source"]["credential_scope"] == scope
    verify(authentic)


@pytest.mark.parametrize("provider,scope", [
    ("owner-claim", "WOW_QA_REVIEW_OPENAI_API_KEY"),
    ("anthropic-claude-api", "WOW_QA_REVIEW_GEMINI_API_KEY"),
    ("gemini-generative-api", "ANTHROPIC_API_KEY"),
    ("openai-codex-action", "GEMINI_API_KEY"),
])
def test_unknown_or_swapped_provider_binding_cannot_issue_pass(provider, scope):
    with pytest.raises(review.SourceReviewError, match="SOURCE_REVIEW_PROVIDER_CREDENTIAL_UNTRUSTED"):
        review.build_artifact(
            repository=review.CANONICAL_REPOSITORY,
            pr=pr(), files=DIFF, commit=COMMIT_METADATA, review_result=RESULT,
            run=PRODUCER_RUN, now=NOW,
            provider_identity=provider, credential_scope=scope,
        )
    fake = artifact()
    fake["source"]["provider_identity"] = provider
    fake["source"]["credential_scope"] = scope
    with pytest.raises(review.SourceReviewError, match="SOURCE_REVIEW_PROVENANCE_MISMATCH"):
        verify(fake)
