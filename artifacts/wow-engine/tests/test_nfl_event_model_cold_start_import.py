from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ENGINE_ROOT = Path(__file__).parents[1]


def test_importing_nfl_runtime_model_does_not_eagerly_load_sklearn() -> None:
    probe = (
        "import sys; "
        "import nfl_event_model_v17; "
        "assert 'sklearn' not in sys.modules, "
        "'production import eagerly loaded sklearn training dependencies'"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ENGINE_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_nfl_runtime_model_remains_non_executable() -> None:
    probe = (
        "import nfl_event_model_v17 as model; "
        "assert model.CAN_EXECUTE is False"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ENGINE_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
