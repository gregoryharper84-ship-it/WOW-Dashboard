from __future__ import annotations

import os
import subprocess
import sys


def test_v17_active_production_import_completes_without_import_time_bridge_mutation():
    env = os.environ.copy()
    env["WOW_V17_ACTIVE"] = "1"
    env["WOW_CALIBRATION_PUBLICATION_LANE_SEPARATION"] = "1"
    env["WOW_CAN_EXECUTE"] = "false"
    env["WOW_DRY_RUN_ONLY"] = "true"

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import api_ncaaf_acceptance as api; "
                "assert api.V17_ACTIVE is True; "
                "assert api.app.state.v17_mlb_event_bridge_deferred is True; "
                "print('V17_PRODUCTION_IMPORT_OK')"
            ),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "V17_PRODUCTION_IMPORT_OK" in completed.stdout


def test_v17_production_bridge_registration_is_idempotent_after_api_import():
    env = os.environ.copy()
    env["WOW_V17_ACTIVE"] = "1"
    env["WOW_CALIBRATION_PUBLICATION_LANE_SEPARATION"] = "1"
    env["WOW_CAN_EXECUTE"] = "false"
    env["WOW_DRY_RUN_ONLY"] = "true"

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import api_ncaaf_acceptance as api; "
                "from v17 import _defer_mlb_event_bridge_install; "
                "from v17 import team_event_request_runtime as runtime; "
                "before = sum(f.__name__ == 'install_mlb_event_bridge_after_imports' "
                "for f in api.app.router.on_startup); "
                "assert before == 1, before; "
                "assert _defer_mlb_event_bridge_install("
                "market_api=api.base.market_api, team_runtime=runtime) is True; "
                "after = sum(f.__name__ == 'install_mlb_event_bridge_after_imports' "
                "for f in api.app.router.on_startup); "
                "assert after == 1, after; "
                "assert api.app.state.v17_mlb_event_bridge_deferred is True"
            ),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
