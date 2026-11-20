"""Ordinary-PR auto-merge lane: diff guard and workflow wiring."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from v17 import ordinary_merge_guard as guard

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / ".github" / "morning-green-protected-paths.txt"
WORKFLOW = ROOT / ".github" / "workflows" / "wow-v17-morning-green-continuation.yml"
HEAD = "a" * 40


def _eval(paths, *, declared="A", status="modified", head=HEAD):
    files = [{"filename": p, "status": status} for p in paths]
    return guard.evaluate(
        files=files,
        declared_class=declared,
        protected_patterns=guard.load_protected_patterns(MANIFEST),
        expected_head=HEAD,
        compared_head=head,
    )


def test_plain_reliability_change_is_eligible():
    result = _eval(["artifacts/wow-engine/v17/news_headline_normalizer.py", "docs/notes.md"])
    assert result["eligible"] is True, result["reasons"]
    assert result["can_execute"] is False


@pytest.mark.parametrize("declared", ["C", "P", "", "a", "A,B"])
def test_only_declared_class_a_or_b_is_ordinary(declared):
    result = _eval(["docs/notes.md"], declared=declared)
    assert result["eligible"] is False
    assert any(r.startswith("DECLARED_CLASS_NOT_ORDINARY") for r in result["reasons"])


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/wow-verify.yml",
        ".github/scripts/anything.py",
        ".github/morning-green-protected-paths.txt",
        "render.yaml",
        "CLAUDE.md",
    ],
)
def test_trust_root_paths_are_denied(path):
    result = _eval([path])
    assert result["eligible"] is False
    assert any(r.startswith(("TRUST_ROOT_PATH", "PROTECTED_PATH")) for r in result["reasons"])


@pytest.mark.parametrize(
    "path",
    [
        "artifacts/wow-engine/v17/independent_release_checks.py",
        "artifacts/wow-engine/v17/engineering_recovery_planner.py",
        "artifacts/wow-engine/v17/required_checks_deploy.py",
        "artifacts/wow-engine/v17/github_release_journal.py",
        "artifacts/wow-engine/v17/check_registry.py",
        "artifacts/wow-engine/v17/ordinary_merge_guard.py",
        "artifacts/wow-engine/v17/terminal_reducer.py",
        "artifacts/wow-engine/v17/nfl_passing_specialist.py",
        "artifacts/wow-engine/v17/prop_calibration_cohort.py",
        "artifacts/wow-engine/v17/spread_certification_decision.py",
        "artifacts/wow-engine/v17/nba_points_challenger.py",
        "artifacts/wow-engine/v17/qa/evidence.py",
        "artifacts/wow-engine/v17/fitted/nba.json",
        "artifacts/wow-engine/migrations/20261120_x.sql",
        "supabase/migrations/20261120_x.sql",
        "artifacts/wow-engine/v17/auth_bearer.py",
    ],
)
def test_release_review_model_and_persistence_surfaces_are_denied(path):
    result = _eval([path])
    assert result["eligible"] is False, path


def test_runtime_marker_impact_gate_is_denied():
    # hydration is a runtime marker in engineering_effectiveness, not a name token here.
    result = _eval(["artifacts/wow-engine/v17/nfl_hydration_window.py"])
    assert result["eligible"] is False
    assert any(r.startswith("IMPACT_GATE:") for r in result["reasons"])


def test_rename_source_path_is_checked():
    files = [{"filename": "docs/renamed.md", "previous_filename": ".github/workflows/x.yml", "status": "renamed"}]
    result = guard.evaluate(
        files=files, declared_class="A", protected_patterns=["render.yaml"], expected_head=HEAD, compared_head=HEAD
    )
    assert result["eligible"] is False
    assert "TRUST_ROOT_PATH:.github/workflows/x.yml" in result["reasons"]


@pytest.mark.parametrize("path", ["artifacts/wow-engine/tests/test_x.py", "artifacts/wow-engine/test_y.py", "lib/conftest.py"])
def test_deleting_a_test_is_denied(path):
    result = _eval([path], status="removed")
    assert result["eligible"] is False
    assert any(r.startswith("TEST_REMOVAL_NEEDS_REVIEW") for r in result["reasons"])


def test_head_binding_and_size_limits_fail_closed():
    assert "DIFF_NOT_BOUND_TO_EXPECTED_HEAD" in _eval(["docs/a.md"], head="b" * 40)["reasons"]
    assert "NO_DIFF" in _eval([])["reasons"]
    many = [f"docs/{i}.md" for i in range(guard.MAX_FILES)]
    assert "DIFF_TOO_LARGE_OR_TRUNCATED" in _eval(many)["reasons"]


def test_cli_exit_codes(tmp_path):
    compare = {"commits": [{"sha": HEAD}], "files": [{"filename": "docs/a.md", "status": "added"}]}
    path = tmp_path / "compare.json"
    path.write_text(json.dumps(compare))
    args = ["--compare-json", str(path), "--protected-paths", str(MANIFEST), "--expected-head", HEAD]
    assert guard.main(args + ["--declared-class", "A"]) == 0
    assert guard.main(args + ["--declared-class", "C"]) == 3
    path.write_text("{}")
    assert guard.main(args + ["--declared-class", "A"]) == 2
    path.write_text(json.dumps({"commits": [], "files": [{"filename": "docs/a.md"}]}))
    assert guard.main(args + ["--declared-class", "A"]) == 3


def test_manifest_protects_the_guard_itself():
    patterns = guard.load_protected_patterns(MANIFEST)
    assert "artifacts/wow-engine/v17/ordinary_merge_guard.py" in patterns
    assert "artifacts/wow-engine/tests/test_v17_ordinary_merge_guard.py" in patterns


# --- workflow wiring -------------------------------------------------------


def _wf() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_ordinary_lane_is_opt_in_and_class_bound():
    text = _wf()
    assert "WOW-Ordinary-Auto-Merge: true" in text
    assert "WOW-Change-Class:" in text
    assert re.search(r'case "\$change_class" in A\|B\) ;;', text)


def test_morning_green_marker_keeps_precedence():
    text = _wf()
    assert text.index("Morning-Green-Autonomous: true") < text.index("WOW-Ordinary-Auto-Merge: true")


def test_ordinary_guard_runs_from_trusted_main_on_exact_head_compare():
    text = _wf()
    assert "python -m v17.ordinary_merge_guard" in text
    assert 'compare/main...${EXPECTED_HEAD_SHA}' in text
    # The guard runs after the trusted main checkout, never from the PR head.
    assert text.index("Checkout trusted Morning-Green guard from protected main") < text.index(
        "python -m v17.ordinary_merge_guard"
    )


def test_ordinary_lane_requires_current_branch_before_merge():
    text = _wf()
    assert "ORDINARY_BRANCH_NOT_CURRENT" in text
    assert '"$mergeable_state" == "clean"' in text


def test_all_lanes_still_need_five_exact_head_checks_and_head_match():
    text = _wf()
    for name in (
        "WOW required-three regression|15368",
        "WOW governed probability backend|15368",
        "WOW additional required regression|15368",
        "WOW Independent QA exact-head|5245832",
        "WOW Release Authority exact-head|5245853",
    ):
        assert name in text
    assert '--match-head-commit "$HEAD_SHA"' in text
