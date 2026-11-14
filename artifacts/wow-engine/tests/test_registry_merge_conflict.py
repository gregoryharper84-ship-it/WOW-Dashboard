"""The registry merge-conflict guard reacts to registry conflicts and nothing else.

A conflicted head has no merge commit, so GitHub runs no checks on it and the
pull request silently goes dirty. These guards cover the same detection the CI
step runs: which paths git reports as conflicted, and which of them are the
registry documents.
"""
import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAILURE_DOC = "docs/failure_codes.md"


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
