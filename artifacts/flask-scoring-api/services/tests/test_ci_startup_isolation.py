"""Exercise app startup without leaving database daemons in the test process."""
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("ci_mode", [True, False])
def test_app_startup_worker_policy(ci_mode):
    script = """
import os
from unittest.mock import patch

ci_mode = os.environ['WOW_CI'] == '1'
started = []
with patch('threading.Thread.start', lambda thread: started.append(thread.name)), \\
     patch('gate_engine.daily_run_lifecycle.ensure_manifest_ready'), \\
     patch('gate_engine.daily_run_lifecycle.start_manifest_reaper') as reaper, \\
     patch('gate_engine.llp_stage2_tables.ensure_all_tables') as schema:
    import app
    assert ('startup-warmup' in started) is (not ci_mode)
    assert reaper.called is (not ci_mode)
    assert schema.called is ci_mode
"""
    env = os.environ.copy()
    env.update({
        "WOW_CI": "1" if ci_mode else "0",
        "WNBA_DISABLE_CRON": "1",
        "LLP_DISABLE_SNAPSHOT_CRON": "1",
        "SETTLEMENT_WORKER_DISABLED": "1",
        "ANTHROPIC_API_KEY": "wow-ci-placeholder-not-live",
        "SESSION_SECRET": "wow-ci-attestation-secret-not-live",
    })
    # The test runner may be invoked from the repo root or another checkout
    # directory. The child must import this Flask app, not a globally installed
    # app module or an unrelated package from the caller's current directory.
    app_root = Path(__file__).resolve().parents[2]
    assert (app_root / "app.py").is_file()
    inherited_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(app_root) if not inherited_path
        else str(app_root) + os.pathsep + inherited_path
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=str(app_root), env=env,
        capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
