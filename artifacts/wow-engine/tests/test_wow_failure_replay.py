from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "wow_failure_replay.py"
spec = importlib.util.spec_from_file_location("wow_failure_replay", MODULE_PATH)
assert spec and spec.loader
subject = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = subject
spec.loader.exec_module(subject)


def test_validate_target_allows_test_surfaces_and_rejects_escape() -> None:
    assert subject._validate_target("tests/test_example.py::test_case") == "tests/test_example.py::test_case"
    assert subject._validate_target("v17/test_example.py") == "v17/test_example.py"
    with pytest.raises(ValueError, match="TARGET_UNSAFE"):
        subject._validate_target("../secrets.txt")
    with pytest.raises(ValueError, match="OUTSIDE_TEST_SURFACE"):
        subject._validate_target("v17/scorer.py")


def test_classify_codes_requires_stable_result() -> None:
    assert subject._classify_codes([0, 0]) == ("PREPRODUCTION_REPLAY_PASS", None)
    assert subject._classify_codes([1, 1]) == ("PREPRODUCTION_REPLAY_FAIL", None)
    status, blocker = subject._classify_codes([0, 1])
    assert status == "BLOCKED_WITH_EXACT_REASON"
    assert blocker == "FAILURE_CAPSULE_REPLAY_FLAKY:[0, 1]"
    status, blocker = subject._classify_codes([2, 2])
    assert status == "BLOCKED_WITH_EXACT_REASON"
    assert blocker == "FAILURE_CAPSULE_REPLAY_INFRA_FAILURE:[2]"


def test_run_replay_blocks_capsule_without_deterministic_targets(tmp_path: Path) -> None:
    capsule = {
        "fingerprint": "fp-missing-targets",
        "reproduction": {},
        "can_execute": False,
    }
    capsule_path = tmp_path / "capsule.json"
    capsule_path.write_text(json.dumps(capsule), encoding="utf-8")

    result = subject.run_replay(capsule_path=capsule_path, engine_root=tmp_path)

    assert result["status"] == "BLOCKED_WITH_EXACT_REASON"
    assert result["blocker"] == "FAILURE_CAPSULE_DETERMINISTIC_PYTEST_TARGETS_UNAVAILABLE"
    assert result["can_execute"] is False
    assert result["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_run_replay_emits_pass_receipt_after_two_stable_runs(monkeypatch, tmp_path: Path) -> None:
    capsule = {
        "fingerprint": "fp-pass",
        "terminal_code": "NFL_FITTED_SCORER_FAILED",
        "reproduction": {
            "pytest": ["tests/test_fixture.py::test_reproduction"],
        },
        "can_execute": False,
    }
    capsule_path = tmp_path / "capsule.json"
    capsule_path.write_text(json.dumps(capsule), encoding="utf-8")

    class Proc:
        returncode = 0
        stdout = "abc123\n"

    monkeypatch.setattr(subject.subprocess, "run", lambda *args, **kwargs: Proc())
    result = subject.run_replay(capsule_path=capsule_path, engine_root=tmp_path)

    assert result["status"] == "PREPRODUCTION_REPLAY_PASS"
    assert result["return_codes"] == [0, 0]
    assert result["pytest"] == ["tests/test_fixture.py::test_reproduction"]
    assert result["can_execute"] is False


def test_run_replay_rejects_flaky_patch(monkeypatch, tmp_path: Path) -> None:
    capsule = {
        "fingerprint": "fp-flaky",
        "reproduction": {"pytest": ["tests/test_fixture.py"]},
    }
    capsule_path = tmp_path / "capsule.json"
    capsule_path.write_text(json.dumps(capsule), encoding="utf-8")
    calls = iter([0, 1])

    class Proc:
        stdout = "abc123\n"

        def __init__(self, returncode: int):
            self.returncode = returncode

    def fake_run(args, **kwargs):
        if args[:3] == ["git", "rev-parse", "HEAD"]:
            proc = Proc(0)
            proc.stdout = "abc123\n"
            return proc
        return Proc(next(calls))

    monkeypatch.setattr(subject.subprocess, "run", fake_run)
    result = subject.run_replay(capsule_path=capsule_path, engine_root=tmp_path)

    assert result["status"] == "BLOCKED_WITH_EXACT_REASON"
    assert result["blocker"] == "FAILURE_CAPSULE_REPLAY_FLAKY:[0, 1]"
    assert result["can_execute"] is False
