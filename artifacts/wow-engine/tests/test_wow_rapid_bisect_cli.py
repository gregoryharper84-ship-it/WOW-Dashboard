from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "wow_rapid.py"
spec = importlib.util.spec_from_file_location("wow_rapid_bisect_cli", MODULE_PATH)
assert spec and spec.loader
subject = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = subject
spec.loader.exec_module(subject)


def test_run_regression_bisect_emits_governed_complete_receipt(monkeypatch, tmp_path: Path) -> None:
    class Blocked(RuntimeError):
        pass

    def fake_run_bisect(repo_root, good_sha, bad_sha, pytest_targets, **kwargs):
        assert repo_root == tmp_path.resolve()
        assert good_sha == "good"
        assert bad_sha == "bad"
        assert pytest_targets == ["tests/test_case.py::test_repro"]
        assert kwargs["max_commits"] == 12
        assert kwargs["stability_runs"] == 3
        return {
            "status": "BISECT_COMPLETE",
            "good_sha": "middle-good",
            "first_bad_sha": "first-bad",
        }

    monkeypatch.setattr(subject, "_load_bisect_engine", lambda: (Blocked, fake_run_bisect))
    result = subject.run_regression_bisect(
        repo_root=tmp_path,
        good_sha="good",
        bad_sha="bad",
        pytest_targets=["tests/test_case.py::test_repro"],
        max_commits=12,
        stability_runs=3,
    )

    assert result["status"] == "BISECT_COMPLETE"
    assert result["first_bad_sha"] == "first-bad"
    assert result["runtime_generation"] == "V17_ACTIVE"
    assert result["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert result["can_execute"] is False


def test_run_regression_bisect_preserves_exact_blocker(monkeypatch, tmp_path: Path) -> None:
    class Blocked(RuntimeError):
        pass

    def fake_run_bisect(*args, **kwargs):
        raise Blocked("BISECT_FLAKY_REPRODUCTION:[0, 1]")

    monkeypatch.setattr(subject, "_load_bisect_engine", lambda: (Blocked, fake_run_bisect))
    result = subject.run_regression_bisect(
        repo_root=tmp_path,
        good_sha="good",
        bad_sha="bad",
        pytest_targets=["tests/test_case.py"],
    )

    assert result == {
        "status": "BLOCKED_WITH_EXACT_REASON",
        "blocker": "BISECT_FLAKY_REPRODUCTION:[0, 1]",
        "good_sha": "good",
        "bad_sha": "bad",
        "pytest_targets": ["tests/test_case.py"],
        "runtime_generation": "V17_ACTIVE",
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }


def test_write_json_is_machine_readable(tmp_path: Path) -> None:
    output = tmp_path / "receipt.json"
    subject._write_json({"status": "BISECT_COMPLETE", "can_execute": False}, str(output))
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "status": "BISECT_COMPLETE",
        "can_execute": False,
    }


def test_cli_source_exposes_explicit_bisect_arguments() -> None:
    text = MODULE_PATH.read_text(encoding="utf-8")
    assert 'sub.add_parser("bisect"' in text
    assert 'bisect.add_argument("--good", required=True)' in text
    assert 'bisect.add_argument("--bad", required=True)' in text
    assert 'bisect.add_argument("--target", action="append", dest="targets", required=True)' in text
    assert 'return 0 if receipt.get("status") == "BISECT_COMPLETE" else 1' in text
