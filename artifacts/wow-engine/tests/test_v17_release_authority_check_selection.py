"""Adversarial release-policy regression for exact-head check selection."""
from pathlib import Path
import subprocess
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "release-authority.yml"
REQUIRED = (
    (15368, "WOW additional required regression"),
    (15368, "WOW governed probability backend"),
    (15368, "WOW required-three regression"),
    (5245832, "WOW Independent QA exact-head"),
    (5245853, "WOW Release Authority exact-head"),
)
SHA = "a" * 40


def expression():
    content = WORKFLOW.read_text(encoding="utf-8")
    marker = 'jq -es --arg sha "$EXPECTED_SHA" '
    return content.split(marker, 1)[1].split("' <<< \"$checks\"", 1)[0].strip().strip("'").strip()


def record(app, name, id, status="completed", conclusion="success", sha=SHA):
    return dict(app={"id": app}, name=name, id=id, status=status,
                conclusion=conclusion, head_sha=sha)


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq required to exercise actual policy")
def test_superseded_pass_does_not_authorize():
    import json
    checks = [record(app, name, id=i + 1) for i, (app, name) in enumerate(REQUIRED)]
    def run(rows):
        result = subprocess.run(
            ["jq", "-es", "--arg", "sha", SHA, expression()],
            input=json.dumps({"check_runs": rows}), text=True, capture_output=True,
            timeout=5, check=False,
        )
        output = result.stdout.strip()
        assert output in {"true", "false"}, result.stderr
        assert result.returncode == (0 if output == "true" else 1), result.stderr
        return output
    assert run(checks) == "true"
    assert run(checks + [record(*REQUIRED[0], id=99, conclusion="failure")]) == "false"
    assert run(checks + [record(*REQUIRED[0], id=99, status="in_progress", conclusion=None)]) == "false"
    assert run(checks + [record(*REQUIRED[0], id=99, sha="b" * 40)]) == "true"
    assert run(checks[:-1]) == "false"
    assert run([record(999999, name, i + 1) for i, (_, name) in enumerate(REQUIRED)]) == "false"


def test_verifier_stays_read_only_and_signer_hold_remains():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "contents: read" in text and "checks: read" in text
    assert "ISOLATED_SIGNER_AND_DEPLOYMENT_IDENTITY_NOT_PROVISIONED" in text
    assert "exit 1" in text
    assert "final_main=" in text
    assert "grep -qx true" in text
    assert "sort_by(.id) | last" in text
