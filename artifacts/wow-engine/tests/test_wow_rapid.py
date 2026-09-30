from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "wow_rapid.py"
spec = importlib.util.spec_from_file_location("wow_rapid", MODULE_PATH)
assert spec and spec.loader
wow_rapid = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wow_rapid)


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


def test_invalid_shard_parameters_fail_closed() -> None:
    try:
        wow_rapid.shard_test_files(["tests/test_a.py"], 2, 2)
    except ValueError as exc:
        assert "0 <= index < total" in str(exc)
    else:
        raise AssertionError("invalid shard index must fail")
