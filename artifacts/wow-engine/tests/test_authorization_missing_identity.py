"""Missing role credentials must fail rather than produce a neutral workflow."""
from pathlib import Path
import os
import subprocess
import yaml

ROOT = Path(__file__).resolve().parents[3]

def test_missing_role_credentials_fail_before_any_check_is_published():
    for role, filename in (("QA", "independent-qa"), ("RELEASE", "release-authority")):
        path = ROOT / ".github/workflows" / f"wow-v17-{filename}-check.yml"
        workflow = yaml.safe_load(path.read_text())
        job = next(iter(workflow["jobs"].values()))
        assert job["if"] == "github.repository == 'gregoryharper84-ship-it/WOW-Dashboard'"
        assert job["environment"] == ("wow-qa" if role == "QA" else "wow-release")
        assert all(value == "read" for value in workflow["permissions"].values())
        steps = job["steps"]
        probe = next(step for step in steps if step["name"] == "Require this role's App identity")
        assert steps.index(probe) < next(i for i, step in enumerate(steps) if step["name"] == "Mint checks-only App token")
        assert probe["run"].count("exit 1") == 1
        for app_id, key_present, expected in (("", "false", 1), ("123", "false", 1), ("invalid", "true", 1), ("123", "true", 0)):
            env = dict(os.environ, APP_ID=app_id, KEY_PRESENT=key_present)
            result = subprocess.run(["bash", "-c", probe["run"]], env=env, capture_output=True, text=True, timeout=5)
            assert result.returncode == expected
            if expected:
                assert f"{role}_APP_CREDENTIAL_MISSING" in result.stdout

if __name__ == "__main__":
    test_missing_role_credentials_fail_before_any_check_is_published()
    print("PASS: 8 executed credential cases across isolated QA and Release workflows")
