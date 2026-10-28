"""Offline exact-reservation Cursor terminal proof and append-only release regressions."""
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from v17 import cursor_terminal_status_receipt as probe
from v17 import engineering_remote_fallback as ledger
from v17 import engineering_remote_writer_release as release

CID = 6455426974
INCIDENT = "2020"
PR = 2182
ORIGINAL = "8f33c3688b911201326933c3e3508b6d1f949280"
NEW_HEAD = "2fe07d4dcb3f551e240c9c92ae8a44eac6e89bd3"
MAIN = "3ec2968f524324ec86f9fa501779463b20cdca22"
AGENT = "bc-c9168595-e78f-5142-b75f-67725aeb0a50"
RUN = "run-456052a5-3bd9-4872-98b0-db89858ad245"
WORKER_RUN = 40597017766
WORKER = f"github-actions:{WORKER_RUN}:1"


def immutable():
    return {
        "id": CID,
        "user": {"login": "github-actions[bot]", "id": 41898282},
        "issue_url": f"{probe.GITHUB_API}/repos/{probe.REPOSITORY}/issues/1138",
        "body": ledger._body(ledger.receipt(
            "CURSOR_AGENT_ENQUEUED", incident_id=INCIDENT, provider="cursor",
            lease_group=f"PR_{PR}", main_sha=MAIN, hold=True,
            pr_number=str(PR), pr_sha=ORIGINAL, cursor_agent_id=AGENT,
            cursor_run_id=RUN, worker_id=WORKER,
        )),
    }


class Fixture:
    def __init__(self):
        self.comment = immutable()
        self.origin = {
            "id": WORKER_RUN, "run_attempt": 1, "event": "workflow_dispatch",
            "path": ".github/workflows/wow-v17-engineering-provider-dispatcher.yml",
            "head_sha": MAIN, "head_branch": "main",
            "status": "completed", "conclusion": "success",
            "repository": {"full_name": probe.REPOSITORY},
            "head_repository": {"full_name": probe.REPOSITORY},
        }
        self.agent = {"id": AGENT, "latestRunId": RUN, "status": "IDLE"}
        self.run = {"id": RUN, "agentId": AGENT, "status": "FINISHED"}
        self.pr = {
            "number": PR, "state": "open", "changed_files": 2,
            "head": {"sha": NEW_HEAD, "repo": {"full_name": probe.REPOSITORY}},
            "base": {"ref": "main", "repo": {"full_name": probe.REPOSITORY}},
        }
        self.comparison = {
            "status": "ahead", "behind_by": 0,
            "merge_base_commit": {"sha": ORIGINAL},
        }
        self.files = [
            {"filename": "artifacts/wow-engine/test_v17_provider_manual_lease_concurrency.py"},
            {"filename": ".github/workflows/wow-v17-engineering-provider-dispatcher.yml"},
        ]
        self.pr_reads = 0

    def api(self, url, *, token, basic=False):
        assert token in ("fixture-github", "fixture-cursor")
        prefix = f"{probe.GITHUB_API}/repos/{probe.REPOSITORY}"
        if url.startswith(prefix + "/issues/comments/"):
            return copy.deepcopy(self.comment)
        if url == prefix + f"/actions/runs/{WORKER_RUN}":
            return copy.deepcopy(self.origin)
        if url == f"{probe.CURSOR_API}/agents/{AGENT}":
            assert basic
            return copy.deepcopy(self.agent)
        if url == f"{probe.CURSOR_API}/agents/{AGENT}/runs/{RUN}":
            assert basic
            return copy.deepcopy(self.run)
        if url == prefix + f"/pulls/{PR}":
            self.pr_reads += 1
            return copy.deepcopy(self.pr)
        if url == prefix + f"/pulls/{PR}/files?per_page=100&page=1":
            return copy.deepcopy(self.files)
        if url == prefix + f"/compare/{ORIGINAL}...{NEW_HEAD}":
            return copy.deepcopy(self.comparison)
        raise AssertionError("unrecognized read-only API endpoint: " + url)

    def prove(self):
        return probe.collect_receipt(
            "fixture-cursor", "fixture-github", api=self.api,
            reservation_comment_id=CID,
        )


def test_verified_current_reservation_authenticates_live_run_and_advanced_pr():
    fixture = Fixture()
    result = fixture.prove()
    assert result["status"] == "CURSOR_TERMINAL_STATUS_VERIFIED"
    assert result["terminal_status"] == "FINISHED"
    assert result["agent_status"] == "IDLE"
    assert result["run_id"] == RUN
    assert result["pr_number"] == PR
    assert result["head_sha"] == NEW_HEAD
    assert result["reservation"]["reservation_comment_id"] == CID
    assert result["reservation"]["reservation_sha256"] == hashlib.sha256(
        fixture.comment["body"].encode()).hexdigest()
    assert fixture.pr_reads == 2
    assert result["lease_released"] is False
    assert result["production_verified"] is False
    assert result["can_execute"] is False
    assert result["terminal_authority"] == "V17_TERMINAL_REDUCER"


@pytest.mark.parametrize("change,expected", [
    (lambda f: f.comment["user"].update(login="owner"), "CURSOR_RESERVATION_UNTRUSTED"),
    (lambda f: f.comment["user"].update(id=999), "CURSOR_RESERVATION_UNTRUSTED"),
    (lambda f: f.comment.update(issue_url="https://api.github.com/repos/other/repo/issues/1138"), "CURSOR_RESERVATION_UNTRUSTED"),
    (lambda f: f.origin["head_repository"].update(full_name="attacker/repo"), "CURSOR_RESERVATION_WORKFLOW_UNVERIFIED"),
    (lambda f: f.comment.update(id=CID + 1), "CURSOR_RESERVATION_UNTRUSTED"),
    (lambda f: f.origin.update(head_sha="b" * 40), "CURSOR_RESERVATION_WORKFLOW_UNVERIFIED"),
    (lambda f: f.origin.update(run_attempt=2), "CURSOR_RESERVATION_WORKFLOW_UNVERIFIED"),
    (lambda f: f.origin.update(conclusion="failure"), "CURSOR_RESERVATION_WORKFLOW_UNVERIFIED"),
    (lambda f: f.origin.update(head_branch="feature"), "CURSOR_RESERVATION_WORKFLOW_UNVERIFIED"),
    (lambda f: f.agent.update(status="ACTIVE"), "CURSOR_AGENT_STILL_ACTIVE"),
    (lambda f: f.run.update(status="CREATING"), "CURSOR_TERMINAL_STATUS_UNVERIFIED"),
    (lambda f: f.run.update(id="run-forged"), "CURSOR_RUN_ID_MISMATCH"),
    (lambda f: f.comparison.update(merge_base_commit={"sha": "b" * 40}), "CURSOR_PR_HEAD_NOT_DESCENDANT"),
    (lambda f: f.comparison.update(behind_by=1), "CURSOR_PR_HEAD_NOT_DESCENDANT"),
    (lambda f: f.pr["head"].update(repo={"full_name": "attacker/fork"}), "CURSOR_SOURCE_PR_IDENTITY_INVALID"),
    (lambda f: f.pr["base"].update(ref="other"), "CURSOR_SOURCE_PR_IDENTITY_INVALID"),
    (lambda f: f.pr.update(state="closed"), "CURSOR_SOURCE_PR_IDENTITY_INVALID"),
])
def test_forgery_and_active_run_fail_closed(change, expected):
    fixture = Fixture()
    change(fixture)
    with pytest.raises(probe.ReceiptBlocked, match=expected):
        fixture.prove()


def test_reservation_parser_rejects_modified_lease_issuer_and_authority():
    for field, value in [
        ("hold", False), ("provider", "copilot"), ("lease_group", "PR_9999"),
        ("can_execute", True), ("terminal_authority", "OTHER"),
        ("probability_authority", True), ("cursor_run_id", "injected"),
    ]:
        fixture = Fixture()
        first, raw = fixture.comment["body"].split("\n", 1)
        payload = json.loads(raw)
        payload[field] = value
        fixture.comment["body"] = first + "\n" + json.dumps(payload)
        with pytest.raises(probe.ReceiptBlocked):
            fixture.prove()


def test_unknown_reservation_does_not_fallback_to_legacy_cursor_agent():
    fixture = Fixture()
    with pytest.raises(probe.ReceiptBlocked, match="CURSOR_RESERVATION_UNTRUSTED"):
        probe.collect_receipt(
            "fixture-cursor", "fixture-github", api=fixture.api,
            reservation_comment_id=CID + 1,
        )


def test_probe_workflow_requires_exact_branch_and_numeric_target():
    root = Path(__file__).resolve().parents[2]
    text = (root / ".github/workflows/wow-v17-cursor-terminal-status-receipt.yml").read_text()
    assert "refs/heads/main" in text
    assert "reservation_comment_id:" in text
    assert "required: true" in text
    assert "--reservation-comment-id" in text
    assert "CURSOR_RESERVATION_ID_INVALID" in text
    assert "issues: write" not in text
    assert "CURSOR_API_KEY" in text


def test_original_release_and_second_reservation_remain_distinct_and_fenced():
    from test_v17_engineering_remote_writer_release import released_comments, run
    fixture = Fixture()
    comments = released_comments() + [fixture.comment]
    def get_run(rid):
        assert rid == 99001
        return run()
    with pytest.raises(ledger.ProviderUnavailable, match="REMOTE_WRITER_RECONCILIATION_REQUIRED"):
        ledger.assert_no_remote_writer(comments, get_run=get_run)
    assert release.verified_released_ids(
        comments, ledger.reservations(comments), get_run=get_run,
    ) == {release.EXPECTED_COMMENT_ID}


def test_dynamic_finalizer_does_not_release_an_unverified_or_concurrent_cursor():
    from test_v17_engineering_remote_writer_release import _FakeAPI, released_comments
    fixture = Fixture()
    records = released_comments() + [fixture.comment]
    api = _FakeAPI(records)
    proof = fixture.prove()
    assert proof["agent_id"] != release.EXPECTED_WORKER_ID
    env = {"GITHUB_REPOSITORY": probe.REPOSITORY, "GITHUB_REF": "refs/heads/main"}
    # Different failed identity proofs cannot append an authorization receipt.
    proof["reservation"]["reservation_sha256"] = "b" * 64
    with patch.dict("os.environ", env), patch(
        "v17.cursor_terminal_status_receipt.collect_receipt", return_value=proof,
    ), pytest.raises(release.ReleaseBlocked, match="REMOTE_RELEASE_PROBE_IDENTITY_MISMATCH"):
        release.append_terminal_release(
            api, cursor_token="fixture", run_id=99004, run_attempt=1,
            source_sha="a" * 40, reservation_comment_id=CID,
        )
    assert api.puts == 0
    records.append({"id": CID + 5, "body": ledger._body(ledger.receipt(
        "REMOTE_LAUNCH_RESERVED", incident_id="9090", provider="copilot",
        main_sha="a" * 40, hold=True,
    ))})
    api = _FakeAPI(records)
    with patch.dict("os.environ", env), pytest.raises(
        release.ReleaseBlocked, match="REMOTE_RELEASE_ORIGINAL_RESERVATION_UNVERIFIED",
    ):
        release.append_terminal_release(
            api, cursor_token="fixture", run_id=99005, run_attempt=1,
            source_sha="a" * 40, reservation_comment_id=CID,
        )
    assert api.puts == 0


def test_dynamic_finalizer_appends_new_receipt_only_after_terminal_proof():
    from test_v17_engineering_remote_writer_release import _FakeAPI, released_comments, run
    fixture = Fixture()
    proof = fixture.prove()

    class DynamicAPI(_FakeAPI):
        def request(self, path, payload=None, method=None):
            if path == release.LEDGER and method == "POST":
                self.puts += 1
                created = {
                    "id": 9000000000, "body": payload["body"],
                    "user": {"login": "github-actions[bot]"},
                }
                self.comments.append(created)
                return copy.deepcopy(created)
            if path.endswith("/issues/comments/9000000000"):
                return copy.deepcopy(self.comments[-1])
            return super().request(path, payload, method)

    api = DynamicAPI(released_comments() + [fixture.comment])
    before = copy.deepcopy(api.comments)
    env = {"GITHUB_REPOSITORY": probe.REPOSITORY, "GITHUB_REF": "refs/heads/main"}
    with patch.dict("os.environ", env), patch(
        "v17.cursor_terminal_status_receipt.collect_receipt", return_value=proof,
    ):
        result = release.append_terminal_release(
            api, cursor_token="fixture", run_id=99004, run_attempt=1,
            source_sha="a" * 40, reservation_comment_id=CID,
        )
    assert result["status"] == "REMOTE_RELEASE_PENDING_WORKFLOW_SUCCESS"
    assert result["original_comment_id"] == CID
    assert result["release_comment_id"] == 9000000000
    assert result["can_execute"] is False
    assert result["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert api.comments[:len(before)] == before, "immutable old reservations and releases"
    assert api.puts == 1
    released = release.verified_released_ids(
        api.comments, ledger.reservations(api.comments), get_run=lambda _: run(),
    )
    assert released == {release.EXPECTED_COMMENT_ID, CID}
    ledger.assert_no_remote_writer(api.comments, get_run=lambda _: run())
    with patch.dict("os.environ", env), patch(
        "v17.cursor_terminal_status_receipt.collect_receipt", return_value=proof,
    ), pytest.raises(release.ReleaseBlocked, match="REMOTE_RELEASE_ORIGINAL_RESERVATION_UNVERIFIED"):
        release.append_terminal_release(
            api, cursor_token="fixture", run_id=99005, run_attempt=1,
            source_sha="a" * 40, reservation_comment_id=CID,
        )
    assert api.puts == 1, "no duplicate release"
