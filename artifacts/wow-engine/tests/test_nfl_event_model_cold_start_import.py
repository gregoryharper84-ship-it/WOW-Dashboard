from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ENGINE_ROOT = Path(__file__).parents[1]


def _run_probe(probe: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ENGINE_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_importing_nfl_runtime_model_does_not_eagerly_load_sklearn() -> None:
    completed = _run_probe(
        "import sys; "
        "import nfl_event_model_v17; "
        "assert 'sklearn' not in sys.modules, "
        "'NFL production import eagerly loaded sklearn training dependencies'"
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_importing_ncaaf_maintenance_runner_does_not_eagerly_load_sklearn() -> None:
    completed = _run_probe(
        "import sys; "
        "import ncaaf_candidate_training_runner; "
        "assert 'sklearn' not in sys.modules, "
        "'NCAAF maintenance import eagerly loaded sklearn training dependencies'"
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_production_route_construction_does_not_eagerly_load_sklearn() -> None:
    modules = [
        "v17.first_six_open_data_maintenance",
        "v17.candidate_certification_evidence",
        "v17.team_state_challenger_training",
        "v17.spread_margin_replay_route",
    ]
    probe = (
        "import sys; "
        f"mods={modules!r}; "
        "[__import__(name) for name in mods]; "
        "assert 'sklearn' not in sys.modules, "
        "'production route construction eagerly loaded sklearn training dependencies'"
    )
    completed = _run_probe(probe)
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_runtime_modules_remain_non_executable() -> None:
    completed = _run_probe(
        "import nfl_event_model_v17 as nfl; "
        "import ncaaf_candidate_training_runner as ncaaf; "
        "import v17.first_six_open_data_maintenance as first_six; "
        "import v17.candidate_certification_evidence as evidence; "
        "import v17.team_state_challenger_training as team_state; "
        "import v17.spread_margin_replay_route as spread; "
        "assert nfl.CAN_EXECUTE is False; "
        "assert ncaaf.CAN_EXECUTE is False; "
        "assert first_six.CAN_EXECUTE is False; "
        "assert evidence.CAN_EXECUTE is False; "
        "assert team_state.CAN_EXECUTE is False; "
        "assert spread.CAN_EXECUTE is False"
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
