import base64
from unittest.mock import MagicMock

import pytest

import v17.engineering_app_reproposal as m

from v17.engineering_app_reproposal import (
    APP_LOGIN, MAX_TOTAL_BYTES, ProposedFile, REPO, ReproposalBlocked,
    SOURCE_ALLOWLIST, build_plan, compare_source_and_main, create_draft, decode_content,
    require_engineering_installation, validate_changes, validate_pr_metadata,
)

HEAD = "a" * 40
BASE = "b" * 40
MAIN = "c" * 40


@pytest.fixture(autouse=True)
def _pin_fixture_heads(monkeypatch, request):
    # Fixture PRs use synthetic HEAD; test_pinned_* tests exercise the shipped pins.
    if not request.node.name.startswith("test_pinned_"):
        monkeypatch.setattr(m, "SOURCE_PINNED_HEADS", {n: HEAD for n in SOURCE_ALLOWLIST})


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
        None,
        {"tree": {"sha": "d" * 40}},
    ]
    api.post.side_effect = [
        {"sha": "e" * 40},  # git blob
        {"sha": "f" * 40},  # git tree
        {"sha": "1" * 40},  # git commit
        {"ref": "refs/heads/engineering/repropose-1683-aaaaaaaaaaaa-cccccccccccc",
         "object": {"sha": "1" * 40}},
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
    responses = list(api.post.side_effect)
    responses[-1]["user"]["login"] = "other-bot[bot]"
    api.post.side_effect = responses
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


def test_malformed_base64_payload_is_typed_failure():
    with pytest.raises(ReproposalBlocked, match="CONTENTS_DECODE_INVALID"):
        decode_content({"type": "file", "encoding": "base64", "content": "!!!==x"})


def test_unverifiable_branch_ref_cannot_open_pr():
    api = _allowed_app()
    responses = list(api.post.side_effect)
    responses[3] = {"ref": "refs/heads/wrong-branch", "object": {"sha": "1" * 40}}
    api.post.side_effect = responses
    with pytest.raises(ReproposalBlocked, match="BRANCH_REF_UNVERIFIED_DRAFT_HOLD"):
        create_draft(api, 1683, HEAD, MAIN,
                     [ProposedFile("render.yaml", b"after", "modified")])
    assert api.post.call_count == 4


def test_existing_exact_source_app_draft_is_idempotent():
    api = _allowed_app()
    branch = "engineering/repropose-1683-aaaaaaaaaaaa-cccccccccccc"
    api.get.side_effect = [
        {"object": {"sha": MAIN}},
        {"object": {"sha": "1" * 40}},
        [{"number": 999, "draft": True, "user": {"login": APP_LOGIN},
          "head": {"sha": "1" * 40}, "body": "Source head: " + HEAD}],
    ]
    result = create_draft(api, 1683, HEAD, MAIN,
                          [ProposedFile("render.yaml", b"after", "modified")])
    assert result["state"] == "DUPLICATE"
    assert result["owner_approval_required"] is True
    api.post.assert_not_called()


def test_existing_untrusted_branch_fails_closed_without_writes():
    api = _allowed_app()
    api.get.side_effect = [
        {"object": {"sha": MAIN}},
        {"object": {"sha": "1" * 40}},
        [{"number": 999, "draft": True, "user": {"login": "foreign[bot]"},
          "head": {"sha": "1" * 40}, "body": "Source head: " + HEAD}],
    ]
    with pytest.raises(ReproposalBlocked, match="EXISTING_BRANCH_UNVERIFIED"):
        create_draft(api, 1683, HEAD, MAIN,
                     [ProposedFile("render.yaml", b"after", "modified")])
    api.post.assert_not_called()


def _merge_base_api(*, merge_base=BASE, drifted_path=None):
    # GitHub's current PR base can be newer than the source's true fork base.
    # The merge-base comparison must supply the ancestor used for all file
    # drift comparisons, rather than trusting source["base"]["sha"].
    api = MagicMock()
    changed = [{"filename": path, "status": "modified"}
               for path in sorted(SOURCE_ALLOWLIST[1683])]
    original = b"verified-old-source"
    current = b"main-updated-after-branch" if drifted_path else original

    def reply(path, **kwargs):
        if path == "/pulls/1683":
            source = source_pr()
            source["base"]["sha"] = MAIN   # moving PR base: NOT the merge base
            source["changed_files"] = len(changed)
            return source
        if path == "/pulls/1683/files?per_page=100":
            return changed
        if path == "/git/ref/heads/main":
            return {"object": {"sha": MAIN}}
        if path == f"/compare/{MAIN}...{HEAD}":
            return {"merge_base_commit": {"sha": merge_base}} if merge_base else {}
        if path.startswith("/contents/"):
            ref = path.rsplit("?ref=", 1)[-1]
            data = (b"reviewed-new-source" if ref == HEAD
                    else current if ref == MAIN
                    else original if ref == BASE
                    else None)
            if data is None:
                raise AssertionError("UNEXPECTED_CONTENT_REF:" + ref)
            return {"type": "file", "encoding": "base64",
                    "content": base64.b64encode(data).decode()}
        raise AssertionError("UNEXPECTED_ENDPOINT:" + path)

    api.get.side_effect = reply
    return api


def test_build_plan_uses_verified_merge_base_not_movable_pr_base_sha():
    api = _merge_base_api()
    main, proposed = build_plan(api, 1683, HEAD)
    assert main == MAIN
    assert len(proposed) == len(SOURCE_ALLOWLIST[1683])
    assert all(p.content == b"reviewed-new-source" for p in proposed)
    assert any(x.args[0] == f"/compare/{MAIN}...{HEAD}"
               for x in api.get.call_args_list)
    assert all("?ref=" + BASE in x.args[0] for x in api.get.call_args_list
               if "?ref=" in x.args[0] and "?ref=" + MAIN not in x.args[0]
               and "?ref=" + HEAD not in x.args[0])
    api.post.assert_not_called()


def test_build_plan_rejects_main_drift_even_if_pr_base_points_to_current_main():
    api = _merge_base_api(drifted_path="render.yaml")
    with pytest.raises(ReproposalBlocked, match="BASE_FILE_DRIFT"):
        build_plan(api, 1683, HEAD)
    api.post.assert_not_called()


@pytest.mark.parametrize("merge_base", [None, "malformed", "f" * 39])
def test_build_plan_rejects_unknown_merge_base_before_reading_or_writing_files(merge_base):
    api = _merge_base_api(merge_base=merge_base)
    with pytest.raises(ReproposalBlocked, match="SOURCE_MERGE_BASE_UNVERIFIED"):
        build_plan(api, 1683, HEAD)
    assert not any("/contents/" in x.args[0] for x in api.get.call_args_list)
    api.post.assert_not_called()


def test_audited_manifest_source_1716_is_narrowly_bounded():
    source = source_pr(number=1716)
    validate_pr_metadata(source, number=1716, expected_head=HEAD)
    assert SOURCE_ALLOWLIST[1716] == frozenset({
        "artifacts/wow-engine/v17/engineering_dispatch_manifest.json",
        "artifacts/wow-engine/tests/test_v17_engineering_dispatch_queue.py",
    })
    allowed = [{"filename": p, "status": "modified"} for p in sorted(SOURCE_ALLOWLIST[1716])]
    assert len(validate_changes(allowed, source_pr=1716)) == 2
    allowed[0]["filename"] = ".github/workflows/wow-v17-independent-qa-check.yml"
    with pytest.raises(ReproposalBlocked, match="SOURCE_FILE_NOT_ALLOWLISTED"):
        validate_changes(allowed, source_pr=1716)


def test_audited_manifest_source_1716_still_requires_app_identity():
    api = MagicMock()
    api.token = "ghu_owner_scope"
    with pytest.raises(ReproposalBlocked, match="ENGINEERING_APP_INSTALLATION_TOKEN_REQUIRED"):
        create_draft(api, 1716, HEAD, MAIN,
                     [ProposedFile("artifacts/wow-engine/v17/engineering_dispatch_manifest.json",
                                   b"changed", "modified")])
    api.post.assert_not_called()


def test_unrelated_ci_throttle_source_is_not_authorized_for_render_recovery():
    assert 1717 not in SOURCE_ALLOWLIST
    assert 1717 not in m.SOURCE_PINNED_HEADS
    with pytest.raises(ReproposalBlocked, match="SOURCE_NOT_ALLOWLISTED"):
        validate_pr_metadata(source_pr(number=1717), number=1717, expected_head=HEAD)


def test_pinned_every_allowlisted_source_has_exact_owner_pinned_head():
    assert set(m.SOURCE_PINNED_HEADS) == set(SOURCE_ALLOWLIST)
    assert all(len(h) == 40 and int(h, 16) >= 0 for h in m.SOURCE_PINNED_HEADS.values())


def test_pinned_moved_source_head_is_not_laundered_into_app_authorship():
    moved = "d" * 40
    with pytest.raises(ReproposalBlocked, match="SOURCE_HEAD_NOT_OWNER_PINNED"):
        validate_pr_metadata(source_pr(number=1716, head=moved), number=1716, expected_head=moved)
    pinned = m.SOURCE_PINNED_HEADS[1716]
    validate_pr_metadata(source_pr(number=1716, head=pinned), number=1716, expected_head=pinned)


def test_pinned_recovery_source_cannot_reallowlist_itself():
    assert 1702 not in SOURCE_ALLOWLIST and 1702 not in m.SOURCE_PINNED_HEADS
    with pytest.raises(ReproposalBlocked, match="SOURCE_NOT_ALLOWLISTED"):
        validate_pr_metadata(source_pr(number=1702), number=1702, expected_head=HEAD)
