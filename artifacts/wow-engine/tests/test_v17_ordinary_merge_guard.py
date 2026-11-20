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


# --- ordinary sweep (schedule) --------------------------------------------

import os
import subprocess

import yaml

REPO = "gregoryharper84-ship-it/WOW-Dashboard"


def _sweep_script() -> str:
    jobs = yaml.safe_load(_wf())["jobs"]
    return jobs["ordinary-sweep"]["steps"][0]["run"]


def _pr(n, *, state="clean", body="WOW-Ordinary-Auto-Merge: true\nWOW-Change-Class: A", draft=False,
        repo=REPO, created="2026-11-20T10:00:00Z"):
    return {"number": n, "draft": draft, "body": body, "created_at": created, "mergeable_state": state,
            "head": {"sha": f"{n:040d}", "ref": f"branch-{n}", "repo": {"full_name": repo}}}


def _run_sweep(tmp_path, prs, *, fail_reads=False):
    routes = {"list": prs, **{str(p["number"]): p for p in prs}}
    (tmp_path / "routes.json").write_text(json.dumps(routes))
    log = tmp_path / "calls.log"
    gh = tmp_path / "gh"
    gh.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "open(os.environ['CALL_LOG'], 'a').write(' '.join(args) + '\\n')\n"
        "routes = json.load(open(os.environ['ROUTES']))\n"
        "if os.environ.get('FAIL_READS') == '1': sys.exit(1)\n"
        "if args[0] == 'api' and '-X' not in args:\n"
        "    path = [a for a in args if a.startswith('/repos/')][0]\n"
        "    key = 'list' if '/pulls?' in path else path.rsplit('/', 1)[1]\n"
        "    print(json.dumps(routes[key]))\n"
    )
    gh.chmod(0o755)
    (tmp_path / "sleep").write_text("#!/bin/sh\nexit 0\n")
    (tmp_path / "sleep").chmod(0o755)
    summary = tmp_path / "summary"
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", ROUTES=str(tmp_path / "routes.json"),
               CALL_LOG=str(log), GITHUB_REPOSITORY=REPO, GITHUB_STEP_SUMMARY=str(summary), GH_TOKEN="test",
               FAIL_READS="1" if fail_reads else "0")
    proc = subprocess.run(["bash", "-c", _sweep_script()], env=env, capture_output=True, text=True, timeout=20)
    calls = log.read_text().splitlines() if log.exists() else []
    return proc, calls, summary.read_text() if summary.exists() else ""


def test_sweep_runs_only_on_schedule_and_continuation_never_does():
    jobs = yaml.safe_load(_wf())["jobs"]
    assert jobs["ordinary-sweep"]["if"] == "github.event_name == 'schedule'"
    assert "github.event_name == 'workflow_run'" in jobs["continue-repair"]["if"]
    assert "schedule" in yaml.safe_load(_wf())[True]


def test_sweep_dispatches_continuation_for_clean_pr(tmp_path):
    proc, calls, summary = _run_sweep(tmp_path, [_pr(11)])
    assert proc.returncode == 0, proc.stderr
    assert any(c.startswith("workflow run wow-v17-morning-green-continuation.yml") and "pr_number=11" in c for c in calls)
    assert "ORDINARY_SWEEP_CONTINUATION_DISPATCHED:#11" in summary


def test_sweep_updates_behind_pr_and_dispatches_required_ci_on_its_branch(tmp_path):
    proc, calls, summary = _run_sweep(tmp_path, [_pr(12, state="behind")])
    assert proc.returncode == 0, proc.stderr
    update = [c for c in calls if "update-branch" in c]
    assert update and f"expected_head_sha={12:040d}" in update[0]
    assert any("workflow run wow-verify.yml" in c and "--ref branch-12" in c for c in calls)
    assert any("workflow run wow-engine-verify.yml" in c and "--ref branch-12" in c for c in calls)
    assert not any("morning-green-continuation" in c for c in calls)


def test_sweep_waits_on_other_states_and_skips_non_candidates(tmp_path):
    prs = [
        _pr(13, state="blocked"),
        _pr(14, draft=True),
        _pr(15, repo="fork/WOW-Dashboard"),
        _pr(16, body="WOW-Change-Class: A"),
        _pr(17, body="Morning-Green-Autonomous: true\nWOW-Ordinary-Auto-Merge: true"),
        _pr(18, body="x WOW-Ordinary-Auto-Merge: true"),
    ]
    proc, calls, summary = _run_sweep(tmp_path, prs)
    assert proc.returncode == 0, proc.stderr
    assert "ORDINARY_SWEEP_WAIT:#13:blocked" in summary
    assert not any(c.startswith("workflow run") or "update-branch" in c for c in calls)
    for n in (14, 15, 16, 17, 18):
        assert f"#{n}" not in summary


def test_sweep_caps_at_five_oldest(tmp_path):
    prs = [_pr(20 + i, created=f"2026-11-20T1{i}:00:00Z") for i in range(7)]
    proc, calls, summary = _run_sweep(tmp_path, prs)
    dispatched = sorted(int(c.split("pr_number=")[1]) for c in calls if "pr_number=" in c)
    assert dispatched == [20, 21, 22, 23, 24]


def test_sweep_never_fails_main_on_api_errors(tmp_path):
    proc, calls, summary = _run_sweep(tmp_path, [_pr(30)], fail_reads=True)
    assert proc.returncode == 0, proc.stderr
    assert "ORDINARY_SWEEP_NO_CANDIDATES" in summary
