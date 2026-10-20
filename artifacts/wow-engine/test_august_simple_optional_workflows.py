from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
OPTIONAL_WORKFLOWS = (
    ".github/workflows/wow-v17-engineering-digest.yml",
    ".github/workflows/wow-v17-engineering-effectiveness-scorecard.yml",
    ".github/workflows/wow-v17-engineering-pr-keep-current.yml",
    ".github/workflows/wow-v17-engineering-probe-cleanup.yml",
)


def test_optional_engineering_workflows_are_manual_only() -> None:
    for relative_path in OPTIONAL_WORKFLOWS:
        workflow = yaml.load(
            (ROOT / relative_path).read_text(encoding="utf-8"),
            Loader=yaml.BaseLoader,
        )

        assert set(workflow["on"]) == {"workflow_dispatch"}, relative_path
