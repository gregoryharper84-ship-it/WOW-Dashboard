"""Regression for deterministic evidence admission in the Claude worker (#1021, incident #1388).

Run 39844630386 admitted ``:workspace`` mutation on an LLM-only triage claim with no
reproduction. Triage ``reproduction_status`` is model-origin; implementation must only be
admitted after the worker itself re-runs a tracked failing regression. These tests execute
the protected workflow's actual Bash in a disposable git repository. No provider is invoked.
"""
import os
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/wow-v17-claude-engineering-worker.yml"
STEP_NAME = "Corroborate triage reproduction deterministically"


def _steps():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    return workflow["jobs"]["multi-agent-engineering"]["steps"]


def _by_name():
    return {step.get("name"): (i, step) for i, step in enumerate(_steps())}


# --- structure -------------------------------------------------------------


def test_triage_schema_requires_reproduction_test_identity():
    _, triage = _by_name()["Research / Root-Cause Triage agent"]
    schema = triage["with"]["output_schema"]
    assert '"reproduction_test":{"type":"string"}' in schema
    assert '"reproduction_status","reproduction_test"' in schema


def test_corroboration_runs_after_triage_and_specialist_before_mutation_gate():
    by_name = _by_name()
    triage_idx, _ = by_name["Normalize Triage packet"]
    specialist_idx, _ = by_name["Normalize specialist packet"]
    step_idx, step = by_name[STEP_NAME]
    gate_idx, _ = by_name["Enforce mutation safety gate"]
    impl_idx, _ = by_name["Implementation agent"]
    assert triage_idx < specialist_idx < step_idx < gate_idx < impl_idx
    assert step["id"] == "reproduction"
    assert step.get("continue-on-error", False) is False


def test_corroboration_step_receives_no_secret_or_token():
    _, step = _by_name()[STEP_NAME]
    assert set(step["env"]) == {"REPRODUCTION", "REPRODUCTION_TEST"}
    assert "secrets." not in yaml.safe_dump(step)
    assert "${{" not in step["run"]


@pytest.mark.parametrize("name", ["Enforce mutation safety gate", "Implementation agent"])
def test_mutation_and_workspace_agent_require_deterministic_corroboration(name):
    _, step = _by_name()[name]
    assert "steps.reproduction.outputs.corroborated == 'true'" in step["if"]


# --- triage normalization --------------------------------------------------


def _normalize(tmp_path, result):
    _, step = _by_name()["Normalize Triage packet"]
    out = tmp_path / "triage-output.txt"
    (tmp_path / "artifacts/wow-engine/v17/.agent-handoff").mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, RESULT=result, EXPECTED_INCIDENT="1388", GITHUB_OUTPUT=str(out))
    proc = subprocess.run(["bash", "-c", step["run"]], cwd=tmp_path, env=env,
                          text=True, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stderr
    return dict(line.split("=", 1) for line in out.read_text().splitlines())


def _triage(status, test):
    import json
    return json.dumps({
        "incident_id": "1388", "repairable": True, "reproduction_status": status,
        "reproduction_test": test, "root_cause": "x", "risk_class": "R1",
        "primary_subsystem": "engine", "typed_failure": "X", "acceptance_criteria": [],
        "recommended_fix_boundary": "x", "hard_boundary": "x",
    })


@pytest.mark.parametrize(
    "status, test, expected_status, expected_test",
    [
        (" reproduced ", "test_a.py::test_b", "REPRODUCED", "test_a.py::test_b"),
        ("Not yet proven", "NONE", "UNRECOGNIZED", "NONE"),
        ("INSUFFICIENT_EVIDENCE", "test_a.py\n::x", "INSUFFICIENT_EVIDENCE", "INVALID"),
        ("REPRODUCED", "test_a.py; rm -rf /", "REPRODUCED", "INVALID"),
    ],
)
def test_triage_normalization_bounds_llm_fields(tmp_path, status, test, expected_status, expected_test):
    outputs = _normalize(tmp_path, _triage(status, test))
    assert outputs["reproduction"] == expected_status
    assert outputs["reproduction_test"] == expected_test


# --- deterministic corroboration behaviour ---------------------------------


@pytest.fixture()
def repo(tmp_path):
    engine = tmp_path / "artifacts/wow-engine"
    engine.mkdir(parents=True)
    (engine / "test_incident.py").write_text(
        "def test_red():\n    assert 0 == 5\n\n\ndef test_green():\n    assert True\n"
    )
    (engine / "test_untracked.py").write_text("def test_red():\n    assert False\n")
    (engine / "helper.py").write_text("def f():\n    return 1\n")
    (engine / "test_broken.py").write_text("import does_not_exist_module\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "artifacts/wow-engine/test_incident.py",
                    "artifacts/wow-engine/helper.py", "artifacts/wow-engine/test_broken.py"],
                   cwd=tmp_path, check=True)
    return engine


def _corroborate(engine, status, test):
    _, step = _by_name()[STEP_NAME]
    out = engine / "corroboration-output.txt"
    out.unlink(missing_ok=True)
    env = dict(os.environ, REPRODUCTION=status, REPRODUCTION_TEST=test,
               GITHUB_OUTPUT=str(out), GITHUB_STEP_SUMMARY=str(engine / "summary.txt"),
               RUNNER_TEMP=str(engine.parent))
    proc = subprocess.run(["bash", "-c", dedent(step["run"])], cwd=engine, env=env,
                          text=True, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return dict(line.split("=", 1) for line in out.read_text().splitlines())


@pytest.mark.parametrize("status", ["REPRODUCED", "INTERMITTENT", "ENVIRONMENT_SPECIFIC"])
def test_observed_red_regression_is_admitted(repo, status):
    outputs = _corroborate(repo, status, "test_incident.py::test_red")
    assert outputs == {"corroborated": "true", "reason": "RED_OBSERVED:test_incident.py::test_red"}


@pytest.mark.parametrize(
    "status, test, reason",
    [
        # Incident #1388: self-reported, unproven status is never admission evidence.
        ("INSUFFICIENT_EVIDENCE", "test_incident.py::test_red", "STATUS_NOT_ADMISSIBLE:INSUFFICIENT_EVIDENCE"),
        ("NOT_REPRODUCED", "test_incident.py::test_red", "STATUS_NOT_ADMISSIBLE:NOT_REPRODUCED"),
        ("", "test_incident.py::test_red", "STATUS_NOT_ADMISSIBLE:MISSING"),
        # An LLM claiming REPRODUCED without a failing test is rejected.
        ("REPRODUCED", "NONE", "TEST_NODE_INVALID"),
        ("REPRODUCED", "INVALID", "TEST_NODE_INVALID"),
        ("REPRODUCED", "../test_incident.py::test_red", "TEST_NODE_INVALID"),
        ("REPRODUCED", "-p::x.py", "TEST_NODE_INVALID"),
        ("REPRODUCED", "helper.py", "TEST_NODE_NOT_A_TEST_MODULE"),
        ("REPRODUCED", "test_untracked.py::test_red", "TEST_NODE_UNTRACKED"),
        ("REPRODUCED", "test_missing.py::test_red", "TEST_NODE_UNTRACKED"),
        ("REPRODUCED", "test_incident.py::test_green", "TEST_PASSED_NOT_REPRODUCED"),
        ("REPRODUCED", "test_broken.py", "TEST_ERROR_EXIT_2"),
        ("REPRODUCED", "test_incident.py::test_absent", "TEST_ERROR_EXIT_4"),
    ],
)
def test_uncorroborated_claims_are_denied_with_exact_reason(repo, status, test, reason):
    outputs = _corroborate(repo, status, test)
    assert outputs == {"corroborated": "false", "reason": reason}
