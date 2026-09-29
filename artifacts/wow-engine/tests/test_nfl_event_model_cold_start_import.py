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


def test_runtime_modules_remain_non_executable() -> None:
    completed = _run_probe(
        "import nfl_event_model_v17 as nfl; "
        "import ncaaf_candidate_training_runner as ncaaf; "
        "assert nfl.CAN_EXECUTE is False; "
        "assert ncaaf.CAN_EXECUTE is False"
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
