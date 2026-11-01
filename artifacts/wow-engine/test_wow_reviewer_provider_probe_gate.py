"""Execute offline credential-preflight regressions in required governed-backend CI.

The operational CLI lives in scripts/, not a default pytest discovery target.
This wrapper ensures its six no-network negative cases run on the PR head.
It never loads credentials, requests provider tokens, or publishes checks.
"""
from pathlib import Path
import re
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
SUITE = REPO / "scripts" / "tests" / "test_wow_reviewer_provider_probe.py"
PROBE = REPO / "scripts" / "wow_reviewer_provider_probe.py"


def test_credential_probe_offline_safety_suite_runs():
    assert SUITE.is_file() and PROBE.is_file()
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover",
         "-s", str(SUITE.parent), "-p", SUITE.name, "-v"],
        cwd=REPO, capture_output=True, text=True, timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    match = re.search(r"Ran ([0-9]+) tests", result.stderr)
    assert match and int(match.group(1)) >= 6, result.stderr
    for case in (
        "test_whitespace_is_not_trimmed_into_a_false_healthy_result",
        "test_typed_provider_401_does_not_disclose_key",
        "test_plain_404_is_neutral_and_not_mislabeled_as_entitlement",
        "test_untrusted_exception_text_never_reaches_stdout",
    ):
        assert case in result.stderr, result.stderr
