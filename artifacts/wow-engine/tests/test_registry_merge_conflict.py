"""The registry merge-conflict guard reacts to registry conflicts and nothing else.

A conflicted head has no merge commit, so GitHub runs no checks on it and the
pull request silently goes dirty. These guards cover the same detection the CI
step runs: which paths git reports as conflicted, which of them are the registry
documents, and the exit status of the script itself against the real main tip
and against a seeded registry row conflict.
"""
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAILURE_DOC = "docs/failure_codes.md"
SCRIPT = ROOT / "scripts" / "registry_merge_conflict.py"


def _guard():
    spec = importlib.util.spec_from_file_location(
        "registry_merge_conflict", ROOT / "scripts" / "registry_merge_conflict.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GUARD = _guard()


def _merge_tree_supported():
    if subprocess.run(
        ("git", "rev-parse", "--git-dir"), cwd=ROOT, capture_output=True
    ).returncode:
        return False
    proc = subprocess.run(
        ("git", "merge-tree", "--write-tree", "--name-only", "HEAD", "HEAD"),
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return proc.returncode in (0, 1)


MERGE_TREE_SUPPORTED = _merge_tree_supported()


def _ref_exists(ref):
    return subprocess.run(
        ("git", "rev-parse", "--verify", "--quiet", ref),
        cwd=ROOT,
        capture_output=True,
    ).returncode == 0


# CI checks out with fetch-depth: 0, so origin/main is the base tip the workflow's
# merge step compares against. A local main is the fallback for a clone without a
# remote-tracking ref.
BASE_REF = next((ref for ref in ("origin/main", "main") if _ref_exists(ref)), None)


def test_registry_document_is_watched_by_its_repository_relative_path():
    watched = GUARD.documented_paths()
    assert len(watched) == len(GUARD.REGISTRY_DOCS)
    assert all(path.startswith("artifacts/wow-engine/docs/") for path in watched)


def test_registry_conflict_is_reported():
    path = GUARD.documented_paths([FAILURE_DOC])[0]
    assert GUARD.registry_conflicts([path]) == [path]


def test_unrelated_conflicts_are_not_failed():
    assert GUARD.registry_conflicts(
        ["gate_engine/classifier.py", "v17/nhl_pregame_orchestration.py"]
    ) == []


def test_dot_prefixed_paths_still_match():
    path = GUARD.documented_paths([FAILURE_DOC])[0]
    assert GUARD.registry_conflicts(["./" + path]) == [path]


def test_parse_drops_the_written_tree_object_id():
    oid = "0" * 40
    assert GUARD.parse_merge_tree(f"{oid}\nartifacts/x/y.md\n") == ["artifacts/x/y.md"]


def test_parse_of_a_clean_merge_yields_no_conflicts():
    assert GUARD.parse_merge_tree("") == []


@pytest.mark.skipif(not MERGE_TREE_SUPPORTED, reason="git merge-tree --write-tree needs git >= 2.38")
def test_guard_exits_zero_against_the_current_main_tip():
    """The step the workflow runs stays green as main advances.

    The Verification notes cite a clean merge against main; running the script
    itself against the main ref this checkout fetched fails the moment that
    stops being true, instead of leaving a stale claim in the pull request.
    """
    if BASE_REF is None:
        pytest.skip("neither origin/main nor main is available to merge against")

    proc = subprocess.run(
        (sys.executable, str(SCRIPT), "--base", BASE_REF, "--head", "HEAD"),
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"no registry conflict between HEAD and {BASE_REF}" in proc.stdout


@pytest.mark.skipif(not MERGE_TREE_SUPPORTED, reason="git merge-tree --write-tree needs git >= 2.38")
def test_script_exits_nonzero_when_a_registry_row_conflicts(tmp_path):
    """Both branches add a row to the same document, so the script must fail."""
    repo = tmp_path / "repo"
    # The script takes its repository root from its own location, so it has to
    # sit where it does in this repository for the watched paths to line up.
    script = repo / "artifacts" / "wow-engine" / "scripts" / "registry_merge_conflict.py"
    doc = repo / "artifacts" / "wow-engine" / "docs" / "failure_codes.md"
    script.parent.mkdir(parents=True)
    doc.parent.mkdir(parents=True)
    shutil.copyfile(SCRIPT, script)
    doc.write_text("| Code |\n|---|\n| `A` |\n", encoding="utf-8")

    def git(*args):
        return subprocess.run(
            ("git",) + args, cwd=repo, check=True, capture_output=True, text=True
        )

    git("init", "-q")
    git("config", "user.email", "guard@example.com")
    git("config", "user.name", "Registry Guard")
    git("add", "-A")
    git("commit", "-qm", "base")
    git("branch", "base")

    doc.write_text("| Code |\n|---|\n| `A` |\n| `B` |\n", encoding="utf-8")
    git("commit", "-qam", "this branch adds a row")
    git("checkout", "-q", "base")
    doc.write_text("| Code |\n|---|\n| `A` |\n| `C` |\n", encoding="utf-8")
    git("commit", "-qam", "base adds a row elsewhere")
    git("checkout", "-q", "-")

    proc = subprocess.run(
        (sys.executable, str(script), "--base", "base", "--head", "HEAD"),
        cwd=repo,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert (
        "artifacts/wow-engine/docs/failure_codes.md conflicts with base" in proc.stdout
    )


@pytest.mark.skipif(not MERGE_TREE_SUPPORTED, reason="git merge-tree --write-tree needs git >= 2.38")
def test_conflicting_rows_in_a_real_repository_are_detected(tmp_path):
    repo = tmp_path / "repo"
    doc = repo / "artifacts" / "wow-engine" / "docs" / "failure_codes.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("| Code |\n|---|\n| `A` |\n", encoding="utf-8")

    def git(*args):
        return subprocess.run(
            ("git",) + args, cwd=repo, check=True, capture_output=True, text=True
        )

    git("init", "-q")
    git("config", "user.email", "guard@example.com")
    git("config", "user.name", "Registry Guard")
    git("add", "-A")
    git("commit", "-qm", "base")
    git("branch", "base")

    doc.write_text("| Code |\n|---|\n| `A` |\n| `B` |\n", encoding="utf-8")
    git("commit", "-qam", "this branch adds a row")
    git("checkout", "-q", "base")
    doc.write_text("| Code |\n|---|\n| `A` |\n| `C` |\n", encoding="utf-8")
    git("commit", "-qam", "base adds a row elsewhere")
    git("checkout", "-q", "-")

    proc = subprocess.run(
        ("git", "merge-tree", "--write-tree", "--name-only", "base", "HEAD"),
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stderr
    paths = GUARD.parse_merge_tree(proc.stdout)
    assert GUARD.registry_conflicts(paths) == [
        "artifacts/wow-engine/docs/failure_codes.md"
    ]
