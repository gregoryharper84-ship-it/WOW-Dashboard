"""#1484 — Scout structured-source refresh must never report healthy when nothing was acquired.

These tests execute the real CLI and the real workflow step scripts; they do not
assert on source strings.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from v17 import scout_source_refresh as ssr

ENGINE = Path(__file__).resolve().parent
ROOT = ENGINE.parents[1]
WORKFLOW = ROOT / ".github/workflows/wow-v17-scout-source-refresh.yml"
SECRET = "sk-SENTINEL-must-never-appear-1484"
CRED_VARS = ("WOW_SCOUT_DATABASE_URL", "DATABASE_URL", "SPORTSDATAIO_API_KEY", "WOW_SPORTSDATAIO_API_KEY")


@dataclass
class Cap:
    sport_key: str
    capability: str
    endpoint_template: str | None = "/x"
    supported: bool = True
    provider: str = "SPORTSDATAIO"


@dataclass
class Result:
    ok: bool
    sport_key: str
    capability: str
    code: str


def _clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in CRED_VARS}
    env.update(extra)
    return env


def _run_cli(tmp_path: Path, env: dict[str, str]) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    out = tmp_path / "receipt.json"
    proc = subprocess.run([sys.executable, "v17/scout_source_refresh.py", "--output", str(out)],
                          cwd=ENGINE, env=env, capture_output=True, text=True, timeout=60)
    return proc, json.loads(out.read_text(encoding="utf-8"))


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WOW_SCOUT_DATABASE_URL", "postgresql://user:" + SECRET + "@127.0.0.1:1/db")
    monkeypatch.setenv("WOW_SPORTSDATAIO_API_KEY", SECRET)


def _install(monkeypatch: pytest.MonkeyPatch, caps: list[Cap], fetch, persist) -> None:
    monkeypatch.setattr(ssr, "SPORTSDATAIO_CAPABILITIES", caps)
    monkeypatch.setattr(ssr, "sportsdataio_fetch", fetch)
    monkeypatch.setattr(ssr, "persist_source_result", persist)


def _ok_fetch(sport_key, capability, path_values=None):
    return Result(True, sport_key, capability, "SOURCE_FETCH_OK")


def _persist(result):
    return {"snapshot_id": f"src_{result.sport_key}_{result.capability}", "sport_key": result.sport_key,
            "capability": result.capability, "source_status": "OK" if result.ok else "BLOCKED", "source_code": result.code}


def _assert_governed(receipt: dict[str, Any]) -> None:
    assert receipt["prediction_authority"] is False
    assert receipt["can_execute"] is False
    assert receipt["schema_version"] == "wow.v17.scout_source_refresh.v2"
    assert SECRET not in json.dumps(receipt)


# --- Real CLI execution: missing configuration --------------------------------------------

def test_cli_missing_all_configuration_is_typed_blocked_and_exits_nonzero(tmp_path):
    proc, receipt = _run_cli(tmp_path, _clean_env())
    assert proc.returncode == ssr.EXIT_BLOCKED, proc.stderr
    assert receipt["status"] == "BLOCKED_SOURCE_CONFIGURATION"
    assert receipt["health"] == "BLOCKED"
    assert receipt["configuration"] == {"database_configured": False, "sportsdataio_configured": False}
    assert receipt["attempted_count"] == 0 and receipt["successful_count"] == 0
    assert "SOURCE_CONFIGURATION_MISSING" in receipt["blocker_codes"]
    assert "::error::" in proc.stdout
    _assert_governed(receipt)


def test_cli_partial_configuration_is_blocked_without_touching_db_or_provider(tmp_path):
    # A real DB URL value would hang/raise if any connection were attempted; it must not be.
    env = _clean_env(WOW_SCOUT_DATABASE_URL="postgresql://u:" + SECRET + "@10.255.255.1:5432/db")
    proc, receipt = _run_cli(tmp_path, env)
    assert proc.returncode == ssr.EXIT_BLOCKED
    assert receipt["status"] == "BLOCKED_SOURCE_CONFIGURATION"
    assert receipt["configuration"] == {"database_configured": True, "sportsdataio_configured": False}
    assert SECRET not in proc.stdout + proc.stderr
    _assert_governed(receipt)


# --- In-process typed outcomes ------------------------------------------------------------

def test_all_capabilities_persisted_ok_is_healthy(monkeypatch, tmp_path, capsys):
    _configure(monkeypatch)
    _install(monkeypatch, [Cap("americanfootball_nfl", "injuries"), Cap("baseball_mlb", "injuries")], _ok_fetch, _persist)
    code = ssr.main(["--sports", "americanfootball_nfl,baseball_mlb", "--output", str(tmp_path / "r.json")])
    receipt = json.loads((tmp_path / "r.json").read_text())
    assert code == 0
    assert (receipt["status"], receipt["health"]) == ("COMPLETE", "HEALTHY")
    assert receipt["successful_count"] == 2 and receipt["blocked_count"] == 0
    assert all(s["persisted"] is True for s in receipt["successful_snapshots"])
    _assert_governed(receipt)


def test_partial_acquisition_is_degraded_not_healthy(monkeypatch, tmp_path, capsys):
    _configure(monkeypatch)

    def fetch(sport_key, capability, path_values=None):
        return Result(sport_key == "americanfootball_nfl", sport_key, capability,
                      "SOURCE_FETCH_OK" if sport_key == "americanfootball_nfl" else "SOURCE_ENDPOINT_UNCONFIGURED")

    _install(monkeypatch, [Cap("americanfootball_nfl", "injuries"), Cap("basketball_wnba", "injuries")], fetch, _persist)
    code = ssr.main(["--sports", "americanfootball_nfl,basketball_wnba", "--output", str(tmp_path / "r.json")])
    receipt = json.loads((tmp_path / "r.json").read_text())
    assert code == 0
    assert (receipt["status"], receipt["health"]) == ("COMPLETE_WITH_BLOCKERS", "DEGRADED")
    assert receipt["blocker_codes"] == ["SOURCE_ENDPOINT_UNCONFIGURED"]
    assert "::warning::" in capsys.readouterr().out


def test_configured_but_every_capability_blocked_is_blocked_not_complete(monkeypatch, tmp_path):
    """Regression: v1 reported COMPLETE_WITH_BLOCKERS (exit 0) with zero acquired snapshots."""
    _configure(monkeypatch)
    blocked_fetch = lambda s, c, path_values=None: Result(False, s, c, "SPORTSDATAIO_HTTP_401")  # noqa: E731
    _install(monkeypatch, [Cap("americanfootball_nfl", "injuries"), Cap("baseball_mlb", "injuries")], blocked_fetch, _persist)
    code = ssr.main(["--sports", "americanfootball_nfl,baseball_mlb", "--output", str(tmp_path / "r.json")])
    receipt = json.loads((tmp_path / "r.json").read_text())
    assert code == ssr.EXIT_BLOCKED
    assert (receipt["status"], receipt["health"]) == ("BLOCKED_SOURCE_ACQUISITION", "BLOCKED")
    assert receipt["successful_count"] == 0 and receipt["blocked_count"] == 2


def test_configured_but_zero_attempted_is_blocked_not_complete(monkeypatch, tmp_path):
    """Regression: v1 reported COMPLETE with nothing attempted."""
    _configure(monkeypatch)
    _install(monkeypatch, [Cap("americanfootball_nfl", "injuries", supported=False)], _ok_fetch, _persist)
    code = ssr.main(["--sports", "americanfootball_nfl", "--output", str(tmp_path / "r.json")])
    receipt = json.loads((tmp_path / "r.json").read_text())
    assert code == ssr.EXIT_BLOCKED
    assert receipt["status"] == "BLOCKED_SOURCE_ACQUISITION" and receipt["attempted_count"] == 0


def test_database_outage_is_typed_and_leaks_no_secret(monkeypatch, tmp_path, capsys):
    _configure(monkeypatch)

    def persist_down(result):
        raise ConnectionError("could not connect to postgresql://user:" + SECRET + "@db")

    _install(monkeypatch, [Cap("americanfootball_nfl", "injuries")], _ok_fetch, persist_down)
    code = ssr.main(["--sports", "americanfootball_nfl", "--output", str(tmp_path / "r.json")])
    receipt = json.loads((tmp_path / "r.json").read_text())
    assert code == ssr.EXIT_BLOCKED
    assert receipt["status"] == "BLOCKED_SOURCE_ACQUISITION"
    assert receipt["blocked_snapshots"][0]["source_code"] == "PERSISTENCE_EXCEPTION:ConnectionError"
    assert receipt["blocked_snapshots"][0]["persisted"] is False
    assert SECRET not in capsys.readouterr().out
    _assert_governed(receipt)


def test_unexpected_fetch_exception_is_typed_per_capability(monkeypatch, tmp_path):
    _configure(monkeypatch)

    def fetch(sport_key, capability, path_values=None):
        if sport_key == "baseball_mlb":
            raise ValueError("boom " + SECRET)
        return Result(True, sport_key, capability, "SOURCE_FETCH_OK")

    _install(monkeypatch, [Cap("americanfootball_nfl", "injuries"), Cap("baseball_mlb", "injuries")], fetch, _persist)
    code = ssr.main(["--sports", "americanfootball_nfl,baseball_mlb", "--output", str(tmp_path / "r.json")])
    receipt = json.loads((tmp_path / "r.json").read_text())
    assert code == 0 and receipt["health"] == "DEGRADED"
    assert receipt["blocker_codes"] == ["FETCH_EXCEPTION:ValueError"]
    _assert_governed(receipt)


def test_refresh_abort_still_writes_typed_receipt(monkeypatch, tmp_path):
    _configure(monkeypatch)

    def explode(**_kwargs):
        raise RuntimeError("abort " + SECRET)

    monkeypatch.setattr(ssr, "refresh", explode)
    out = tmp_path / "nested" / "r.json"
    code = ssr.main(["--output", str(out)])
    receipt = json.loads(out.read_text())
    assert code == ssr.EXIT_BLOCKED
    assert receipt["status"] == "BLOCKED_REFRESH_EXCEPTION" and receipt["error_type"] == "RuntimeError"
    _assert_governed(receipt)


@pytest.mark.parametrize("health", [None, "", "UNKNOWN", "healthy"])
def test_unknown_health_fails_closed(health):
    assert ssr.exit_code_for({"health": health}) == ssr.EXIT_BLOCKED


# --- Workflow: structure plus real execution of its step scripts ----------------------------

def _steps() -> dict[str, dict[str, Any]]:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = wf["jobs"]["refresh"]
    assert job["env"]["WOW_CAN_EXECUTE"] == "false"
    return {s.get("name", s.get("uses")): s for s in job["steps"]}


def test_workflow_never_gates_refresh_or_receipt_on_credentials():
    steps = _steps()
    refresh = steps["Refresh raw structured source snapshots"]
    assert "if" not in refresh, "refresh must always run; the CLI types missing configuration"
    for name in ("Ensure typed source refresh receipt exists", "Summarize source refresh receipt", "Upload source refresh receipt"):
        assert steps[name]["if"] == "always()", name
    assert steps["Upload source refresh receipt"]["with"]["if-no-files-found"] == "error"
    for step in steps.values():
        assert "credentials.outputs" not in str(step.get("if", ""))


def _bash(script: str, runner_temp: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = _clean_env(RUNNER_TEMP=str(runner_temp), GITHUB_STEP_SUMMARY=str(runner_temp / "summary.md"))
    return subprocess.run(["bash", "-e", "-c", script], cwd=cwd, env=env, capture_output=True, text=True, timeout=60)


def test_workflow_unconfigured_run_concludes_red_with_uploadable_receipt(tmp_path):
    steps = _steps()
    refresh = _bash(steps["Refresh raw structured source snapshots"]["run"], tmp_path, ENGINE)
    assert refresh.returncode == ssr.EXIT_BLOCKED, "unconfigured scheduled run must not conclude green"
    ensure = _bash(steps["Ensure typed source refresh receipt exists"]["run"], tmp_path, tmp_path)
    assert ensure.returncode == 0
    summary = _bash(steps["Summarize source refresh receipt"]["run"], tmp_path, tmp_path)
    assert summary.returncode == 0
    receipt = json.loads((tmp_path / "scout-source-refresh/receipt.json").read_text())
    assert receipt["status"] == "BLOCKED_SOURCE_CONFIGURATION"
    assert "BLOCKED_SOURCE_CONFIGURATION" in (tmp_path / "summary.md").read_text()


def test_workflow_fallback_writes_blocked_receipt_when_cli_emitted_nothing(tmp_path):
    ensure = _bash(_steps()["Ensure typed source refresh receipt exists"]["run"], tmp_path, tmp_path)
    assert ensure.returncode == 3
    receipt = json.loads((tmp_path / "scout-source-refresh/receipt.json").read_text())
    assert (receipt["status"], receipt["health"]) == ("BLOCKED_REFRESH_EXCEPTION", "BLOCKED")
    assert receipt["can_execute"] is False and receipt["prediction_authority"] is False


def test_workflow_fallback_never_overwrites_a_real_receipt(tmp_path):
    path = tmp_path / "scout-source-refresh/receipt.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"status":"COMPLETE"}\n')
    ensure = _bash(_steps()["Ensure typed source refresh receipt exists"]["run"], tmp_path, tmp_path)
    assert ensure.returncode == 0
    assert json.loads(path.read_text()) == {"status": "COMPLETE"}
