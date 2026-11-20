"""Drift guard: every check name / App ID in workflows and modules matches the registry.

Redesign R3 (2026-11-20). If this test fails, a check was renamed, a GitHub App
was re-created with a new ID, or a typo crept into a gate. Fix the registry
(``v17/check_registry.py``) or the drifted copy, never the test.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from v17 import check_registry as reg

ROOT = Path(__file__).resolve().parents[3]
WF = ROOT / ".github" / "workflows"
ENGINE = ROOT / "artifacts" / "wow-engine"
WORKFLOWS = sorted(WF.glob("*.yml"))

PAIR_RE = re.compile(r'"(WOW [^"|]+)\|(\d+)"')
JQ_RE = re.compile(r'\.name == "(WOW [^"]+)" and \.app\.id == (\d+)')
NAME_RE = re.compile(r'"(WOW [A-Za-z0-9 -]+? (?:exact-head|regression|backend|impact gate|Security Review))"')


def test_classes_are_disjoint_and_match_the_ruleset_required_set():
    assert set(reg.REQUIRED) == {
        "WOW required-three regression", "WOW governed probability backend",
        "WOW additional required regression"}
    assert set(reg.REQUIRED).isdisjoint(reg.ADVISORY)
    assert set(reg.REQUIRED).isdisjoint(reg.EVIDENCE)
    assert set(reg.ADVISORY).isdisjoint(reg.EVIDENCE)
    assert all(reg.app_id(n) == reg.GITHUB_ACTIONS_APP_ID for n in reg.REQUIRED)
    assert not any(reg.is_required(n) for n in (*reg.ADVISORY, *reg.EVIDENCE))


@pytest.mark.parametrize("workflow, job_name", [
    ("wow-verify.yml", "WOW required-three regression"),
    ("wow-verify.yml", "WOW additional required regression"),
    ("wow-engine-verify.yml", "WOW governed probability backend"),
    ("wow-v17-change-impact-gate.yml", "WOW V17 change impact gate"),
])
def test_producers_publish_exactly_the_registered_names(workflow, job_name):
    assert job_name in reg.ALL
    assert re.search(rf"^\s+name: {re.escape(job_name)}\s*$", (WF / workflow).read_text(), re.M), \
        f"{workflow} no longer produces {job_name!r}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_name_app_pairs_match_the_registry(path):
    text = path.read_text(encoding="utf-8")
    for regex in (PAIR_RE, JQ_RE):
        for name, app in regex.findall(text):
            assert name in reg.ALL, f"{path.name}: unregistered check {name!r}"
            assert int(app) == reg.app_id(name), f"{path.name}: {name!r} paired with App {app}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_check_name_literals_are_registered(path):
    for name in NAME_RE.findall(path.read_text(encoding="utf-8")):
        assert name in reg.ALL, f"{path.name}: unregistered or misspelled check {name!r}"


def _constants(path: Path) -> dict:
    """Module-level literal assignments, read statically (no import side effects)."""
    out = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                continue
    return out


def _module_path(module: str) -> Path:
    return ENGINE / (module.replace(".", "/") + ".py")


@pytest.mark.parametrize("module, attribute, expected", [
    ("v17.engineering_hold_target", "QA_CHECK", "WOW Independent QA exact-head"),
    ("v17.engineering_hold_target", "RELEASE_CHECK", "WOW Release Authority exact-head"),
    ("v17.engineering_delivery_pipeline", "QA_APP_ID", 5245832),
    ("v17.engineering_delivery_pipeline", "RELEASE_APP_ID", 5245853),
    ("v17.engineering_delivery_pipeline", "QA_CHECK", "WOW Independent Security Review"),
    ("v17.engineering_delivery_pipeline", "INDEPENDENT_QA_CHECK", "WOW Independent QA exact-head"),
    ("v17.engineering_delivery_pipeline", "RELEASE_CHECK", "WOW Release Authority exact-head"),
    ("v17.factory_closed_loop_acceptance", "QA_APP_ID", 5245832),
    ("v17.factory_closed_loop_acceptance", "RELEASE_APP_ID", 5245853),
    ("v17.github_release_journal", "AUTHENTIC_QA_APP_ID", 5245832),
    ("v17.github_release_journal", "AUTHENTIC_RELEASE_APP_ID", 5245853),
    ("v17.governance_dispatcher", "GITHUB_ACTIONS_APP_ID", 15368),
    ("v17.governance_dispatcher", "CHANGE_IMPACT_CHECK", "WOW V17 change impact gate"),
    ("v17.engineering_authorized_adoption", "ACTIONS_APP_ID", 15368),
    ("v17.independent_release_checks", "CHANGE_IMPACT_CHECK", "WOW V17 change impact gate"),
    ("v17.required_checks_deploy", "GITHUB_ACTIONS_APP_ID", 15368),
])
def test_module_copies_match_the_registry(module, attribute, expected):
    value = _constants(_module_path(module))[attribute]
    assert value == expected
    # And the expectation itself is the registry's value, not a second copy.
    if isinstance(expected, str):
        assert expected in reg.ALL
    else:
        assert expected in set(reg.ALL.values())


def test_ruleset_migration_and_security_review_maps_match_the_registry():
    migration = _constants(_module_path("v17.lean_release_ruleset_migration"))
    apps = {"ACTIONS_APP": migration["ACTIONS_APP"], "QA_APP": migration["QA_APP"],
            "RELEASE_APP": migration["RELEASE_APP"]}
    assert apps == {"ACTIONS_APP": reg.GITHUB_ACTIONS_APP_ID, "QA_APP": reg.QA_APP_ID,
                    "RELEASE_APP": reg.RELEASE_APP_ID}
    src = _module_path("v17.lean_release_ruleset_migration").read_text(encoding="utf-8")
    for name, var in re.findall(r'"(WOW [^"]+)": (ACTIONS_APP|QA_APP|RELEASE_APP)', src):
        assert reg.app_id(name) == apps[var], name
    review = _constants(ROOT / ".github/scripts/wow_simple_security_review.py")
    assert review["CHECK_NAME"] == reg.SECURITY_REVIEW_CHECK


def test_cli_emits_bash_pairs_and_json():
    run = lambda *a: subprocess.run([sys.executable, "-m", "v17.check_registry", *a], cwd=ENGINE,
                                    capture_output=True, text=True, check=True).stdout
    pairs = run("--class", "required", "--format", "pairs").splitlines()
    assert sorted(pairs) == sorted(f"{n}|15368" for n in reg.REQUIRED)
    assert json.loads(run("--class", "advisory")) == reg.ADVISORY


def test_guard_catches_a_drifted_pair(tmp_path):
    bad = '"WOW Release Authority exact-head|5245832"'
    name, app = PAIR_RE.findall(bad)[0]
    assert int(app) != reg.app_id(name)
    assert NAME_RE.findall('"WOW Independant QA exact-head"') == ["WOW Independant QA exact-head"]
    assert "WOW Independant QA exact-head" not in reg.ALL
