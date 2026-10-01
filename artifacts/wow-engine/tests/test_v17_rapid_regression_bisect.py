from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "v17" / "rapid_regression_bisect.py"
spec = importlib.util.spec_from_file_location("v17_rapid_regression_bisect", MODULE_PATH)
assert spec and spec.loader
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


def test_find_first_bad_revision_returns_boundary_and_preserves_governance() -> None:
    revisions = ["a", "b", "c", "d", "e", "f"]
    bad = {"d", "e", "f"}

    result = subject.find_first_bad_revision(revisions, lambda revision: revision in bad)

    assert result["good_sha"] == "c"
    assert result["first_bad_sha"] == "d"
    assert result["status"] == "BISECT_COMPLETE"
    assert result["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert result["can_execute"] is False


def test_find_first_bad_revision_rejects_bad_known_good() -> None:
    with pytest.raises(subject.BisectBlocked, match="BISECT_GOOD_SHA_REPRODUCES_FAILURE"):
        subject.find_first_bad_revision(["a", "b"], lambda _revision: True)


def test_find_first_bad_revision_rejects_nonreproducing_bad_sha() -> None:
    with pytest.raises(subject.BisectBlocked, match="BISECT_BAD_SHA_DOES_NOT_REPRODUCE_FAILURE"):
        subject.find_first_bad_revision(["a", "b"], lambda _revision: False)


def test_classify_pytest_codes_rejects_flaky_fixture() -> None:
    with pytest.raises(subject.BisectBlocked, match="BISECT_FLAKY_REPRODUCTION"):
        subject._classify_pytest_codes([0, 1])


def test_classify_pytest_codes_rejects_infrastructure_exit_code() -> None:
    with pytest.raises(subject.BisectBlocked, match="BISECT_PYTEST_INFRA_FAILURE"):
        subject._classify_pytest_codes([2, 2])


def test_classify_pytest_codes_distinguishes_pass_and_failure() -> None:
    assert subject._classify_pytest_codes([0, 0]) is False
    assert subject._classify_pytest_codes([1, 1]) is True


def test_ordered_revisions_requires_bad_descendant(monkeypatch, tmp_path: Path) -> None:
    class Proc:
        stdout = "child\n"

    monkeypatch.setattr(subject.subprocess, "run", lambda *args, **kwargs: Proc())
    with pytest.raises(subject.BisectBlocked, match="BISECT_BAD_SHA_NOT_REACHABLE_FROM_GOOD_SHA"):
        subject.ordered_revisions(tmp_path, "good", "bad")


def test_ordered_revisions_enforces_bounded_range(monkeypatch, tmp_path: Path) -> None:
    class Proc:
        stdout = "b\nc\nbad\n"

    monkeypatch.setattr(subject.subprocess, "run", lambda *args, **kwargs: Proc())
    with pytest.raises(subject.BisectBlocked, match="BISECT_RANGE_TOO_LARGE"):
        subject.ordered_revisions(tmp_path, "good", "bad", max_commits=3)
