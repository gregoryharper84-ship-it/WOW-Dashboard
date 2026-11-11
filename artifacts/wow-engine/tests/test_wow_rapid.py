from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "wow_rapid.py"
ROOT = Path(__file__).resolve().parents[3]
RAPID_WORKFLOW = ROOT / ".github" / "workflows" / "wow-v17-rapid-repair.yml"
RECEIPT_VERIFIER = ROOT / ".github" / "scripts" / "verify_existing_pr_governance_receipt.py"
spec = importlib.util.spec_from_file_location("wow_rapid", MODULE_PATH)
assert spec and spec.loader
wow_rapid = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wow_rapid)
verifier_spec = importlib.util.spec_from_file_location("verify_existing_pr_governance_receipt", RECEIPT_VERIFIER)
assert verifier_spec and verifier_spec.loader
receipt_verifier = importlib.util.module_from_spec(verifier_spec)
verifier_spec.loader.exec_module(receipt_verifier)


def test_rapid_repair_trigger_covers_every_main_pr_path() -> None:
    workflow = yaml.load(RAPID_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    pull_request = workflow["on"]["pull_request"]

    assert pull_request["branches"] == ["main"]
    assert "paths" not in pull_request
    assert "paths-ignore" not in pull_request


def test_required_rapid_repair_identity_remains_exact_and_fail_closed() -> None:
    name = "wow-v17-rapid-repair"
    path = ".github/workflows/wow-v17-rapid-repair.yml"
    assert receipt_verifier.REQUIRED_WORKFLOWS[name] == path

    for run in (
        {
            "id": 1,
            "name": name,
            "path": path,
            "head_sha": "b" * 40,
            "created_at": "2026-10-20T00:00:00Z",
        },
        {
            "id": 2,
            "name": name,
            "path": ".github/workflows/wow-v17-rapid-repair-renamed.yml",
            "head_sha": "a" * 40,
            "created_at": "2026-10-20T00:00:00Z",
        },
    ):
        assert receipt_verifier._latest(
            [run],
            name=name,
            expected_sha="a" * 40,
            path=path,
            time_field="created_at",
        ) is None


def test_discovery_includes_all_wow_pytest_filename_patterns(tmp_path: Path) -> None:
    for name in ("test_alpha.py", "bravo_test.py", "deployment_gate_tests.py"):
        (tmp_path / name).write_text("def test_ok(): assert True\n", encoding="utf-8")
    (tmp_path / "not_a_test_module.py").write_text("x = 1\n", encoding="utf-8")

    discovered = wow_rapid.discover_test_files(tmp_path)

    assert discovered == ["bravo_test.py", "deployment_gate_tests.py", "test_alpha.py"]


def test_shards_are_disjoint_and_complete() -> None:
    files = [f"tests/test_{index}.py" for index in range(50)]
    shards = [set(wow_rapid.shard_test_files(files, index, 4)) for index in range(4)]

    assert set().union(*shards) == set(files)
    for left in range(4):
        for right in range(left + 1, 4):
            assert shards[left].isdisjoint(shards[right])


def test_sharding_is_deterministic() -> None:
    files = ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py"]
    assert wow_rapid.shard_test_files(files, 1, 3) == wow_rapid.shard_test_files(list(reversed(files)), 1, 3)


def test_affected_selection_always_preserves_governance_pack(monkeypatch, tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    for rel in wow_rapid.GOVERNANCE_TESTS:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_ok(): assert True\n", encoding="utf-8")
    (tests / "test_v17_nfl_identity.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    (tests / "test_v17_mlb_identity.py").write_text("def test_ok(): assert True\n", encoding="utf-8")

    selected = wow_rapid.select_affected_tests(
        ["artifacts/wow-engine/v17/nfl_event_identity.py"], engine_root=tmp_path
    )

    assert set(wow_rapid.GOVERNANCE_TESTS).issubset(selected)
    assert "tests/test_v17_nfl_identity.py" in selected
    assert "tests/test_v17_mlb_identity.py" not in selected


def test_affected_selection_remains_nonempty_for_unfiltered_pr_path_classes(tmp_path: Path) -> None:
    for rel in wow_rapid.GOVERNANCE_TESTS:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_ok(): assert True\n", encoding="utf-8")

    for changed_paths in (
        ["docs/README.md"],
        ["lib/backend.py"],
        [".github/workflows/wow-v17-rapid-repair-renamed.yml"],
    ):
        selected = wow_rapid.select_affected_tests(changed_paths, engine_root=tmp_path)
        assert selected
        assert set(wow_rapid.GOVERNANCE_TESTS).issubset(selected)


def test_invalid_shard_parameters_fail_closed() -> None:
    try:
        wow_rapid.shard_test_files(["tests/test_a.py"], 2, 2)
    except ValueError as exc:
        assert "0 <= index < total" in str(exc)
    else:
        raise AssertionError("invalid shard index must fail")
