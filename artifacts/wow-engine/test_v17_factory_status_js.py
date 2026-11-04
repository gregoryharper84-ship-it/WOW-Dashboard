"""Exercise the optional factory-status JavaScript contract in existing WOW pytest CI.

This is a test-only bridge: no new workflow, controller, remote calls, credentials,
protected review bypass or change to model/trading authority.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
NODE_TEST = REPOSITORY_ROOT / "scripts/tests/wow_v17_factory_status.test.cjs"


def test_read_only_factory_status_javascript_contract() -> None:
    """Run the actual Node TAP suite rather than treating unrelated CI as proof."""
    node = shutil.which("node")
    assert node is not None, "CI must provide Node to verify the factory probe"
    assert NODE_TEST.is_file(), "factory probe Node regression suite must be present"
    result = subprocess.run(
        [node, "--test", str(NODE_TEST)],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = result.stdout + "\n" + result.stderr
    assert result.returncode == 0, output[-8000:]
    # Check the TAP summary so an empty/filtered run cannot count as verification.
    # Node's legacy TAP reporter prints "# pass N"; Node >= 22 defaults to the
    # spec reporter and prints "\u2139 pass N". Both carry the same authoritative
    # counts, so accept either marker while keeping the exact count assertions.
    pass_match = re.search(r"(?m)^(?:#|\u2139)\s*pass\s+(\d+)\s*$", output)
    fail_match = re.search(r"(?m)^(?:#|\u2139)\s*fail\s+(\d+)\s*$", output)
    assert pass_match is not None and int(pass_match.group(1)) >= 7, output[-8000:]
    assert fail_match is not None and int(fail_match.group(1)) == 0, output[-8000:]
