from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "wow-v17-rapid-repair.yml"
CODECOV = ROOT / "codecov.yml"


def test_codecov_is_informational_and_patch_aware() -> None:
    config = yaml.safe_load(CODECOV.read_text(encoding="utf-8"))

    assert config["coverage"]["status"]["project"]["default"]["informational"] is True
    assert config["coverage"]["status"]["patch"]["default"]["informational"] is True
    assert config["comment"]["hide_project_coverage"] is True


def test_rapid_gate_uses_oidc_without_a_codecov_secret() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "id-token: write" in text
    assert "codecov/codecov-action@v5" in text
    assert "use_oidc: true" in text
    assert "fail_ci_if_error: false" in text
    assert "CODECOV_TOKEN" not in text
    assert "pytest-cov" in text
    assert "--cov=." in text
    assert "can_execute=false" in text
    assert "V17_TERMINAL_REDUCER" in text
