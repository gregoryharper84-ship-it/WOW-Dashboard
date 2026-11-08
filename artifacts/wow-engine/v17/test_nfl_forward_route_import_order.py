"""Fresh-process regression for NFL forward-evidence route import ordering.

The production route must remain available regardless of whether its control
plane or the Fantasy Score bridge is imported first. This is an import-only
regression: no probability certification, market data, or wagers are involved.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "imports",
    [
        "from v17 import nfl_prop_evidence_control_plane as nfl\n"
        "from v17 import fantasy_score_pick_request_bridge\n"
        "assert callable(nfl.install_nfl_prop_forward_evidence_route)\n",
        "from v17 import fantasy_score_pick_request_bridge\n"
        "from v17 import nfl_prop_evidence_control_plane as nfl\n"
        "assert callable(nfl.install_nfl_prop_forward_evidence_route)\n",
    ],
)
def test_nfl_forward_route_import_order_does_not_cycle(imports: str) -> None:
    # Import into a fresh interpreter so other tests cannot prime sys.modules.
    engine_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-c", imports],
        cwd=engine_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr
