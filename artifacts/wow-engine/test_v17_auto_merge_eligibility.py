"""No protected/unknown PR may be auto-armed."""
from copy import deepcopy
from v17.auto_merge_eligibility import eligible

REPO = "gregoryharper84-ship-it/WOW-Dashboard"
SHA = "a" * 40


def pr():
    return {
        "state": "open", "draft": False,
        "user": {"login": "wow-engineering[bot]"},
        "base": {"ref": "main", "repo": {"full_name": REPO}},
        "head": {"sha": SHA, "repo": {"full_name": REPO}},
        "labels": [{"name": "class-a"}],
    }


def policy(p=None, files=None, protected=()):
    return eligible(p if p is not None else pr(),
                    files if files is not None else [{"filename": "src/safe.py", "status": "modified"}],
                    "codeowners", lambda _rules, path: ["@owner"] if path in protected else [])


def test_ordinary_app_class_a_is_eligible():
    assert policy() == (True, "ELIGIBLE")


def test_owner_authored_is_not_eligible():
    p = pr()
    p["user"]["login"] = "gregoryharper84-ship-it"
    assert policy(p)[1] == "ENGINEERING_APP_AUTHOR_REQUIRED"


def test_draft_and_unclassified_are_not_eligible():
    p = pr()
    p["draft"] = True
    assert not policy(p)[0]
    p = pr()
    p["labels"] = []
    assert policy(p)[1] == "CLASS_A_LABEL_REQUIRED"


def test_protected_prefix_and_codeowner_paths_are_ineligible():
    for path in [".github/workflows/critical.yml", ".agents/SKILL.md"]:
        assert policy(files=[{"filename": path, "status": "modified"}])[1] == "PROTECTED_PATH"
    assert policy(protected=("src/safe.py",))[1] == "CODEOWNERS_PROTECTED_PATH"


def test_rename_origin_cannot_escape_protection():
    files = [{"filename": "src/safe.py", "previous_filename": ".github/critical.yml", "status": "renamed"}]
    assert policy(files=files)[1] == "PROTECTED_PATH"
    assert policy(files=[{"filename": "src/safe.py", "status": "renamed"}])[1] == "RENAME_ORIGIN_MISSING"


def test_rejects_foreign_head_bad_sha_and_invalid_file_evidence():
    p = pr()
    p["head"]["repo"]["full_name"] = "attacker/fork"
    assert policy(p)[1] == "FOREIGN_HEAD"
    p = pr()
    p["head"]["sha"] = "bad"
    assert policy(p)[1] == "HEAD_SHA_INVALID"
    assert policy(files=[])[1] == "FILE_INVENTORY_MISSING_OR_TRUNCATED"
    assert policy(files=[{}])[1] == "FILE_METADATA_INVALID"


def test_ineligible_if_base_wrong_or_state_closed():
    p = pr()
    p["base"]["ref"] = "dev"
    assert not policy(p)[0]
    p = pr()
    p["state"] = "closed"
    assert not policy(p)[0]


def test_auto_merge_policy_itself_requires_owner_code_review():
    from pathlib import Path
    from v17.agent_identity_policy import _codeowners_owners
    root = Path(__file__).resolve().parents[2]
    rules = (root / ".github/CODEOWNERS").read_text()
    assert _codeowners_owners(
        rules, "artifacts/wow-engine/v17/auto_merge_eligibility.py"
    ) == ["@gregoryharper84-ship-it"]
