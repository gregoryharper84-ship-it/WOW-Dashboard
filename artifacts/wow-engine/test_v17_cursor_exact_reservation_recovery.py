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


def _fresh_cursor_reader(fixture):
    """Provider responses may change *after* the original verified probe."""
    def read(url, *, token, basic=False):
        assert token == "fixture" and basic is True
        if url == f"{probe.CURSOR_API}/agents/{AGENT}":
            return copy.deepcopy(fixture.agent)
        if url == f"{probe.CURSOR_API}/agents/{AGENT}/runs/{RUN}":
            return copy.deepcopy(fixture.run)
        raise AssertionError("unexpected Cursor final witness URL: " + url)
    return read


def _dynamic_release_api(fixture):
    from test_v17_engineering_remote_writer_release import _FakeAPI, released_comments

    class DynamicAPI(_FakeAPI):
        def request(self, path, payload=None, method=None):
            if path == f"/repos/{release.REPOSITORY}/pulls/{PR}":
                return copy.deepcopy(fixture.pr)
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

    return DynamicAPI(released_comments() + [fixture.comment])


@pytest.mark.parametrize("failure,code", [
    ("pr_head_moved", "REMOTE_RELEASE_PR_MOVED_AFTER_PROBE"),
    ("pr_closed", "REMOTE_RELEASE_PR_MOVED_AFTER_PROBE"),
    ("pr_moved_to_fork", "REMOTE_RELEASE_PR_MOVED_AFTER_PROBE"),
    ("cursor_resumed", "REMOTE_RELEASE_PROVIDER_REACTIVATED"),
    ("latest_run_changed", "REMOTE_RELEASE_PROVIDER_REACTIVATED"),
    ("run_not_terminal", "REMOTE_RELEASE_PROVIDER_REACTIVATED"),
    ("run_agent_changed", "REMOTE_RELEASE_PROVIDER_REACTIVATED"),
])
def test_fresh_finalizer_witness_rejects_state_change_without_appending(failure, code):
    fixture = Fixture()
    original_proof = fixture.prove()
    api = _dynamic_release_api(fixture)
    if failure == "pr_head_moved":
        fixture.pr["head"]["sha"] = "c" * 40
    elif failure == "pr_closed":
        fixture.pr["state"] = "closed"
    elif failure == "pr_moved_to_fork":
        fixture.pr["head"]["repo"]["full_name"] = "attacker/repo"
    elif failure == "cursor_resumed":
        fixture.agent["status"] = "ACTIVE"
    elif failure == "latest_run_changed":
        fixture.agent["latestRunId"] = "run-newer"
    elif failure == "run_not_terminal":
        fixture.run["status"] = "CREATING"
    elif failure == "run_agent_changed":
        fixture.run["agentId"] = "bc-attacker"
    env = {"GITHUB_REPOSITORY": probe.REPOSITORY, "GITHUB_REF": "refs/heads/main"}
    with patch.dict("os.environ", env), patch(
        "v17.cursor_terminal_status_receipt.collect_receipt", return_value=original_proof,
    ), patch(
        "v17.cursor_terminal_status_receipt.api_json", side_effect=_fresh_cursor_reader(fixture),
    ), pytest.raises(release.ReleaseBlocked, match=code):
        release.append_terminal_release(
            api, cursor_token="fixture", run_id=99004, run_attempt=1,
            source_sha="a" * 40, reservation_comment_id=CID,
        )
    assert api.puts == 0, "no release receipt after changed post-probe witness"


def test_fresh_finalizer_provider_transport_failure_never_writes():
    fixture = Fixture()
    original_proof = fixture.prove()
    api = _dynamic_release_api(fixture)
    env = {"GITHUB_REPOSITORY": probe.REPOSITORY, "GITHUB_REF": "refs/heads/main"}
    with patch.dict("os.environ", env), patch(
        "v17.cursor_terminal_status_receipt.collect_receipt", return_value=original_proof,
    ), patch(
        "v17.cursor_terminal_status_receipt.api_json", side_effect=probe.ReceiptBlocked("STATUS_API_HTTP_503"),
    ), pytest.raises(release.ReleaseBlocked, match="REMOTE_RELEASE_PROVIDER_RECHECK_UNVERIFIED"):
        release.append_terminal_release(
            api, cursor_token="fixture", run_id=99004, run_attempt=1,
            source_sha="a" * 40, reservation_comment_id=CID,
        )
    assert api.puts == 0


def test_dynamic_finalizer_appends_new_receipt_only_after_terminal_proof():
    from test_v17_engineering_remote_writer_release import _FakeAPI, released_comments, run
    fixture = Fixture()
    proof = fixture.prove()

    api = _dynamic_release_api(fixture)
    before = copy.deepcopy(api.comments)
    env = {"GITHUB_REPOSITORY": probe.REPOSITORY, "GITHUB_REF": "refs/heads/main"}
    with patch.dict("os.environ", env), patch(
        "v17.cursor_terminal_status_receipt.collect_receipt", return_value=proof,
    ), patch(
        "v17.cursor_terminal_status_receipt.api_json", side_effect=_fresh_cursor_reader(fixture),
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


FAILED_CID = 900001
FAILED_INCIDENT = "101"
FAILED_PR = 101
FAILED_ORIGINAL_HEAD = "d" * 40
FAILED_NEW_HEAD = "e" * 40
FAILED_MAIN = "f" * 40
FAILED_AGENT = probe.deterministic_agent_id(probe.REPOSITORY, FAILED_PR, FAILED_ORIGINAL_HEAD)
UNRELATED_HISTORICAL_AGENT = "bc-00000000-1111-2222-3333-444444444444"
FAILED_WORKER_RUN = 800001
FAILED_WORKER = f"github-actions:{FAILED_WORKER_RUN}:1"


class FailedLaunchFixture:
    def __init__(self):
        self.comment = {
            "id": FAILED_CID,
            "user": {"login": "github-actions[bot]", "id": 41898282},
            "issue_url": f"{probe.GITHUB_API}/repos/{probe.REPOSITORY}/issues/1138",
            "body": ledger._body(ledger.receipt(
                "CURSOR_LAUNCH_RESERVED", incident_id=FAILED_INCIDENT, provider="cursor",
                lease_group=f"PR_{FAILED_PR}", main_sha=FAILED_MAIN, hold=True,
                branch="fix/synthetic-101",
                pr_number=str(FAILED_PR), pr_sha=FAILED_ORIGINAL_HEAD,
                cursor_agent_id=FAILED_AGENT, cursor_run_id=None, worker_id=FAILED_WORKER,
            )),
        }
        self.origin = {
            "id": FAILED_WORKER_RUN, "run_attempt": 1, "event": "workflow_dispatch",
            "path": ".github/workflows/wow-v17-engineering-provider-dispatcher.yml",
            "head_sha": FAILED_MAIN, "head_branch": "main",
            "status": "completed", "conclusion": "failure",
            "repository": {"full_name": probe.REPOSITORY},
            "head_repository": {"full_name": probe.REPOSITORY},
        }
        self.agent_response = probe.ReceiptBlocked("STATUS_API_HTTP_404")
        self.global_pages = [{"items": [{"id": UNRELATED_HISTORICAL_AGENT, "status": "IDLE"}]}]
        self.pr_pages = [{"items": []}]
        self.pr = {
            "number": FAILED_PR, "state": "open", "changed_files": 2,
            "head": {"sha": FAILED_NEW_HEAD, "ref": "fix/synthetic-101",
                     "repo": {"full_name": probe.REPOSITORY}},
            "base": {"ref": "main", "repo": {"full_name": probe.REPOSITORY}},
        }
        self.comparison = {
            "status": "ahead", "behind_by": 0,
            "merge_base_commit": {"sha": FAILED_ORIGINAL_HEAD},
        }
        self.files = [
            {"filename": ".github/scripts/wow_simple_security_review.py"},
            {"filename": ".github/scripts/tests/test_wow_simple_security_review.py"},
        ]

    def api(self, url, *, token, basic=False):
        prefix = f"{probe.GITHUB_API}/repos/{probe.REPOSITORY}"
        if url == f"{prefix}/issues/comments/{FAILED_CID}":
            return copy.deepcopy(self.comment)
        if url == f"{prefix}/actions/runs/{FAILED_WORKER_RUN}":
            return copy.deepcopy(self.origin)
        if url == f"{probe.CURSOR_API}/agents/{FAILED_AGENT}":
            assert basic
            if isinstance(self.agent_response, Exception):
                raise self.agent_response
            return copy.deepcopy(self.agent_response)
        if url.startswith(f"{probe.CURSOR_API}/agents?"):
            assert basic
            if "prUrl=" in url:
                return copy.deepcopy(self.pr_pages[0])
            return copy.deepcopy(self.global_pages[0])
        if url == f"{prefix}/pulls/{FAILED_PR}":
            return copy.deepcopy(self.pr)
        if url == f"{prefix}/pulls/{FAILED_PR}/files?per_page=100&page=1":
            return copy.deepcopy(self.files)
        if url == f"{prefix}/compare/{FAILED_ORIGINAL_HEAD}...{FAILED_NEW_HEAD}":
            return copy.deepcopy(self.comparison)
        raise AssertionError("unrecognized URL: " + url)

    def prove(self):
        return probe.collect_receipt(
            "fixture-cursor", "fixture-github", api=self.api,
            reservation_comment_id=FAILED_CID,
        )


def test_failed_launch_negative_proof_verifies_404_and_complete_archived_inventories():
    fixture = FailedLaunchFixture()
    result = fixture.prove()
    assert result["status"] == "CURSOR_TERMINAL_STATUS_VERIFIED"
    assert result["terminal_status"] == "CURSOR_CREATE_FAILED_NO_AGENT_PROVEN"
    assert result["agent_status"] == "NEVER_CREATED"
    assert result["agent_id"] == FAILED_AGENT
    assert result["run_id"] is None
    assert result["latest_run_id"] is None
    assert result["release_authorized"] is False
    assert result["lease_released"] is False
    assert result["can_execute"] is False
    assert result["production_verified"] is False
    assert result["negative_launch_proof"] == {
        "global_pages": 1, "global_agents_checked": 1,
        "pr_pages": 1, "pr_agents_checked": 0,
    }


@pytest.mark.parametrize("mutate,expected", [
    (lambda f: setattr(f, "agent_response", {"id": FAILED_AGENT, "status": "ACTIVE"}), "CURSOR_AGENT_STILL_ACTIVE"),
    (lambda f: setattr(f, "agent_response", {"id": FAILED_AGENT, "status": "IDLE"}), "CURSOR_AGENT_EXISTS_REQUIRES_RUN_RECONCILIATION"),
    (lambda f: setattr(f, "agent_response", probe.ReceiptBlocked("STATUS_API_HTTP_503")), "STATUS_API_HTTP_503"),
    (lambda f: setattr(f, "global_pages", [{"items": [{"id": FAILED_AGENT, "status": "IDLE"}]}]), "CURSOR_AGENT_INVENTORY_CONTRADICTION"),
    (lambda f: setattr(f, "pr_pages", [{"items": [{"id": FAILED_AGENT, "status": "IDLE"}]}]), "CURSOR_AGENT_INVENTORY_CONTRADICTION"),
    (lambda f: setattr(f, "global_pages", [{"items": [{"id": "bc-other", "status": "ACTIVE"}]}]), "CURSOR_GLOBAL_ACTIVE_AGENT_EXISTS"),
    (lambda f: setattr(f, "pr_pages", [{"items": [{"id": "bc-other", "status": "ACTIVE"}]}]), "CURSOR_ACTIVE_PR_AGENT_EXISTS"),
    (lambda f: setattr(f, "global_pages", [{"items": [], "nextCursor": "page2"}]), "CURSOR_GLOBAL_AGENT_INVENTORY_UNVERIFIED"),
    (lambda f: f.origin.update(conclusion="success"), "CURSOR_RESERVATION_WORKFLOW_UNVERIFIED"),
])
def test_failed_launch_negative_proof_fails_closed_on_ambiguous_or_active_evidence(mutate, expected):
    fixture = FailedLaunchFixture()
    mutate(fixture)
    with pytest.raises(probe.ReceiptBlocked, match=expected):
        fixture.prove()


def test_closed_failed_launch_pr_fails_closed():
    fixture = FailedLaunchFixture()
    fixture.pr["state"] = "closed"
    with pytest.raises(probe.ReceiptBlocked, match="CURSOR_SOURCE_PR_IDENTITY_INVALID"):
        fixture.prove()


def test_failed_launch_pr_state_movement_during_probe_is_rejected():
    fixture = FailedLaunchFixture()
    original_api = fixture.api
    seen = 0

    def changing_api(url, *, token, basic=False):
        nonlocal seen
        pr_url = f"{probe.GITHUB_API}/repos/{probe.REPOSITORY}/pulls/{FAILED_PR}"
        if url == pr_url:
            seen += 1
            if seen == 2:
                item = copy.deepcopy(fixture.pr)
                item["state"] = "closed"
                return item
        return original_api(url, token=token, basic=basic)

    with pytest.raises(probe.ReceiptBlocked, match="CURSOR_PR_MOVED_DURING_PROBE"):
        probe.collect_receipt(
            "fixture-cursor", "fixture-github", api=changing_api,
            reservation_comment_id=FAILED_CID,
        )
    assert seen == 2


def test_failed_launch_finalizer_appends_verified_release_without_inventing_run_id():
    from test_v17_engineering_remote_writer_release import _FakeAPI, released_comments, run

    fixture = FailedLaunchFixture()
    proof = fixture.prove()

    class FailedLaunchAPI(_FakeAPI):
        def request(self, path, payload=None, method=None):
            if path == f"/repos/{release.REPOSITORY}/pulls/{FAILED_PR}":
                return copy.deepcopy(fixture.pr)
            if path == release.LEDGER and method == "POST":
                self.puts += 1
                created = {
                    "id": 9100000000, "body": payload["body"],
                    "user": {"login": "github-actions[bot]"},
                }
                self.comments.append(created)
                return copy.deepcopy(created)
            if path.endswith("/issues/comments/9100000000"):
                return copy.deepcopy(self.comments[-1])
            return super().request(path, payload, method)

    api = FailedLaunchAPI(released_comments() + [fixture.comment])
    env = {"GITHUB_REPOSITORY": probe.REPOSITORY, "GITHUB_REF": "refs/heads/main"}
    with patch.dict("os.environ", env), patch(
        "v17.cursor_terminal_status_receipt.collect_receipt", return_value=proof,
    ), patch(
        "v17.cursor_terminal_status_receipt.api_json", side_effect=fixture.api,
    ):
        with pytest.raises(release.ReleaseBlocked, match="REMOTE_RELEASE_ORIGINAL_RESERVATION_UNVERIFIED"):
            release.append_terminal_release(
                api, cursor_token="fixture", run_id=99010, run_attempt=1,
                source_sha="a" * 40, reservation_comment_id=None,
            )
        result = release.append_terminal_release(
            api, cursor_token="fixture", run_id=99010, run_attempt=1,
            source_sha="a" * 40, reservation_comment_id=FAILED_CID,
        )
    assert result["status"] == "REMOTE_RELEASE_PENDING_WORKFLOW_SUCCESS"
    assert result["original_comment_id"] == FAILED_CID
    assert api.puts == 1
    appended_doc = json.loads(api.comments[-1]["body"].split("\n", 1)[1])
    assert appended_doc["cursor_run_id"] is None
    assert appended_doc["terminal_status"] == "CURSOR_CREATE_FAILED_NO_AGENT_PROVEN"
    assert appended_doc["agent_status"] == "NEVER_CREATED"
    assert appended_doc["negative_inventory_contract"] == 2
    assert appended_doc["pr_agents_checked"] == 0
    released = release.verified_released_ids(
        api.comments, ledger.reservations(api.comments), get_run=lambda _: run(),
    )
    assert released == {release.EXPECTED_COMMENT_ID, FAILED_CID}
    ledger.assert_no_remote_writer(api.comments, get_run=lambda _: run())

    # Fabricating a cursor_run_id on a CURSOR_LAUNCH_RESERVED release fails closed.
    bad_comments = copy.deepcopy(api.comments)
    bad_doc = copy.deepcopy(appended_doc)
    bad_doc["cursor_run_id"] = "run-fabricated"
    bad_comments[-1]["body"] = f"{release.RELEASE_MARKER}{FAILED_INCIDENT} -->\n" + json.dumps(bad_doc, sort_keys=True)
    with pytest.raises(ledger.ProviderUnavailable, match="REMOTE_RELEASE_RECEIPT_UNVERIFIED"):
        ledger.assert_no_remote_writer(bad_comments, get_run=lambda _: run())

def test_missing_branch_never_uses_historical_time_exception():
    fixture = FailedLaunchFixture()
    header, payload = fixture.comment["body"].split("\n", 1)
    entry = json.loads(payload)
    entry.pop("branch", None)
    fixture.comment["body"] = header + "\n" + json.dumps(entry, sort_keys=True)
    with pytest.raises(probe.ReceiptBlocked, match="CURSOR_RESERVATION_IDENTITY_INVALID"):
        fixture.prove()


def test_negative_inventory_rejects_second_page_unexpected_agent():
    seen = []
    def cursor_api(path, *, token, basic=False):
        assert basic and token == "test-cursor"
        seen.append(path)
        if "prUrl=" in path:
            if "cursor=next2" in path:
                return {"items": [{"id": "bc-foreign-agent", "status": "ARCHIVED"}]}
            return {"items": [], "nextCursor": "next2"}
        return {"items": []}
    with pytest.raises(probe.ReceiptBlocked, match="CURSOR_PR_ASSOCIATED_AGENT_EXISTS"):
        probe.verify_negative_inventories("test-cursor", agent_id=FAILED_AGENT,
            pr_number=FAILED_PR, api=cursor_api)
    assert len([u for u in seen if "prUrl=" in u]) == 2


@pytest.mark.parametrize("agent_status", ["IDLE", "ARCHIVED", "FINISHED", "UNKNOWN"])
def test_failed_launch_rejects_any_other_agent_on_same_pr(agent_status):
    fixture = FailedLaunchFixture()
    fixture.pr_pages = [{"items": [{"id": "bc-unexpected-pr-agent", "status": agent_status}]}]
    with pytest.raises(probe.ReceiptBlocked, match="CURSOR_PR_ASSOCIATED_AGENT_EXISTS"):
        fixture.prove()


def test_failed_launch_refuses_malformed_or_nonzero_inventory_count_before_append():
    from test_v17_engineering_remote_writer_release import _FakeAPI, released_comments
    fixture = FailedLaunchFixture()
    original = fixture.prove()
    api = _FakeAPI(released_comments() + [fixture.comment])
    env = {"GITHUB_REPOSITORY": probe.REPOSITORY, "GITHUB_REF": "refs/heads/main"}
    for invalid in (None, True, 1, "0"):
        corrupted = copy.deepcopy(original)
        corrupted["negative_launch_proof"]["pr_agents_checked"] = invalid
        with patch.dict("os.environ", env), patch(
            "v17.cursor_terminal_status_receipt.collect_receipt", return_value=corrupted,
        ), pytest.raises(release.ReleaseBlocked, match="REMOTE_RELEASE_TERMINAL_PROOF_MISMATCH"):
            release.append_terminal_release(
                api, cursor_token="fixture", run_id=99010, run_attempt=1,
                source_sha="a" * 40, reservation_comment_id=FAILED_CID,
            )
    assert api.puts == 0
