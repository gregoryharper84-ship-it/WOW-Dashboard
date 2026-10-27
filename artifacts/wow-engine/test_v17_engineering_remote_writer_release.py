"""Exact production-shaped, zero-network reconciliation and single-writer tests."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from v17 import engineering_remote_fallback as ledger
from v17 import engineering_remote_writer_release as release

SHA = "a" * 40
PROOF = {
    "status": "CURSOR_TERMINAL_STATUS_VERIFIED",
    "terminal_status": "FINISHED", "agent_status": "IDLE",
    "agent_id": "bc-c8a71883-d1ab-593a-8e92-2f7779436cb4",
    "run_id": "run-7b568d98-de5b-4cce-b2a4-e7743fd43787",
    "pr_number": 2090, "can_execute": False,
}


def original():
    claim = ledger.receipt(
        "CURSOR_AGENT_ENQUEUED",
        provider="cursor", incident_id="2054", hold=True, main_sha=SHA,
        lease_group="PR_2090", pr_number="2090", pr_sha="b" * 40,
        cursor_agent_id=PROOF["agent_id"],
        cursor_run_id=PROOF["run_id"],
        worker_id=release.EXPECTED_WORKER_ID,
    )
    return {
        "id": release.EXPECTED_COMMENT_ID,
        "body": ledger._body(claim),
        "user": {"login": "github-actions[bot]"},
    }


def run(**changes):
    return {
        "path": release.WORKFLOW, "event": "workflow_dispatch",
        "status": "completed", "conclusion": "success",
        "head_branch": "main", "head_sha": SHA, "run_attempt": 1,
        "repository": {"full_name": ledger.REPOSITORY},
        **changes,
    }


def released_comments(**changes):
    old = original()
    doc = {
        "status": "REMOTE_RELEASE_PENDING_WORKFLOW_SUCCESS",
        "incident_id": "2054",
        "original_comment_id": old["id"],
        "original_digest": release._original_digest(old),
        "provider": "cursor", "agent_id": PROOF["agent_id"],
        "cursor_run_id": PROOF["run_id"],
        "worker_id": release.EXPECTED_WORKER_ID,
        "pr_number": 2090,
        "terminal_status": "FINISHED",
        "agent_status": "IDLE", "workflow_run_id": 99001,
        "run_attempt": 1, "source_sha": SHA,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }
    doc.update(changes)
    receipt = {
        "id": old["id"] + 2,
        "body": f"{release.RELEASE_MARKER}2054 -->\n"
                + json.dumps(doc, sort_keys=True),
        "user": {"login": "github-actions[bot]"},
    }
    return [old, receipt]


def test_verified_immutable_release_is_effective_only_after_workflow_success():
    comments = released_comments()
    with pytest.raises(ledger.ProviderUnavailable, match="REMOTE_RELEASE_RECEIPT_UNVERIFIED"):
        ledger.assert_no_remote_writer(comments)
    with pytest.raises(ledger.ProviderUnavailable, match="REMOTE_RELEASE_RECEIPT_UNVERIFIED"):
        ledger.assert_no_remote_writer(
            comments, get_run=lambda _: run(status="in_progress", conclusion=None))
    ledger.assert_no_remote_writer(comments, get_run=lambda _: run())
    assert ledger.reservations(comments)[0][1]["hold"] is True
    assert ledger.reconcile(
        _FakeAPI(comments), comments, "2054")[0]["hold"] is False
    assert ledger.reservations(comments)[0][1]["hold"] is True


@pytest.mark.parametrize("field,value", [
    ("agent_id", "bc-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    ("cursor_run_id", "new-run"),
    ("worker_id", "another-worker"),
    ("original_digest", "f" * 64),
    ("original_comment_id", 99999),
    ("terminal_status", "CREATING"),
    ("agent_status", "ACTIVE"),
    ("can_execute", True),
    ("terminal_authority", "OTHER"),
    ("source_sha", "invalid"),
    ("run_attempt", 0),
    ("workflow_run_id", False),
])
def test_changed_or_spoofed_release_fails_closed(field, value):
    comments = released_comments(**{field: value})
    with pytest.raises(ledger.ProviderUnavailable):
        ledger.assert_no_remote_writer(comments, get_run=lambda _: run())


@pytest.mark.parametrize("invalid", [
    {"path": "different.yml"}, {"event": "workflow_run"},
    {"status": "in_progress"}, {"conclusion": "failure"},
    {"head_branch": "feature"}, {"head_sha": "b" * 40},
    {"run_attempt": 2}, {"repository": {"full_name": "another/repo"}},
])
def test_only_protected_successful_exact_workflow_run_authorizes_release(invalid):
    with pytest.raises(ledger.ProviderUnavailable):
        ledger.assert_no_remote_writer(
            released_comments(), get_run=lambda _: run(**invalid))


def test_identity_of_release_author_and_original_remain_immutable():
    comments = released_comments()
    comments[1]["user"] = {"login": "owner"}
    with pytest.raises(ledger.ProviderUnavailable):
        ledger.assert_no_remote_writer(comments, get_run=lambda _: run())
    comments = released_comments()
    comments[0]["body"] += " "
    with pytest.raises(ledger.ProviderUnavailable):
        ledger.assert_no_remote_writer(comments, get_run=lambda _: run())
    comments = released_comments()
    comments.append(copy.deepcopy(comments[1]))
    comments[-1]["id"] += 1
    with pytest.raises(ledger.ProviderUnavailable):
        ledger.assert_no_remote_writer(comments, get_run=lambda _: run())


def test_unrelated_active_writer_still_blocks_even_after_valid_terminal_receipt():
    other = ledger.receipt(
        "REMOTE_LAUNCH_RESERVED", provider="copilot",
        main_sha="c" * 40, incident_id="1234", hold=True)
    comments = released_comments() + [
        {"id": 88000, "body": ledger._body(other)}]
    with pytest.raises(ledger.ProviderUnavailable, match="REMOTE_WRITER_RECONCILIATION_REQUIRED"):
        ledger.assert_no_remote_writer(comments, get_run=lambda _: run())


class _FakeAPI:
    def __init__(self, comments=None):
        self.token = "fixture-token"
        self.comments = copy.deepcopy(comments or [original()])
        self.puts = 0

    def pages(self, path):
        assert path == release.LEDGER
        return copy.deepcopy(self.comments)

    def request(self, path, payload=None, method=None):
        if path.endswith("/commits/main"):
            return {"sha": SHA}
        if "/actions/runs/" in path:
            return run()
        if path == release.LEDGER and method == "POST":
            self.puts += 1
            created = {"id": 6450000000, "body": payload["body"],
                       "user": {"login": "github-actions[bot]"}}
            self.comments.append(created)
            return copy.deepcopy(created)
        if path.endswith("/issues/comments/6450000000"):
            return self.comments[-1]
        raise AssertionError((path, payload, method))


def test_protected_finalizer_appends_one_record_never_patches_held_source():
    api = _FakeAPI()
    old = copy.deepcopy(api.comments[0])
    env = {"GITHUB_REPOSITORY": ledger.REPOSITORY, "GITHUB_REF": "refs/heads/main"}
    with patch.dict("os.environ", env), patch(
        "v17.cursor_terminal_status_receipt.collect_receipt", return_value=PROOF,
    ):
        output = release.append_terminal_release(
            api, cursor_token="fixture", run_id=99001,
            run_attempt=1, source_sha=SHA)
    assert output["status"] == "REMOTE_RELEASE_PENDING_WORKFLOW_SUCCESS"
    assert output["can_execute"] is False
    assert api.puts == 1
    assert api.comments[0] == old
    ledger.assert_no_remote_writer(api.comments, get_run=lambda _: run())
    with patch.dict("os.environ", env), patch(
        "v17.cursor_terminal_status_receipt.collect_receipt", return_value=PROOF,
    ), pytest.raises(release.ReleaseBlocked):
        release.append_terminal_release(api, cursor_token="fixture",
                                        run_id=99002, run_attempt=1, source_sha=SHA)
    assert api.puts == 1


def test_invalid_preconditions_never_append_or_release():
    api = _FakeAPI()
    with patch.dict("os.environ", {"GITHUB_REPOSITORY": ledger.REPOSITORY,
                                   "GITHUB_REF": "refs/heads/feature"}), pytest.raises(release.ReleaseBlocked):
        release.append_terminal_release(api, cursor_token="fixture",
                                        run_id=99001, run_attempt=1, source_sha=SHA)
    assert api.puts == 0
    api.comments.append({"id": 888, "body": ledger._body(ledger.receipt(
        "REMOTE_LAUNCH_RESERVED", provider="copilot", hold=True,
        incident_id="1515", main_sha=SHA))})
    with patch.dict("os.environ", {"GITHUB_REPOSITORY": ledger.REPOSITORY,
                                   "GITHUB_REF": "refs/heads/main"}), pytest.raises(release.ReleaseBlocked):
        release.append_terminal_release(api, cursor_token="fixture",
                                        run_id=99001, run_attempt=1, source_sha=SHA)
    assert api.puts == 0


def test_release_workflow_uses_existing_mutex_and_no_new_provider_writer():
    root = Path(__file__).resolve().parents[2]
    text = (root / release.WORKFLOW).read_text()
    assert "group: wow-v17-engineering-implementation-single-writer" in text
    assert "cancel-in-progress: false" in text
    assert "refs/heads/main" in text
    assert "issues: write" in text
    assert "engineering_remote_fallback finalize-cursor" in text
    assert "CURSOR_API_KEY" in text
    assert "workflow_dispatch:" in text
    assert "push:" in text and "branches:" in text and "main" in text
    ledger.assert_no_remote_writer(
        released_comments(), get_run=lambda _: run(event="push"))
    assert "dispatch.*Cursor" not in text
