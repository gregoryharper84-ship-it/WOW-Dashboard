from __future__ import annotations

import importlib.util
import base64
import hashlib
import hmac
import json
from pathlib import Path
import sys
from datetime import datetime, timedelta, timezone
from urllib.error import URLError

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
V17 = ROOT / "artifacts/wow-engine/v17"
sys.path.insert(0, str(V17))

import governance_dispatcher as policy

spec = importlib.util.spec_from_file_location(
    "governance_dispatcher_github_for_tests",
    V17 / "governance_dispatcher_github.py",
)
transport = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = transport
spec.loader.exec_module(transport)

OWNER_ID = 285088163
OWNER = "gregoryharper84-ship-it"
REPOSITORY = "gregoryharper84-ship-it/WOW-Dashboard"
HEAD = "a" * 40
OTHER_HEAD = "b" * 40


def make_pull(*, author_id=9001, author="engineering-agent", sha=HEAD, changed_files=1):
    return {
        "number": 41,
        "state": "open",
        "draft": False,
        "merged_at": None,
        "changed_files": changed_files,
        "user": {"id": author_id, "login": author},
        "head": {
            "sha": sha,
            "ref": "fix/governance",
            "repo": {"full_name": REPOSITORY},
        },
        "base": {"ref": "main", "repo": {"full_name": REPOSITORY}},
    }


def make_file(path="src/example.py", **extra):
    return {"filename": path, "status": "modified", **extra}


def make_check(name, app_id=15368, conclusion="success", *, head=HEAD, status="completed", id=1):
    return {
        "id": id,
        "name": name,
        "head_sha": head,
        "status": status,
        "conclusion": conclusion if status == "completed" else None,
        "started_at": f"2026-10-21T00:00:{id % 60:02d}Z",
        "completed_at": f"2026-10-21T00:01:{id % 60:02d}Z" if status == "completed" else None,
        "app": {"id": app_id},
    }


def all_ci_checks():
    return [
        *(make_check(name, id=index + 1) for index, name in enumerate(policy.QA_EVIDENCE_CHECKS)),
        make_check(policy.TRUSTED_GOVERNANCE_CHECK, id=10),
    ]


def required_ci_checks():
    return [make_check(name, id=index + 1) for index, name in enumerate(policy.QA_EVIDENCE_CHECKS)]


def journal_test_key():
    return hashlib.sha256(b"dispatcher journal test key").hexdigest()


@pytest.mark.parametrize("value", ["", "0" * 63, "g" * 64, "0" * 65])
def test_journal_key_requires_exactly_64_hex_characters(value):
    with pytest.raises(transport.DispatchError, match="DISPATCH_JOURNAL_KEY_MISSING_OR_INVALID"):
        transport._journal_key(value)


def signed_marker(payload, key=None):
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    signature = hmac.new(bytes.fromhex(key or journal_test_key()), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{transport.SIGNED_RESERVATION_PREFIX}{encoded}.{signature} -->"


def dispatcher_run(run_id="12345", run_attempt=1, **overrides):
    return {
        "id": int(run_id),
        "path": transport.DISPATCHER_WORKFLOW_PATH,
        "repository": {"full_name": REPOSITORY},
        "head_branch": "main",
        "event": "schedule",
        "run_attempt": run_attempt,
        **overrides,
    }


def classify(**overrides):
    values = {
        "repository": REPOSITORY,
        "owner_id": OWNER_ID,
        "owner_login": OWNER,
        "pull": make_pull(),
        "file_records": [make_file()],
        "check_runs": [],
        "reviews": [],
        "source_review": "PASS",
        "available_roles": {"source_review", "qa", "release"},
        "completed_role_runs": {},
    }
    values.update(overrides)
    return policy.classify_target(**values)


class FakeSourceReviewError(ValueError):
    pass


def source_review_module(error_code="SOURCE_REVIEW_ARTIFACT_DUPLICATE_OR_MISSING"):
    def fail(**_kwargs):
        raise FakeSourceReviewError(error_code)

    return type(
        "FakeSourceReviewModule",
        (),
        {
            "SourceReviewError": FakeSourceReviewError,
            "resolve_current_source_review": staticmethod(fail),
        },
    )


def test_queue_rotates_bounded_pages_without_oldest_pr_starvation():
    pulls = [
        {
            **make_pull(sha=f"{number:040x}"),
            "number": number,
        }
        for number in range(1, 51)
    ]

    first = policy.select_targets(pulls, run_number=1, limit=10)
    second = policy.select_targets(pulls, run_number=2, limit=10)
    third = policy.select_targets(pulls, run_number=5, limit=10)

    assert [pr["number"] for pr in first] == list(range(1, 11))
    assert [pr["number"] for pr in second] == list(range(11, 21))
    assert [pr["number"] for pr in third] == list(range(41, 51))
    assert len(first) == 10


@pytest.mark.parametrize(
    "mutation",
    [
        lambda pr: pr.update(draft=True),
        lambda pr: pr.update(draft=None),
        lambda pr: pr.update(state="closed"),
        lambda pr: pr["base"].update(ref="other"),
        lambda pr: pr["head"].update(sha="invalid"),
        lambda pr: pr["head"]["repo"].update(full_name="fork/WOW-Dashboard"),
        lambda pr: pr["base"]["repo"].update(full_name="fork/WOW-Dashboard"),
    ],
)
def test_queue_excludes_untrusted_or_ineligible_pull_requests(mutation):
    pr = make_pull()
    mutation(pr)

    assert policy.select_targets([pr], run_number=1) == []


@pytest.mark.parametrize(
    ("source_review", "runs", "expected", "finding"),
    [
        ("REJECT", {}, "HOLD", "SOURCE_REVIEW_REJECT"),
        ("INVALID", {}, "HOLD", "SOURCE_REVIEW_INVALID"),
        ("PENDING", {}, "HOLD", "SOURCE_REVIEW_PENDING"),
        ("MISSING", {"source_review": "in_progress"}, "HOLD", "SOURCE_REVIEW_PENDING"),
        ("MISSING", {"source_review": "success"}, "HOLD", "SOURCE_REVIEW_RUN_FAILED"),
        ("MISSING", {}, "DISPATCH", None),
    ],
)
def test_source_review_gate_is_fail_closed(source_review, runs, expected, finding):
    result = classify(
        source_review=source_review,
        completed_role_runs=runs,
        check_runs=all_ci_checks(),
    )

    assert result["action"] == expected
    if finding:
        assert finding in result["findings"]
    if expected == "DISPATCH":
        assert result["role"] == "source_review"


def test_missing_trusted_gate_does_not_block_read_only_source_review_dispatch():
    result = classify(
        source_review="MISSING",
        check_runs=required_ci_checks(),
        completed_role_runs={},
    )

    assert result["action"] == "DISPATCH"
    assert result["role"] == "source_review"
    assert result["head_sha"] == HEAD


def test_action_required_zero_job_source_review_only_dispatches_reviewer():
    result = classify(
        source_review="ACTION_REQUIRED_ZERO_JOBS",
        check_runs=required_ci_checks(),
        completed_role_runs={"source_review": "action_required"},
    )

    assert result["action"] == "DISPATCH"
    assert result["role"] == "source_review"
    assert result["head_sha"] == HEAD

    not_verified = classify(
        source_review="ACTION_REQUIRED_ZERO_JOBS",
        check_runs=required_ci_checks(),
        completed_role_runs={"source_review": "success"},
    )
    assert not_verified["action"] == "HOLD"
    assert not_verified["findings"] == ["SOURCE_REVIEW_BLOCKED_RUN_UNPROVEN"]


@pytest.mark.parametrize(
    ("trusted_check", "finding"),
    [
        (None, f"TRUSTED_GOVERNANCE_CHECK_MISSING:{policy.TRUSTED_GOVERNANCE_CHECK}"),
        (
            make_check(policy.TRUSTED_GOVERNANCE_CHECK, status="in_progress"),
            f"TRUSTED_GOVERNANCE_CHECK_PENDING:{policy.TRUSTED_GOVERNANCE_CHECK}",
        ),
        (
            make_check(policy.TRUSTED_GOVERNANCE_CHECK, conclusion="failure"),
            f"TRUSTED_GOVERNANCE_CHECK_FAILED:{policy.TRUSTED_GOVERNANCE_CHECK}",
        ),
        (
            make_check(policy.TRUSTED_GOVERNANCE_CHECK, app_id=policy.QA_APP_ID),
            f"TRUSTED_GOVERNANCE_CHECK_FOREIGN_SOURCE:{policy.TRUSTED_GOVERNANCE_CHECK}",
        ),
    ],
)
def test_authenticated_source_review_does_not_bypass_downstream_trusted_gate(trusted_check, finding):
    checks = required_ci_checks()
    if trusted_check:
        checks.append(trusted_check)

    result = classify(source_review="PASS", check_runs=checks)

    assert result["action"] == "HOLD"
    assert result["findings"] == [finding]


def test_source_review_and_trusted_gate_order_before_qa_then_release():
    checks = required_ci_checks()

    reviewed_without_trusted = classify(source_review="PASS", check_runs=checks)
    assert reviewed_without_trusted["action"] == "HOLD"
    assert reviewed_without_trusted["findings"] == [
        f"TRUSTED_GOVERNANCE_CHECK_MISSING:{policy.TRUSTED_GOVERNANCE_CHECK}"
    ]

    checks.append(make_check(policy.TRUSTED_GOVERNANCE_CHECK))
    qa = classify(source_review="PASS", check_runs=checks)
    assert qa["action"] == "DISPATCH"
    assert qa["role"] == "qa"

    checks.append(make_check(policy.QA_CHECK, app_id=policy.QA_APP_ID))
    release = classify(source_review="PASS", check_runs=checks)
    assert release["action"] == "DISPATCH"
    assert release["role"] == "release"


def test_same_sha_opened_then_ready_blocked_review_still_routes_only_source_review():
    opened = classify(
        pull=make_pull(sha=HEAD),
        source_review="MISSING",
        check_runs=required_ci_checks(),
    )
    ready = classify(
        pull=make_pull(sha=HEAD),
        source_review="ACTION_REQUIRED_ZERO_JOBS",
        check_runs=required_ci_checks(),
        completed_role_runs={"source_review": "action_required"},
    )

    assert opened["head_sha"] == ready["head_sha"] == HEAD
    assert [(opened["action"], opened.get("role")), (ready["action"], ready.get("role"))] == [
        ("DISPATCH", "source_review"),
        ("DISPATCH", "source_review"),
    ]


def test_source_review_must_exist_as_active_protected_workflow():
    result = classify(available_roles={"qa", "release"}, source_review="MISSING")

    assert result == {
        "action": "HOLD",
        "findings": ["SOURCE_REVIEW_WORKFLOW_UNAVAILABLE"],
        "head_sha": HEAD,
    }


def test_ci_must_be_ready_before_controller_dispatches_source_review():
    result = classify(source_review="MISSING")

    assert result["action"] == "HOLD"
    assert result["findings"][0].startswith("REQUIRED_CHECK_MISSING:")


def test_source_review_that_finishes_before_ci_waits_for_ci_then_routes_qa():
    early = classify(source_review="PASS", check_runs=[])
    ready = classify(source_review="PASS", check_runs=all_ci_checks())

    assert early["action"] == "HOLD"
    assert early["findings"][0].startswith("REQUIRED_CHECK_MISSING:")
    assert ready["action"] == "DISPATCH"
    assert ready["role"] == "qa"


def test_expired_review_can_be_refreshed_only_after_successful_prior_review():
    result = classify(
        source_review="STALE",
        check_runs=all_ci_checks(),
        completed_role_runs={"source_review": "success"},
    )

    assert result["action"] == "DISPATCH"
    assert result["role"] == "source_review"


def test_expired_review_without_a_verified_successful_run_holds():
    result = classify(
        source_review="STALE",
        check_runs=all_ci_checks(),
        completed_role_runs={"source_review": "action_required"},
    )

    assert result["action"] == "HOLD"
    assert result["findings"] == ["SOURCE_REVIEW_STALE_UNPROVEN"]


def test_complete_protected_ci_is_required_before_qa_dispatch():
    result = classify(check_runs=[])

    assert result["action"] == "HOLD"
    assert result["findings"] == [f"REQUIRED_CHECK_MISSING:{policy.QA_EVIDENCE_CHECKS[0]}"]


@pytest.mark.parametrize("app_id", [None, 1, 5245832])
def test_ci_checks_must_come_from_actions_app(app_id):
    checks = all_ci_checks()
    checks[0]["app"]["id"] = app_id

    result = classify(check_runs=checks)

    assert result["action"] == "HOLD"
    assert result["findings"][0].startswith("REQUIRED_CHECK_FOREIGN_SOURCE:")


def test_newer_pending_ci_run_supersedes_an_older_success():
    checks = all_ci_checks()
    checks.extend([
        make_check(policy.QA_EVIDENCE_CHECKS[0], id=100, status="in_progress"),
    ])

    result = classify(check_runs=checks)

    assert result["action"] == "HOLD"
    assert result["findings"] == [f"REQUIRED_CHECK_PENDING:{policy.QA_EVIDENCE_CHECKS[0]}"]


def test_newer_foreign_ci_run_supersedes_an_older_actions_app_success():
    checks = all_ci_checks()
    checks.extend([
        make_check(policy.QA_EVIDENCE_CHECKS[0], app_id=98765, id=100),
    ])

    result = classify(check_runs=checks)

    assert result["action"] == "HOLD"
    assert result["findings"] == [f"REQUIRED_CHECK_FOREIGN_SOURCE:{policy.QA_EVIDENCE_CHECKS[0]}"]


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", "action_required", "neutral"])
def test_failed_regression_or_change_impact_check_never_dispatches_qa(conclusion):
    checks = all_ci_checks()
    checks[0]["conclusion"] = conclusion

    result = classify(check_runs=checks)

    assert result["action"] == "HOLD"
    assert result["findings"][0].startswith("REQUIRED_CHECK_FAILED:")


def test_ordinary_pr_requires_trusted_governance_check_before_qa():
    checks = [item for item in all_ci_checks() if item["name"] != policy.TRUSTED_GOVERNANCE_CHECK]

    result = classify(check_runs=checks)

    assert result["action"] == "HOLD"
    assert result["findings"] == [
        f"TRUSTED_GOVERNANCE_CHECK_MISSING:{policy.TRUSTED_GOVERNANCE_CHECK}"
    ]


def test_ordinary_pr_routes_qa_only_after_checks_and_reviewer_artifact():
    result = classify(check_runs=all_ci_checks())

    assert result["action"] == "DISPATCH"
    assert result["role"] == "qa"
    assert result["head_sha"] == HEAD


def test_qa_check_must_be_exact_head_and_from_qa_app():
    checks = all_ci_checks() + [
        make_check(policy.QA_CHECK, app_id=15368, head=HEAD),
    ]

    result = classify(check_runs=checks)

    assert result["action"] == "HOLD"
    assert result["findings"] == ["QA_CHECK_FOREIGN_SOURCE"]


def test_pending_qa_check_is_not_retried_or_passed():
    checks = all_ci_checks() + [
        make_check(policy.QA_CHECK, app_id=policy.QA_APP_ID, status="in_progress"),
    ]

    result = classify(check_runs=checks)

    assert result["action"] == "HOLD"
    assert result["findings"] == ["QA_CHECK_PENDING"]


def test_failed_qa_workflow_without_app_check_is_not_blindly_retried():
    result = classify(check_runs=all_ci_checks(), completed_role_runs={"qa": "action_required"})

    assert result["action"] == "HOLD"
    assert result["findings"] == ["QA_WORKFLOW_RUN_FAILED"]


def test_qa_success_allows_only_release_dispatch():
    checks = all_ci_checks() + [make_check(policy.QA_CHECK, app_id=policy.QA_APP_ID)]

    result = classify(check_runs=checks)

    assert result["action"] == "DISPATCH"
    assert result["role"] == "release"


def test_release_check_requires_distinct_release_app():
    checks = all_ci_checks() + [
        make_check(policy.QA_CHECK, app_id=policy.QA_APP_ID),
        make_check(policy.RELEASE_CHECK, app_id=policy.QA_APP_ID),
    ]

    result = classify(check_runs=checks)

    assert result["action"] == "HOLD"
    assert result["findings"] == ["RELEASE_CHECK_FOREIGN_SOURCE"]


def test_complete_qa_and_release_roles_are_done():
    checks = all_ci_checks() + [
        make_check(policy.QA_CHECK, app_id=policy.QA_APP_ID),
        make_check(policy.RELEASE_CHECK, app_id=policy.RELEASE_APP_ID),
    ]

    result = classify(check_runs=checks)

    assert result["action"] == "DONE"
    assert result["head_sha"] == HEAD


def test_duplicate_or_incomplete_file_inventory_holds():
    result = classify(file_records=[make_file()], pull=make_pull(changed_files=2))

    assert result["action"] == "HOLD"
    assert result["findings"] == ["PR_FILE_INVENTORY_INCOMPLETE"]


def test_renamed_trust_root_is_still_protected_by_its_previous_path():
    pr = make_pull()
    pr["changed_files"] = 1
    pr["user"] = {"id": 9001, "login": "engineering-agent"}
    file_records = [
        {
            "filename": "docs/renamed-workflow.txt",
            "previous_filename": ".github/workflows/old.yml",
            "status": "renamed",
        }
    ]

    result = classify(pull=pr, file_records=file_records, source_review="MISSING")

    assert result["action"] == "HOLD"
    assert result["findings"] == ["TRUST_ROOT_OWNER_APPROVAL_MISSING"]


def test_trust_root_owner_authored_change_cannot_self_approve():
    result = classify(
        pull=make_pull(author_id=OWNER_ID, author=OWNER),
        file_records=[make_file(".github/workflows/new.yml")],
        reviews=[{"user": {"id": OWNER_ID, "login": OWNER}, "state": "APPROVED", "commit_id": HEAD}],
    )

    assert result["action"] == "HOLD"
    assert result["findings"] == ["TRUST_ROOT_OWNER_AUTHORED"]


@pytest.mark.parametrize(
    ("state", "commit_id"),
    [("COMMENTED", HEAD), ("CHANGES_REQUESTED", HEAD), ("DISMISSED", HEAD), ("APPROVED", OTHER_HEAD)],
)
def test_trust_root_requires_genuine_owner_approval_on_exact_head(state, commit_id):
    result = classify(
        file_records=[make_file(".github/workflows/new.yml")],
        reviews=[{"user": {"id": OWNER_ID, "login": OWNER}, "state": state, "commit_id": commit_id}],
    )

    assert result["action"] == "HOLD"
    assert result["findings"] == ["TRUST_ROOT_OWNER_APPROVAL_MISSING"]


def test_trust_root_with_exact_owner_approval_still_requires_source_review():
    result = classify(
        file_records=[make_file(".github/workflows/new.yml")],
        reviews=[{"user": {"id": OWNER_ID, "login": OWNER}, "state": "APPROVED", "commit_id": HEAD}],
        source_review="MISSING",
        check_runs=all_ci_checks(),
    )

    assert result["action"] == "DISPATCH"
    assert result["role"] == "source_review"


@pytest.mark.parametrize("role", ["qa", "release", "source_review"])
def test_manual_dispatch_names_bind_role_pr_and_exact_head(role):
    name = policy.run_name(role, 41, HEAD)

    assert f"pr=41" in name
    assert f"head={HEAD}" in name
    assert name.startswith(WORKFLOW_NAMES[role])


WORKFLOW_NAMES = {
    "source_review": "wow-v17-independent-source-review",
    "qa": "wow-v17-independent-qa-check",
    "release": "wow-v17-release-authority-check",
}


def test_dispatch_names_reject_unknown_roles():
    with pytest.raises(ValueError, match="DISPATCH_ROLE_NOT_ALLOWLISTED"):
        policy.run_name("merge", 41, HEAD)


def test_github_dispatch_posts_only_allowlisted_workflow_with_main_ref():
    class FakeClient:
        def __init__(self):
            self.posts = []

        def post(self, path, body):
            self.posts.append((path, body))

    client = FakeClient()
    workflow = {
        "id": 123,
        "path": transport.WORKFLOWS["qa"]["path"],
    }

    transport.dispatch(client, "qa", workflow, 41, HEAD)

    assert client.posts == [
        (
            f"/repos/{REPOSITORY}/actions/workflows/123/dispatches",
            {"ref": "main", "inputs": {"pr_number": "41", "head_sha": HEAD}},
        )
    ]


def test_github_dispatch_rejects_workflow_path_alias():
    class FakeClient:
        def post(self, *_):
            pytest.fail("untrusted workflow must not be dispatched")

    with pytest.raises(transport.DispatchError, match="DISPATCH_TARGET_NOT_ALLOWLISTED"):
        transport.dispatch(FakeClient(), "qa", {"id": 123, "path": "other.yml"}, 41, HEAD)


def test_active_work_in_progress_is_counted_per_role():
    class FakeClient:
        def get(self, path):
            if "/workflows/2/runs?" in path:
                if "status=in_progress" in path:
                    return {"workflow_runs": [{"id": 10, "status": "in_progress"}]}
                if "status=queued" in path:
                    return {"workflow_runs": [{"id": 11, "status": "queued"}]}
                return {"workflow_runs": []}
            return {"workflow_runs": []}

    workflows = {
        role: {"id": index + 1, **details}
        for index, (role, details) in enumerate(transport.WORKFLOWS.items())
    }

    assert transport.count_wip(FakeClient(), workflows) == {
        "source_review": 0,
        "qa": 2,
        "release": 0,
    }


def test_dispatch_reservation_is_signed_canonical_and_contains_no_token(monkeypatch):
    monkeypatch.setenv(transport.JOURNAL_KEY_ENV, journal_test_key())

    class FakeClient:
        def __init__(self):
            self.posts = []

        def post(self, path, body):
            self.posts.append((path, body))

    client = FakeClient()

    transport.reserve_dispatch(
        client,
        role="release",
        pr_number=41,
        head_sha=HEAD,
        controller_run_id="12345",
    )

    path, body = client.posts[0]
    assert path == f"/repos/{REPOSITORY}/issues/41/comments"
    marker = body["body"][len(transport.SIGNED_RESERVATION_PREFIX):-3].strip()
    encoded, signature = marker.split(".")
    raw_payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    reservation = json.loads(raw_payload)
    assert reservation == {
        "schema": 2,
        "role": "release",
        "pr_number": 41,
        "head_sha": HEAD,
        "run_id": "12345",
        "attempt": 1,
    }
    assert raw_payload == json.dumps(reservation, sort_keys=True, separators=(",", ":")).encode()
    assert hmac.compare_digest(
        signature,
        hmac.new(bytes.fromhex(journal_test_key()), encoded.encode(), hashlib.sha256).hexdigest(),
    )
    assert "token" not in body["body"].lower()


def test_reservation_journal_ignores_non_controller_authors(monkeypatch):
    monkeypatch.setenv(transport.JOURNAL_KEY_ENV, journal_test_key())
    payload = {
        "schema": 2,
        "role": "qa",
        "pr_number": 41,
        "head_sha": HEAD,
        "run_id": "12345",
        "attempt": 1,
    }

    class FakeClient:
        def get(self, path):
            if "/actions/runs/" in path:
                return dispatcher_run()
            return [
                {
                    "user": {"login": "other-workflow[bot]", "type": "Bot"},
                    "body": signed_marker(payload),
                }
            ]

    assert transport.list_reservations(FakeClient(), 41) == []


def test_authenticated_reservation_requires_real_protected_dispatcher_run(monkeypatch):
    monkeypatch.setenv(transport.JOURNAL_KEY_ENV, journal_test_key())
    payload = {
        "schema": 2,
        "role": "release",
        "pr_number": 41,
        "head_sha": HEAD,
        "run_id": "12345",
        "attempt": 2,
    }

    class FakeClient:
        def get(self, path):
            if "/actions/runs/" in path:
                return dispatcher_run(run_attempt=2, event="workflow_dispatch")
            comment = {
                "user": {"login": "github-actions[bot]", "type": "Bot"},
                "body": signed_marker(payload),
            }
            return [comment, comment]

    reservations = transport.list_reservations(FakeClient(), 41)
    assert reservations == [payload]
    assert transport.reservation_attempts(reservations, "release", 41, HEAD) == [1]


def test_reservation_journal_rejects_authenticated_malformed_record(monkeypatch):
    monkeypatch.setenv(transport.JOURNAL_KEY_ENV, journal_test_key())

    class FakeClient:
        def get(self, _):
            return [
                {
                    "user": {"login": "github-actions[bot]", "type": "Bot"},
                    "body": signed_marker({"unknown": 1}),
                }
            ]

    with pytest.raises(transport.DispatchError, match="DISPATCH_JOURNAL_SCHEMA_INVALID"):
        transport.list_reservations(FakeClient(), 41)


def test_forged_or_tampered_reservation_is_not_accepted(monkeypatch):
    monkeypatch.setenv(transport.JOURNAL_KEY_ENV, journal_test_key())
    payload = {
        "schema": 2,
        "role": "qa",
        "pr_number": 41,
        "head_sha": HEAD,
        "run_id": "12345",
        "attempt": 1,
    }
    genuine = signed_marker(payload)
    encoded, signature = genuine[len(transport.SIGNED_RESERVATION_PREFIX):-3].strip().split(".")
    tampered_payload = payload | {"head_sha": OTHER_HEAD}
    tampered_encoded = base64.urlsafe_b64encode(
        json.dumps(tampered_payload, sort_keys=True, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    tampered = f"{transport.SIGNED_RESERVATION_PREFIX}{tampered_encoded}.{signature} -->"
    forged = genuine.rsplit(".", 1)[0] + "." + "0" * 64 + " -->"
    unsigned = f"{transport.SIGNED_RESERVATION_PREFIX}{encoded} -->"
    malformed = f"{transport.SIGNED_RESERVATION_PREFIX}☃.{signature} -->"

    class FakeClient:
        run_lookups = 0

        def get(self, _):
            if "/actions/runs/" in _:
                self.run_lookups += 1
                return dispatcher_run()
            return [
                {"user": {"login": "github-actions[bot]", "type": "Bot"}, "body": forged},
                {"user": {"login": "github-actions[bot]", "type": "Bot"}, "body": tampered},
                {"user": {"login": "github-actions[bot]", "type": "Bot"}, "body": unsigned},
                {"user": {"login": "github-actions[bot]", "type": "Bot"}, "body": malformed},
            ]

    client = FakeClient()
    assert transport.list_reservations(client, 41) == []
    assert client.run_lookups == 0


def test_signed_reservation_requires_exact_dispatcher_run_provenance(monkeypatch):
    monkeypatch.setenv(transport.JOURNAL_KEY_ENV, journal_test_key())
    payload = {
        "schema": 2,
        "role": "qa",
        "pr_number": 41,
        "head_sha": HEAD,
        "run_id": "12345",
        "attempt": 2,
    }

    class FakeClient:
        def get(self, path):
            if "/actions/runs/" in path:
                return dispatcher_run(run_attempt=1)
            return [{
                "user": {"login": "github-actions[bot]", "type": "Bot"},
                "body": signed_marker(payload),
            }]

    with pytest.raises(transport.DispatchError, match="DISPATCH_JOURNAL_PROVENANCE_INVALID"):
        transport.list_reservations(FakeClient(), 41)


def test_missing_or_rotated_journal_key_cannot_authorize_a_reservation(monkeypatch):
    monkeypatch.delenv(transport.JOURNAL_KEY_ENV, raising=False)
    with pytest.raises(transport.DispatchError, match="DISPATCH_JOURNAL_KEY_MISSING_OR_INVALID"):
        transport.list_reservations(object(), 41)

    monkeypatch.setenv(transport.JOURNAL_KEY_ENV, journal_test_key())
    payload = {
        "schema": 2,
        "role": "qa",
        "pr_number": 41,
        "head_sha": HEAD,
        "run_id": "12345",
        "attempt": 1,
    }
    rotated = hashlib.sha256(b"rotated test key").hexdigest()
    marker = signed_marker(payload, key=rotated)

    class FakeClient:
        def get(self, _):
            return [{"user": {"login": "github-actions[bot]", "type": "Bot"}, "body": marker}]

    assert transport.list_reservations(FakeClient(), 41) == []


def test_reconcile_holds_before_github_reads_when_journal_key_is_missing(monkeypatch):
    monkeypatch.delenv(transport.JOURNAL_KEY_ENV, raising=False)

    with pytest.raises(transport.DispatchError, match="DISPATCH_JOURNAL_KEY_MISSING_OR_INVALID"):
        transport.reconcile(
            client=object(),
            input_pr="41",
            run_number=7,
            controller_run_id="12345",
            source_sha=OTHER_HEAD,
        )


@pytest.mark.parametrize(
    "run_override",
    [
        {"path": ".github/workflows/another.yml"},
        {"repository": {"full_name": "another/repository"}},
        {"head_branch": "candidate-branch"},
        {"event": "pull_request"},
        {"id": 12346},
        {"run_attempt": 2},
    ],
)
def test_signed_journal_rejects_invalid_dispatcher_provenance_fields(monkeypatch, run_override):
    monkeypatch.setenv(transport.JOURNAL_KEY_ENV, journal_test_key())
    payload = {
        "schema": 2,
        "role": "qa",
        "pr_number": 41,
        "head_sha": HEAD,
        "run_id": "12345",
        "attempt": 1,
    }

    class FakeClient:
        def get(self, path):
            if "/actions/runs/" in path:
                return dispatcher_run(**run_override)
            return [{
                "user": {"login": "github-actions[bot]", "type": "Bot"},
                "body": signed_marker(payload),
            }]

    with pytest.raises(transport.DispatchError, match="DISPATCH_JOURNAL_PROVENANCE_INVALID"):
        transport.list_reservations(FakeClient(), 41)


def test_role_run_matching_accepts_api_workflow_name_with_display_title():
    expected = policy.run_name("source_review", 41, HEAD)
    run = {
        "id": 987,
        "name": transport.WORKFLOWS["source_review"]["name"],
        "display_title": expected,
        "path": transport.WORKFLOWS["source_review"]["path"],
        "event": "workflow_dispatch",
        "head_branch": "main",
        "head_repository": {"full_name": REPOSITORY},
        "head_sha": OTHER_HEAD,
        "status": "completed",
        "conclusion": "success",
    }

    class FakeClient:
        def get(self, path):
            if path.endswith("/pulls/41"):
                return {"number": 41, "created_at": "2026-10-21T00:00:00Z"}
            return {"workflow_runs": [run]}

    matches = transport.list_role_runs(FakeClient(), 12, "source_review", 41, HEAD)

    assert matches == [run]


@pytest.mark.parametrize("role", ["qa", "release"])
def test_qa_release_history_queries_only_manual_dispatch_events(role):
    """Filtering excludes irrelevant cron/workflow_run backlog without losing qualifying App runs."""
    paths = []

    class FakeClient:
        def get(self, path):
            paths.append(path)
            if path.endswith("/pulls/41"):
                return {"number": 41, "created_at": "2026-10-21T00:00:00Z"}
            return {"workflow_runs": []}

    assert transport.list_role_runs(FakeClient(), 12, role, 41, HEAD) == []
    role_paths = [p for p in paths if "/actions/workflows/12/runs" in p]
    assert len(role_paths) == 1
    assert "&event=workflow_dispatch" in role_paths[0]
    assert "created=" in role_paths[0]


def test_source_review_history_keeps_both_supported_trigger_types():
    """Source review permits manual and workflow_run; never narrow it to manual-only."""
    paths = []

    class FakeClient:
        def get(self, path):
            paths.append(path)
            if path.endswith("/pulls/41"):
                return {"number": 41, "created_at": "2026-10-21T00:00:00Z"}
            return {"workflow_runs": []}

    assert transport.list_role_runs(FakeClient(), 12, "source_review", 41, HEAD) == []
    assert all("event=workflow_dispatch" not in p for p in paths)


@pytest.mark.parametrize(
    ("role", "reviewer_status", "role_statuses", "previous", "expected"),
    [
        ("source_review", "MISSING", {}, [], 1),
        ("source_review", "STALE", {"source_review": "success"}, [1], 2),
    ],
)
def test_dispatch_attempts_allow_only_initial_review_and_one_verified_refresh(
    role, reviewer_status, role_statuses, previous, expected
):
    assert transport.next_dispatch_attempt(role, reviewer_status, role_statuses, previous) == expected


@pytest.mark.parametrize(
    ("role", "reviewer_status", "role_statuses", "previous", "reason"),
    [
        ("source_review", "MISSING", {}, [1], "DISPATCH_RESERVATION_UNCERTAIN"),
        ("source_review", "STALE", {"source_review": "success"}, [1, 2], "DISPATCH_RETRY_LIMIT_REACHED"),
        ("qa", "PASS", {}, [1], "DISPATCH_RESERVATION_UNCERTAIN"),
        ("release", "PASS", {}, [1], "DISPATCH_RESERVATION_UNCERTAIN"),
    ],
)
def test_dispatch_attempts_never_blindly_repeat(role, reviewer_status, role_statuses, previous, reason):
    with pytest.raises(transport.DispatchError, match=reason):
        transport.next_dispatch_attempt(role, reviewer_status, role_statuses, previous)


@pytest.mark.parametrize("expired", [False, True])
def test_expired_source_review_artifact_is_identified_for_bounded_refresh(expired):
    module = source_review_module()

    class FakeClient:
        token = "test-token"

        def get(self, _):
            return {
                "artifacts": [
                    {
                        "id": 42,
                        "name": f"wow-v17-independent-source-review-41-{HEAD}-987-1",
                        "expired": expired,
                        "created_at": (
                            datetime.now(timezone.utc) - timedelta(minutes=31)
                        ).isoformat(),
                        "workflow_run": {"id": 987},
                    }
                ]
            }

    source_run = {
        "id": 987,
        "run_attempt": 1,
        "path": transport.WORKFLOWS["source_review"]["path"],
        "event": "workflow_dispatch",
        "head_branch": "main",
        "head_repository": {"full_name": REPOSITORY},
        "head_sha": OTHER_HEAD,
        "status": "completed",
        "conclusion": "success",
    }

    result = transport.source_review_status(
        FakeClient(),
        module=module,
        pr=make_pull(),
        files=[make_file()],
        source_runs=[source_run],
    )

    assert result == "STALE"


def test_missing_source_review_without_a_prior_run_can_be_dispatched():
    module = source_review_module()

    class FakeClient:
        token = "test-token"

        def get(self, _):
            return {"artifacts": []}

    result = transport.source_review_status(
        FakeClient(),
        module=module,
        pr=make_pull(),
        files=[make_file()],
        source_runs=[],
    )

    assert result == "MISSING"


def test_zero_job_action_required_source_review_is_recoverable_but_not_success():
    module = source_review_module()
    source_run = {
        "id": 987,
        "name": transport.WORKFLOWS["source_review"]["name"],
        "display_title": policy.run_name("source_review", 41, HEAD),
        "path": transport.WORKFLOWS["source_review"]["path"],
        "event": "workflow_run",
        "head_branch": "main",
        "head_repository": {"full_name": REPOSITORY},
        "head_sha": OTHER_HEAD,
        "status": "completed",
        "conclusion": "action_required",
        "created_at": "2026-10-21T04:00:00Z",
        "run_attempt": 1,
    }

    class FakeClient:
        token = "test-token"

        def get(self, path):
            if path.startswith("/repos/") and "/actions/artifacts" in path:
                return {"artifacts": []}
            if "/actions/runs/987/jobs" in path:
                return {"total_count": 0, "jobs": []}
            pytest.fail(f"unexpected API request: {path}")

    status = transport.source_review_status(
        FakeClient(),
        module=module,
        pr=make_pull(),
        files=[make_file()],
        source_runs=[source_run],
    )
    assert status == "ACTION_REQUIRED_ZERO_JOBS"

    decision = classify(
        source_review=status,
        check_runs=required_ci_checks(),
        completed_role_runs={"source_review": "action_required"},
    )
    assert decision["action"] == "DISPATCH"
    assert decision["role"] == "source_review"
    assert decision["findings"] == []


def test_fresh_but_unresolvable_source_review_artifact_is_not_retried():
    module = source_review_module()

    class FakeClient:
        token = "test-token"

        def get(self, _):
            return {
                "artifacts": [
                    {
                        "id": 42,
                        "name": f"wow-v17-independent-source-review-41-{HEAD}-987-1",
                        "expired": False,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "workflow_run": {"id": 987},
                    }
                ]
            }

    source_run = {
        "id": 987,
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "success",
    }

    result = transport.source_review_status(
        FakeClient(),
        module=module,
        pr=make_pull(),
        files=[make_file()],
        source_runs=[source_run],
    )

    assert result == "INVALID"


def test_expired_artifact_requires_matching_run_attempt_and_metadata():
    module = source_review_module()

    class FakeClient:
        token = "test-token"

        def get(self, _):
            return {
                "artifacts": [
                    {
                        "id": 42,
                        "name": f"wow-v17-independent-source-review-41-{HEAD}-987-2",
                        "expired": True,
                        "created_at": (
                            datetime.now(timezone.utc) - timedelta(minutes=31)
                        ).isoformat(),
                        "workflow_run": {"id": 987},
                    }
                ]
            }

    source_run = {
        "id": 987,
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "success",
    }

    result = transport.source_review_status(
        FakeClient(),
        module=module,
        pr=make_pull(),
        files=[make_file()],
        source_runs=[source_run],
    )

    assert result == "INVALID"


def test_multiple_stale_review_artifacts_are_ambiguous_and_hold():
    module = source_review_module()
    old_date = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()

    class FakeClient:
        token = "test-token"

        def get(self, _):
            return {
                "artifacts": [
                    {
                        "id": artifact_id,
                        "name": f"wow-v17-independent-source-review-41-{HEAD}-{run_id}-1",
                        "expired": False,
                        "created_at": old_date,
                        "workflow_run": {"id": run_id},
                    }
                    for artifact_id, run_id in ((42, 987), (43, 988))
                ]
            }

    source_runs = [
        {"id": run_id, "run_attempt": 1, "status": "completed", "conclusion": "success"}
        for run_id in (987, 988)
    ]

    result = transport.source_review_status(
        FakeClient(),
        module=module,
        pr=make_pull(),
        files=[make_file()],
        source_runs=source_runs,
    )

    assert result == "INVALID"


def test_ambiguous_non_idempotent_write_is_not_retried(monkeypatch):
    attempts = []

    def fail(_request, timeout):
        attempts.append(timeout)
        raise URLError("connection timed out")

    monkeypatch.setattr(transport, "urlopen", fail)

    with pytest.raises(transport.AmbiguousWrite, match="GITHUB_WRITE_RESULT_UNCERTAIN"):
        transport.GitHub("test-token").request("POST", "/repos/example/repo/actions/workflows/1/dispatches", {})

    assert len(attempts) == 1


def test_reconcile_reserves_before_dispatch(monkeypatch):
    monkeypatch.setenv(transport.JOURNAL_KEY_ENV, journal_test_key())
    events = []
    pr = make_pull()
    workflows = {
        role: {"id": index + 1, **details}
        for index, (role, details) in enumerate(transport.WORKFLOWS.items())
    }
    checks = all_ci_checks()
    monkeypatch.setattr(transport, "_owner", lambda _: (OWNER_ID, OWNER))
    monkeypatch.setattr(transport, "list_workflows", lambda _: workflows)
    monkeypatch.setattr(transport, "count_wip", lambda *_: {role: 0 for role in transport.WORKFLOWS})
    monkeypatch.setattr(transport, "_source_review_module", lambda: object())
    monkeypatch.setattr(transport, "_target_list", lambda *_: [pr])
    monkeypatch.setattr(transport, "_target_pull", lambda *_: pr)
    monkeypatch.setattr(transport, "pull_files", lambda *_: [make_file()])
    monkeypatch.setattr(transport, "check_runs", lambda *_: checks)
    monkeypatch.setattr(transport, "pull_reviews", lambda *_: [])
    monkeypatch.setattr(transport, "legacy_certifier_ancillary_hold", lambda *_: None)
    history_reads = []
    monkeypatch.setattr(transport, "matching_role_runs",
                        lambda *_args: history_reads.append(_args[2]) or {})
    monkeypatch.setattr(transport, "source_review_status", lambda *_args, **_kwargs: "PASS")
    monkeypatch.setattr(transport, "list_reservations", lambda *_: [])
    monkeypatch.setattr(transport, "reserve_dispatch", lambda *_args, **_kwargs: events.append("reserve"))
    monkeypatch.setattr(transport, "dispatch", lambda *_args, **_kwargs: events.append("dispatch"))

    result = transport.reconcile(
        client=object(),
        input_pr="41",
        run_number=7,
        controller_run_id="12345",
        source_sha=OTHER_HEAD,
    )

    assert history_reads == [41]
    assert events == ["reserve", "dispatch"]
    assert result["dispatch_count"] == 1
    assert result["decisions"][0]["role"] == "qa"
    assert result["can_execute"] is False
    assert result["merge_or_release_authorized"] is False



def test_reconcile_completed_authorized_apps_skip_history_without_false_green(monkeypatch):
    """Valid exact-head App receipts establish DONE without scanning old GitHub role runs."""
    monkeypatch.setenv(transport.JOURNAL_KEY_ENV, journal_test_key())
    pr = make_pull()
    workflows = {
        role: {"id": index + 1, **details}
        for index, (role, details) in enumerate(transport.WORKFLOWS.items())
    }
    checks = all_ci_checks() + [
        make_check(policy.QA_CHECK, app_id=policy.QA_APP_ID, id=20),
        make_check(policy.RELEASE_CHECK, app_id=policy.RELEASE_APP_ID, id=21),
    ]
    monkeypatch.setattr(transport, "_owner", lambda _: (OWNER_ID, OWNER))
    monkeypatch.setattr(transport, "list_workflows", lambda _: workflows)
    monkeypatch.setattr(transport, "count_wip", lambda *_: {role: 0 for role in transport.WORKFLOWS})
    monkeypatch.setattr(transport, "_source_review_module", lambda: object())
    monkeypatch.setattr(transport, "_target_list", lambda *_: [pr])
    monkeypatch.setattr(transport, "_target_pull", lambda *_: pr)
    monkeypatch.setattr(transport, "pull_files", lambda *_: [make_file()])
    monkeypatch.setattr(transport, "check_runs", lambda *_: checks)
    monkeypatch.setattr(transport, "pull_reviews", lambda *_: [])
    monkeypatch.setattr(transport, "matching_role_runs",
                        lambda *_: pytest.fail("no history scan for authentic completed App receipts"))
    monkeypatch.setattr(transport, "dispatch",
                        lambda *_args, **_kwargs: pytest.fail("no dispatch after DONE"))
    result = transport.reconcile(
        client=object(),
        input_pr="41",
        run_number=7,
        controller_run_id="12345",
        source_sha=OTHER_HEAD,
        zero_credit_mode=True,
    )
    assert result["dispatch_count"] == 0
    assert result["decisions"][0]["action"] == "DONE"
    assert result["can_execute"] is False


def test_reconcile_does_not_overfill_singleton_role_workflow_queue(monkeypatch):
    monkeypatch.setenv(transport.JOURNAL_KEY_ENV, journal_test_key())
    events = []
    first = make_pull()
    second = {**make_pull(), "number": 42}
    workflows = {
        role: {"id": index + 1, **details}
        for index, (role, details) in enumerate(transport.WORKFLOWS.items())
    }
    monkeypatch.setattr(transport, "_owner", lambda _: (OWNER_ID, OWNER))
    monkeypatch.setattr(transport, "list_workflows", lambda _: workflows)
    monkeypatch.setattr(transport, "count_wip", lambda *_: {role: 0 for role in transport.WORKFLOWS})
    monkeypatch.setattr(transport, "_source_review_module", lambda: object())
    monkeypatch.setattr(transport, "_target_list", lambda *_: [first, second])
    monkeypatch.setattr(transport, "_target_pull", lambda _client, number, *_: first if number == 41 else second)
    monkeypatch.setattr(transport, "pull_files", lambda *_: [make_file()])
    monkeypatch.setattr(transport, "check_runs", lambda *_: all_ci_checks())
    monkeypatch.setattr(transport, "pull_reviews", lambda *_: [])
    monkeypatch.setattr(transport, "legacy_certifier_ancillary_hold", lambda *_: None)
    monkeypatch.setattr(transport, "matching_role_runs", lambda *_: {})
    monkeypatch.setattr(transport, "source_review_status", lambda *_args, **_kwargs: "PASS")
    monkeypatch.setattr(transport, "list_reservations", lambda *_: [])
    monkeypatch.setattr(transport, "reserve_dispatch", lambda *_args, **_kwargs: events.append("reserve"))
    monkeypatch.setattr(transport, "dispatch", lambda *_args, **_kwargs: events.append("dispatch"))

    result = transport.reconcile(
        client=object(),
        input_pr=None,
        run_number=7,
        controller_run_id="12345",
        source_sha=OTHER_HEAD,
    )

    assert events == ["reserve", "dispatch"]
    assert result["dispatch_count"] == 1
    assert result["decisions"][1]["findings"] == ["ROLE_WIP_LIMIT_REACHED"]


def test_reconcile_does_not_fetch_reviewer_artifact_before_ci_is_ready(monkeypatch):
    monkeypatch.setenv(transport.JOURNAL_KEY_ENV, journal_test_key())
    pr = make_pull()
    workflows = {
        role: {"id": index + 1, **details}
        for index, (role, details) in enumerate(transport.WORKFLOWS.items())
    }
    monkeypatch.setattr(transport, "_owner", lambda _: (OWNER_ID, OWNER))
    monkeypatch.setattr(transport, "list_workflows", lambda _: workflows)
    monkeypatch.setattr(transport, "count_wip", lambda *_: {role: 0 for role in transport.WORKFLOWS})
    monkeypatch.setattr(transport, "_source_review_module", lambda: object())
    monkeypatch.setattr(transport, "_target_list", lambda *_: [pr])
    monkeypatch.setattr(transport, "_target_pull", lambda *_: pr)
    monkeypatch.setattr(transport, "pull_files", lambda *_: [make_file()])
    monkeypatch.setattr(transport, "check_runs", lambda *_: [])
    monkeypatch.setattr(transport, "pull_reviews", lambda *_: [])
    monkeypatch.setattr(transport, "legacy_certifier_ancillary_hold", lambda *_: None)
    monkeypatch.setattr(transport, "matching_role_runs",
                        lambda *_: pytest.fail("no role history for statically missing CI"))
    monkeypatch.setattr(transport, "list_reservations", lambda *_: [])
    monkeypatch.setattr(
        transport,
        "source_review_status",
        lambda *_args, **_kwargs: pytest.fail("source review must not be fetched before CI"),
    )

    result = transport.reconcile(
        client=object(),
        input_pr="41",
        run_number=7,
        controller_run_id="12345",
        source_sha=OTHER_HEAD,
    )

    assert result["dispatch_count"] == 0
    assert result["decisions"][0]["findings"][0].startswith("REQUIRED_CHECK_MISSING:")


def test_reconcile_does_not_dispatch_when_reservation_is_ambiguous(monkeypatch):
    monkeypatch.setenv(transport.JOURNAL_KEY_ENV, journal_test_key())
    events = []
    pr = make_pull()
    workflows = {
        role: {"id": index + 1, **details}
        for index, (role, details) in enumerate(transport.WORKFLOWS.items())
    }
    monkeypatch.setattr(transport, "_owner", lambda _: (OWNER_ID, OWNER))
    monkeypatch.setattr(transport, "list_workflows", lambda _: workflows)
    monkeypatch.setattr(transport, "count_wip", lambda *_: {role: 0 for role in transport.WORKFLOWS})
    monkeypatch.setattr(transport, "_source_review_module", lambda: object())
    monkeypatch.setattr(transport, "_target_list", lambda *_: [pr])
    monkeypatch.setattr(transport, "_target_pull", lambda *_: pr)
    monkeypatch.setattr(transport, "pull_files", lambda *_: [make_file()])
    monkeypatch.setattr(transport, "check_runs", lambda *_: all_ci_checks())
    monkeypatch.setattr(transport, "pull_reviews", lambda *_: [])
    monkeypatch.setattr(transport, "legacy_certifier_ancillary_hold", lambda *_: None)
    monkeypatch.setattr(transport, "matching_role_runs", lambda *_: {})
    monkeypatch.setattr(transport, "source_review_status", lambda *_args, **_kwargs: "PASS")
    monkeypatch.setattr(transport, "list_reservations", lambda *_: [])

    def ambiguous_reservation(*_args, **_kwargs):
        events.append("reserve")
        raise transport.AmbiguousWrite("GITHUB_WRITE_RESULT_UNCERTAIN")

    monkeypatch.setattr(transport, "reserve_dispatch", ambiguous_reservation)
    monkeypatch.setattr(
        transport,
        "dispatch",
        lambda *_args, **_kwargs: events.append("dispatch"),
    )

    result = transport.reconcile(
        client=object(),
        input_pr="41",
        run_number=7,
        controller_run_id="12345",
        source_sha=OTHER_HEAD,
    )

    assert events == ["reserve"]
    assert result["dispatch_count"] == 0
    assert result["decisions"][0]["findings"] == ["GITHUB_WRITE_RESULT_UNCERTAIN"]


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_workflow_lookup_only_treats_http_404_as_not_installed(status):
    class FakeClient:
        def request(self, *_args, **_kwargs):
            return status, None

    if status == 404:
        assert transport.main_workflow_available(FakeClient(), transport.WORKFLOWS["source_review"]) is None
    else:
        with pytest.raises(transport.DispatchError, match=f"GITHUB_READ_FAILED:{status}"):
            transport.main_workflow_available(FakeClient(), transport.WORKFLOWS["source_review"])


def test_workflow_lookup_preserves_transport_failure():
    class FakeClient:
        def request(self, *_args, **_kwargs):
            raise transport.DispatchError("GITHUB_READ_FAILED")

    with pytest.raises(transport.DispatchError, match="GITHUB_READ_FAILED"):
        transport.main_workflow_available(FakeClient(), transport.WORKFLOWS["qa"])


def test_source_review_api_failure_keeps_http_status_as_a_typed_hold():
    class FakeClient:
        token = "test-token"

        def get(self, _path):
            raise transport.DispatchError("GITHUB_READ_FAILED:403")

    module = type(
        "FakeSourceReviewModule",
        (),
        {
            "SourceReviewError": FakeSourceReviewError,
            "resolve_current_source_review": staticmethod(
                lambda **kwargs: kwargs["get_json"]("/actions/runs/12345")
            ),
        },
    )

    with pytest.raises(transport.DispatchError, match="GITHUB_READ_FAILED:403"):
        transport.source_review_status(
            FakeClient(),
            module=module,
            pr=make_pull(),
            files=[make_file()],
        )


def test_same_sha_ancillary_action_required_is_a_typed_certifier_hold():
    certifier = {
        "id": 800,
        "path": transport.LEGACY_CERTIFIER_PATH,
        "head_sha": HEAD,
        "head_repository": {"full_name": REPOSITORY},
        "pull_requests": [41],
        "created_at": "2026-10-21T03:00:00Z",
        "event": "pull_request_target",
        "status": "completed",
        "conclusion": "failure",
    }
    ancillary_path = transport.LEGACY_CERTIFIER_ANCILLARY[
        "wow-v17-release-production-verification-agent"
    ]
    same_sha_runs = [
        certifier,
        {
            "id": 700,
            "path": ancillary_path,
            "head_sha": HEAD,
            "head_repository": {"full_name": REPOSITORY},
            "pull_requests": [41],
            "created_at": "2026-10-21T02:00:00Z",
            "event": "pull_request",
            "status": "completed",
            "conclusion": "success",
        },
        {
            "id": 801,
            "path": ancillary_path,
            "head_sha": HEAD,
            "head_repository": {"full_name": REPOSITORY},
            "pull_requests": [41],
            "created_at": "2026-10-21T03:01:00Z",
            "event": "pull_request",
            "status": "completed",
            "conclusion": "action_required",
        },
    ]

    class FakeClient:
        def get(self, path):
            if "/actions/runs/801/jobs" in path:
                return {"total_count": 0, "jobs": []}
            return {"workflow_runs": same_sha_runs}

    finding = transport.legacy_certifier_ancillary_hold(FakeClient(), HEAD, 41)
    assert finding == (
        "ANCILLARY_CI_EVENT_POLICY_HOLD:"
        "workflow=wow-v17-release-production-verification-agent:run_id=801:"
        "owner_action=review_protected_certifier_failure"
    )

    blocked = classify(check_runs=all_ci_checks(), ancillary_ci_hold=finding)
    assert blocked["action"] == "HOLD"
    assert blocked["findings"] == [finding]

    failed_required = all_ci_checks()
    failed_required[0] = make_check(policy.QA_EVIDENCE_CHECKS[0], conclusion="failure")
    genuine_failure = classify(check_runs=failed_required, ancillary_ci_hold=finding)
    assert genuine_failure["action"] == "HOLD"
    assert genuine_failure["findings"] == [
        f"REQUIRED_CHECK_FAILED:{policy.QA_EVIDENCE_CHECKS[0]}"
    ]

    reviewer_first = classify(
        source_review="MISSING",
        check_runs=required_ci_checks(),
        ancillary_ci_hold=finding,
    )
    assert reviewer_first["action"] == "DISPATCH"
    assert reviewer_first["role"] == "source_review"

    downstream = classify(
        source_review="PASS",
        check_runs=all_ci_checks(),
        ancillary_ci_hold=finding,
    )
    assert downstream["action"] == "HOLD"
    assert downstream["findings"] == [finding]


def test_ancillary_certifier_hold_requires_same_pr_and_head_identity():
    class FakeClient:
        def get(self, path):
            if "/jobs?" in path:
                pytest.fail("runs for another PR must not trigger this hold")
            return {"workflow_runs": [
                {
                    "id": 800,
                    "path": transport.LEGACY_CERTIFIER_PATH,
                    "head_sha": HEAD,
                    "head_repository": {"full_name": REPOSITORY},
                    "pull_requests": [42],
                    "created_at": "2026-10-21T03:00:00Z",
                    "event": "pull_request_target",
                    "status": "completed",
                    "conclusion": "failure",
                },
            ]}

    assert transport.legacy_certifier_ancillary_hold(FakeClient(), HEAD, 41) is None


def test_ancillary_action_required_does_not_block_source_review_dispatch(monkeypatch):
    monkeypatch.setenv(transport.JOURNAL_KEY_ENV, journal_test_key())
    pr = make_pull()
    workflows = {
        role: {"id": index + 1, **details}
        for index, (role, details) in enumerate(transport.WORKFLOWS.items())
    }
    calls = []
    monkeypatch.setattr(transport, "_owner", lambda _: (OWNER_ID, OWNER))
    monkeypatch.setattr(transport, "list_workflows", lambda _: workflows)
    monkeypatch.setattr(transport, "count_wip", lambda *_: {role: 0 for role in transport.WORKFLOWS})
    monkeypatch.setattr(transport, "_source_review_module", lambda: object())
    monkeypatch.setattr(transport, "_target_list", lambda *_: [pr])
    monkeypatch.setattr(transport, "_target_pull", lambda *_: pr)
    monkeypatch.setattr(transport, "pull_files", lambda *_: [make_file()])
    monkeypatch.setattr(transport, "check_runs", lambda *_: all_ci_checks())
    monkeypatch.setattr(transport, "pull_reviews", lambda *_: [])
    monkeypatch.setattr(
        transport,
        "legacy_certifier_ancillary_hold",
        lambda *_: pytest.fail("ancillary gate must not be queried before reviewer PASS"),
    )
    monkeypatch.setattr(transport, "matching_role_runs", lambda *_: {})
    monkeypatch.setattr(transport, "list_reservations", lambda *_: [])
    monkeypatch.setattr(
        transport,
        "source_review_status",
        lambda *_args, **_kwargs: "MISSING",
    )
    monkeypatch.setattr(transport, "reserve_dispatch", lambda *_args, **_kwargs: calls.append("reserve"))
    monkeypatch.setattr(transport, "dispatch", lambda *_args, **_kwargs: calls.append("dispatch"))

    result = transport.reconcile(
        client=object(),
        input_pr="41",
        run_number=7,
        controller_run_id="12345",
        source_sha=OTHER_HEAD,
    )

    assert result["dispatch_count"] == 1
    assert calls == ["reserve", "dispatch"]
    assert result["decisions"][0]["role"] == "source_review"


def test_controller_workflow_is_scheduled_manual_and_singleton():
    workflow = (ROOT / ".github/workflows/wow-v17-governance-dispatcher.yml").read_text()

    assert "schedule:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "workflow_run:" not in workflow
    assert "group: wow-v17-governance-dispatcher" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "ref: ${{ github.sha }}" in workflow
    assert "actions: write" in workflow
    assert "checks: read" in workflow
    assert "contents: read" in workflow
    assert "pull-requests: read" in workflow
    assert "issues: write" in workflow
    assert "environment: wow-dispatch" in workflow
    assert "WOW_DISPATCH_JOURNAL_HMAC_KEY" in workflow
    assert "checks: write" not in workflow
    assert "pull-requests: write" not in workflow


def test_controller_workflow_is_valid_yaml_and_has_only_main_safe_triggers():
    workflow = yaml.load(
        (ROOT / ".github/workflows/wow-v17-governance-dispatcher.yml").read_text(),
        Loader=yaml.BaseLoader,
    )

    assert set(workflow["on"]) == {"schedule", "workflow_dispatch"}
    assert workflow["on"]["schedule"] == [{"cron": "*/15 * * * *"}]
    assert workflow["permissions"]["actions"] == "write"
    assert workflow["permissions"]["checks"] == "read"
    assert workflow["jobs"]["route"]["if"].endswith("github.ref == 'refs/heads/main'")
    assert workflow["jobs"]["route"]["environment"] == "wow-dispatch"


@pytest.mark.parametrize(
    "workflow_name",
    ["wow-v17-independent-qa-check.yml", "wow-v17-release-authority-check.yml"],
)
def test_existing_manual_qa_release_accept_exact_optional_head_sha(workflow_name):
    workflow = (ROOT / ".github/workflows" / workflow_name).read_text()

    assert "head_sha:" in workflow
    assert "required: false" in workflow
    assert "INPUT_HEAD_SHA: ${{ inputs.head_sha }}" in workflow
    assert "INPUT_HEAD_SHA_INVALID" in workflow
    assert "INPUT_HEAD_SHA_STALE" in workflow
    assert "head=${{ inputs.head_sha || 'AUTO' }}" in workflow
