"""Offline E2E mocks of exact-head authenticated GitHub source ingestion.

Does not call GitHub, run untrusted code, invoke paid models, or post real comments.
"""
import base64
import importlib.util
from pathlib import Path
import pytest

SCRIPT = (Path(__file__).resolve().parents[2]
          / ".github/scripts/wow_native_senior_review.py")
spec = importlib.util.spec_from_file_location("wow_native_senior_review_script", SCRIPT)
reviewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reviewer)

REPO = "gregoryharper84-ship-it/WOW-Dashboard"
BASE = "a" * 40
HEAD = "b" * 40
PATH = "artifacts/wow-engine/v17/native_case.py"
SOURCE = "def unsafe(user):\n    return eval(user)\n"
PATCH = "@@ -0,0 +1,2 @@\n+def unsafe(user):\n+    return eval(user)\n"


class FakeGitHub:
    def __init__(self):
        self.calls = []
        self.moved = False
        self.comments = []
        self.posted = []

    def request(self, path, *, method="GET", body=None):
        self.calls.append((method, path))
        if path == "/pulls/900":
            sha = "c" * 40 if self.moved and self.calls.count(("GET", path)) > 1 else HEAD
            return {
                "number": 900, "state": "open", "draft": False,
                "changed_files": 1, "body": "Incident: #2136\n",
                "head": {"sha": sha, "repo": {"full_name": REPO}},
                "base": {"sha": BASE, "ref": "main",
                         "repo": {"full_name": REPO}},
            }
        if path == "/pulls/900/files?per_page=100&page=1":
            return [{"filename": PATH, "status": "added", "patch": PATCH}]
        if path == f"/contents/{PATH}?ref={HEAD}":
            return {"type": "file", "encoding": "base64",
                    "content": base64.b64encode(SOURCE.encode()).decode()}
        if path == f"/contents/artifacts/wow-engine/v17?ref={HEAD}":
            return []
        if path == "/issues/900/comments?per_page=100&page=1":
            return list(self.comments)
        if path == "/issues/900/comments?per_page=100&page=2":
            return []
        if path == "/issues/900/comments" and method == "POST":
            self.posted.append(("POST", body))
            return {"id": 555}
        if path == "/issues/comments/555" and method == "PATCH":
            self.posted.append(("PATCH", body))
            return {"id": 555}
        raise AssertionError("Unexpected or unsafe API access: " + path)


def test_live_style_authenticated_source_to_advisory_and_same_incident_handoff():
    client = FakeGitHub()
    result = reviewer.collect_pr_review(client, number=900, expected_head=HEAD)
    assert result["incident_id"] == "2136"
    assert result["review"]["finding_count"] == 1
    assert result["review"]["findings"][0]["rule_id"] == "DYNAMIC_CODE_EXECUTION"
    assert result["review"]["findings"][0]["verification"] == "STATIC_PATTERN_UNCONFIRMED"
    assert result["review"]["amrt_handoff"][0]["existing_incident_id"] == "2136"
    assert result["review"]["amrt_handoff"][0]["next_owner"] == "ROOT_CAUSE_INVESTIGATOR"
    assert result["review"]["amrt_handoff"][0]["may_mutate"] is False
    assert result["release_authority"] == "NONE_ADVISORY_ONLY"
    assert result["semantic_review"] == "NOT_RUN"
    assert result["can_execute"] is False
    assert client.calls.count(("GET", "/pulls/900")) == 3


def test_safe_summary_does_not_expose_source_or_claim_semantic_pass():
    result = reviewer.collect_pr_review(FakeGitHub(), number=900, expected_head=HEAD)
    text = reviewer.markdown_summary(result)
    assert "AI semantic review: **NOT_RUN**" in text
    assert "eval(user)" not in text
    assert "advisory, not a release gate" in text
    assert "automatic repair" in text


def test_review_stale_head_aborts_without_comments():
    client = FakeGitHub()
    client.moved = True
    with pytest.raises(reviewer.ReviewHold, match="PR_IDENTITY_OR_SHA_CHANGED"):
        reviewer.collect_pr_review(client, number=900, expected_head=HEAD)
    assert not client.posted


def test_no_issue_spam_existing_owned_comment_is_updated():
    client = FakeGitHub()
    client.comments = [{
        "id": 555, "body": reviewer._COMMENT_MARKER + "\nprior",
        "user": {"login": "github-actions[bot]"},
    }]
    reviewer.post_advisory_comment(client, number=900, head_sha=HEAD,
                                   text=reviewer._COMMENT_MARKER + "\nnew")
    assert client.posted == [("PATCH", {"body": reviewer._COMMENT_MARKER + "\nnew"})]


def test_no_existing_comment_adds_only_one_advisory_comment():
    client = FakeGitHub()
    reviewer.post_advisory_comment(client, number=900, head_sha=HEAD,
                                   text=reviewer._COMMENT_MARKER + "\nnew")
    assert client.posted == [("POST", {"body": reviewer._COMMENT_MARKER + "\nnew"})]


def test_wrong_actor_marker_is_not_eligible_for_edit():
    client = FakeGitHub()
    client.comments = [{
        "id": 555, "body": reviewer._COMMENT_MARKER + "\nhuman",
        "user": {"login": "real-reviewer"},
    }]
    reviewer.post_advisory_comment(client, number=900, head_sha=HEAD,
                                   text=reviewer._COMMENT_MARKER + "\nnew")
    assert client.posted[0][0] == "POST"


def test_duplicate_bots_hold_instead_of_overwriting():
    client = FakeGitHub()
    client.comments = [
        {"id": n, "body": reviewer._COMMENT_MARKER,
         "user": {"login": "github-actions[bot]"}} for n in (555, 556)
    ]
    with pytest.raises(reviewer.ReviewHold, match="PR_ADVISORY_COMMENT_AMBIGUOUS"):
        reviewer.post_advisory_comment(client, number=900, head_sha=HEAD,
                                       text=reviewer._COMMENT_MARKER + "\nnew")
    assert client.posted == []


def test_wrong_expected_sha_rejected_before_network():
    client = FakeGitHub()
    with pytest.raises(reviewer.ReviewHold, match="EXPECTED_HEAD_INVALID"):
        reviewer.collect_pr_review(client, number=900, expected_head="bad")
    assert client.calls == []


def test_invalid_source_path_rejected_and_never_fetched():
    client = FakeGitHub()
    original = client.request
    def injected(path, **kw):
        if path == "/pulls/900/files?per_page=100&page=1":
            return [{"filename": "../secrets", "status": "modified", "patch": PATCH}]
        return original(path, **kw)
    client.request = injected
    with pytest.raises(reviewer.ReviewHold, match="GITHUB_FILE_PATH_INVALID"):
        reviewer.collect_pr_review(client, number=900, expected_head=HEAD)


def test_changed_file_missing_patch_holds_not_silent_filler():
    client = FakeGitHub()
    original = client.request
    def no_patch(path, **kw):
        if path == "/pulls/900/files?per_page=100&page=1":
            return [{"filename": PATH, "status": "modified", "patch": None}]
        return original(path, **kw)
    client.request = no_patch
    with pytest.raises(reviewer.ReviewHold, match="PR_PATCH_UNAVAILABLE"):
        reviewer.collect_pr_review(client, number=900, expected_head=HEAD)


def test_api_client_rejects_path_smuggling_without_http_call():
    api = reviewer.GitHubClient("not-a-real-token")
    with pytest.raises(reviewer.ReviewHold, match="GITHUB_API_PATH_UNTRUSTED"):
        api.request("/pulls/900\nAuthorization: fake")


def test_source_base64_corruption_holds():
    client = FakeGitHub()
    original = client.request
    def corrupt(path, **kw):
        if path == f"/contents/{PATH}?ref={HEAD}":
            return {"type": "file", "encoding": "base64", "content": "#invalid###"}
        return original(path, **kw)
    client.request = corrupt
    with pytest.raises(reviewer.ReviewHold, match="PR_SOURCE_INVALID_UTF8"):
        reviewer.collect_pr_review(client, number=900, expected_head=HEAD)


def test_fork_held_no_reading_untrusted_repo():
    client = FakeGitHub()
    original = client.request
    def fork(path, **kw):
        data = original(path, **kw)
        if path == "/pulls/900":
            data["head"]["repo"]["full_name"] = "attacker/untrusted"
        return data
    client.request = fork
    with pytest.raises(reviewer.ReviewHold, match="PR_IDENTITY_OR_SHA_CHANGED"):
        reviewer.collect_pr_review(client, number=900, expected_head=HEAD)


class IncidentGitHub(FakeGitHub):
    def __init__(self):
        super().__init__()
        self.open_incident = True
        self.incident_comments = []
        self.incident_writes = []

    def request(self, path, *, method="GET", body=None):
        if path == "/issues/2136":
            return {"number": 2136, "state": "open" if self.open_incident else "closed"}
        if path == "/issues/2136/comments?per_page=100&page=1":
            return list(self.incident_comments)
        if path == "/issues/2136/comments?per_page=100&page=2":
            return []
        if path == "/issues/2136/comments" and method == "POST":
            self.incident_writes.append(("POST", body))
            return {"id": 777}
        if path == "/issues/comments/777" and method == "PATCH":
            self.incident_writes.append(("PATCH", body))
            return {"id": 777}
        return super().request(path, method=method, body=body)


def test_existing_incident_receives_one_advisory_handoff_no_new_issue():
    client = IncidentGitHub()
    result = reviewer.collect_pr_review(client, number=900, expected_head=HEAD)
    outcome = reviewer.post_existing_incident_handoff(client, result=result)
    assert outcome == "EXISTING_INCIDENT_NOTE_CREATED"
    assert len(client.incident_writes) == 1
    method, posted = client.incident_writes[0]
    assert method == "POST"
    assert "ADVISORY_PENDING_REPRODUCTION" in posted["body"]
    assert "not verified P0/P1" in posted["body"]
    assert "can_execute=false" in posted["body"]
    assert "AMRT" in posted["body"]
    assert "eval(user)" not in posted["body"]


def test_existing_incident_note_updates_instead_of_duplicate():
    client = IncidentGitHub()
    result = reviewer.collect_pr_review(client, number=900, expected_head=HEAD)
    client.incident_comments = [{
        "id": 777,
        "body": "<!-- WOW_NATIVE_SENIOR_AMRT_ADVISORY_PR_900 -->\nold",
        "user": {"login": "github-actions[bot]"},
    }]
    outcome = reviewer.post_existing_incident_handoff(client, result=result)
    assert outcome == "EXISTING_INCIDENT_NOTE_UPDATED"
    assert [x[0] for x in client.incident_writes] == ["PATCH"]


def test_closed_canonical_incident_refuses_handoff():
    client = IncidentGitHub()
    result = reviewer.collect_pr_review(client, number=900, expected_head=HEAD)
    client.open_incident = False
    with pytest.raises(reviewer.ReviewHold, match="CANONICAL_INCIDENT_NOT_OPEN_ISSUE"):
        reviewer.post_existing_incident_handoff(client, result=result)
    assert not client.incident_writes


def test_stale_head_refuses_amrt_handoff():
    client = IncidentGitHub()
    result = reviewer.collect_pr_review(client, number=900, expected_head=HEAD)
    client.moved = True
    with pytest.raises(reviewer.ReviewHold, match="PR_IDENTITY_OR_SHA_CHANGED"):
        reviewer.post_existing_incident_handoff(client, result=result)
    assert not client.incident_writes


def test_duplicate_incident_bot_notes_fail_closed():
    client = IncidentGitHub()
    result = reviewer.collect_pr_review(client, number=900, expected_head=HEAD)
    client.incident_comments = [{
        "id": n,
        "body": "<!-- WOW_NATIVE_SENIOR_AMRT_ADVISORY_PR_900 -->\nold",
        "user": {"login": "github-actions[bot]"},
    } for n in (777, 778)]
    with pytest.raises(reviewer.ReviewHold, match="INCIDENT_HANDOFF_DUPLICATE_BOT_COMMENTS"):
        reviewer.post_existing_incident_handoff(client, result=result)
    assert client.incident_writes == []


def test_unlinked_pr_keeps_findings_without_creating_new_incident():
    client = IncidentGitHub()
    result = reviewer.collect_pr_review(client, number=900, expected_head=HEAD)
    result["incident_id"] = "PR-900"
    outcome = reviewer.post_existing_incident_handoff(client, result=result)
    assert outcome == "INCIDENT_ID_UNRESOLVED_PR_ADVISORY_ONLY"
    assert client.incident_writes == []
