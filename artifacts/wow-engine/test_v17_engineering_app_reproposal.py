import base64
from unittest.mock import MagicMock

import pytest

from v17.engineering_app_reproposal import (
    APP_LOGIN, MAX_TOTAL_BYTES, ProposedFile, REPO, ReproposalBlocked,
    SOURCE_ALLOWLIST, compare_source_and_main, create_draft, decode_content,
    require_engineering_installation, validate_changes, validate_pr_metadata,
)

HEAD = "a" * 40
BASE = "b" * 40
MAIN = "c" * 40


def source_pr(number=1683, head=HEAD, author="gregoryharper84-ship-it"):
    return {
        "number": number, "state": "open", "draft": True,
        "user": {"login": author},
        "head": {"sha": head, "repo": {"full_name": REPO}},
        "base": {"ref": "main", "sha": BASE},
    }


def test_owner_source_exact_sha_and_author_required():
    validate_pr_metadata(source_pr(), number=1683, expected_head=HEAD)
    for kwargs, code in [
        ({"head": "d" * 40}, "SOURCE_HEAD_MOVED"),
        ({"author": APP_LOGIN}, "SOURCE_PR_AUTHOR_UNEXPECTED"),
    ]:
        payload = source_pr(**kwargs)
        with pytest.raises(ReproposalBlocked, match=code):
            validate_pr_metadata(payload, number=1683, expected_head=HEAD)


def test_prohibits_unlisted_or_deleted_source_paths():
    expected = [{"filename": f, "status": "modified"} for f in sorted(SOURCE_ALLOWLIST[1683])]
    assert len(validate_changes(expected, source_pr=1683)) == 3
    expected[0]["status"] = "removed"
    with pytest.raises(ReproposalBlocked, match="SOURCE_FILE_NOT_ALLOWLISTED"):
        validate_changes(expected, source_pr=1683)
    expected[0]["status"] = "modified"
    expected[0]["filename"] = ".github/CODEOWNERS"
    with pytest.raises(ReproposalBlocked, match="SOURCE_FILE_NOT_ALLOWLISTED"):
        validate_changes(expected, source_pr=1683)


def test_current_main_drift_must_fail_closed():
    with pytest.raises(ReproposalBlocked, match="BASE_FILE_DRIFT"):
        compare_source_and_main(
            [("render.yaml", "modified")],
            base_contents={"render.yaml": b"before"},
            main_contents={"render.yaml": b"changed"},
            source_contents={"render.yaml": b"after"},
        )


def test_only_changed_contents_make_a_proposal():
    proposal = compare_source_and_main(
        [("render.yaml", "modified"), ("new.py", "added")],
        base_contents={"render.yaml": b"before", "new.py": None},
        main_contents={"render.yaml": b"before", "new.py": None},
        source_contents={"render.yaml": b"after", "new.py": b"new"},
    )
    assert [f.path for f in proposal] == ["render.yaml", "new.py"]
    assert proposal[0].content == b"after"


@pytest.mark.parametrize("content", [b"", b"x" * (1_048_576 + 1)])
def test_refuses_empty_or_oversized_source(content):
    with pytest.raises(ReproposalBlocked, match="SOURCE_CONTENT_INVALID"):
        compare_source_and_main(
            [("render.yaml", "modified")],
            base_contents={"render.yaml": b"before"},
            main_contents={"render.yaml": b"before"},
            source_contents={"render.yaml": content},
        )


def test_rejects_bad_contents_payload():
    with pytest.raises(ReproposalBlocked, match="CONTENTS_RESPONSE_INVALID"):
        decode_content({"type": "dir"})
    encoded = base64.b64encode(b"valid").decode()
    assert decode_content({"type": "file", "encoding": "base64", "content": encoded}) == b"valid"
    assert decode_content(None, may_be_absent=True) is None


def test_owner_personal_token_never_writes():
    api = MagicMock()
    api.token = "ghu_owner_access"
    with pytest.raises(ReproposalBlocked, match="ENGINEERING_APP_INSTALLATION_TOKEN_REQUIRED"):
        require_engineering_installation(api)
    api.request.assert_not_called()
    api.post.assert_not_called()


def test_missing_repo_installation_never_writes():
    api = MagicMock()
    api.token = "ghs_opaque_installed_token"
    api.request.return_value = {"repositories": [{"full_name": "other/repo"}]}
    with pytest.raises(ReproposalBlocked, match="ENGINEERING_INSTALLATION_REPO_UNVERIFIED"):
        require_engineering_installation(api)
    api.post.assert_not_called()


def _allowed_app():
    api = MagicMock()
    api.token = "ghs_opaque_installed_token"
    api.request.return_value = {"repositories": [{"full_name": REPO}]}
    api.get.side_effect = [
        {"object": {"sha": MAIN}},
        {"tree": {"sha": "d" * 40}},
    ]
    api.post.side_effect = [
        {"sha": "e" * 40},  # git blob
        {"sha": "f" * 40},  # git tree
        {"sha": "1" * 40},  # git commit
        {"ref": "refs/heads/engineering/repropose"},
        {
            "number": 999, "draft": True,
            "user": {"login": APP_LOGIN},
            "head": {"sha": "1" * 40},
        },
    ]
    return api


def test_reproposal_creates_only_draft_under_app_identity():
    api = _allowed_app()
    receipt = create_draft(api, 1683, HEAD, MAIN,
                           [ProposedFile("render.yaml", b"after", "modified")])
    assert receipt["number"] == 999
    assert receipt["can_execute"] is False
    assert receipt["owner_approval_required"] is True
    assert receipt["independent_qa_required"] is True
    calls = api.post.call_args_list
    assert [a.args[0] for a in calls] == [
        "/git/blobs", "/git/trees", "/git/commits", "/git/refs", "/pulls",
    ]
    assert calls[-1].args[1]["draft"] is True


def test_different_app_created_pr_fails_after_draft_creation():
    api = _allowed_app()
    api.post.side_effect[-1]["user"]["login"] = "other-bot[bot]"
    with pytest.raises(ReproposalBlocked, match="CREATED_PR_IDENTITY_UNVERIFIED_DRAFT_HOLD"):
        create_draft(api, 1683, HEAD, MAIN,
                     [ProposedFile("render.yaml", b"after", "modified")])


def test_main_moved_prevents_any_mutations():
    api = MagicMock()
    api.token = "ghs_opaque_installed_token"
    api.request.return_value = {"repositories": [{"full_name": REPO}]}
    api.get.return_value = {"object": {"sha": "9" * 40}}
    with pytest.raises(ReproposalBlocked, match="MAIN_HEAD_MOVED_REEVALUATE"):
        create_draft(api, 1683, HEAD, MAIN,
                     [ProposedFile("render.yaml", b"after", "modified")])
    api.post.assert_not_called()
