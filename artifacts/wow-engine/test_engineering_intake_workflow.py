"""Prevent regression to unvalidated multi-page controller intake."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class WorkflowIntakeTests(unittest.TestCase):
    def test_every_intake_stage_uses_verified_pager(self):
        wf = (ROOT / '.github/workflows/wow-engineering-24x7.yml').read_text()
        self.assertEqual(wf.count('v17/engineering_intake_api.py'), 5)
        self.assertNotIn('gh api --paginate "/repos/$GITHUB_REPOSITORY/issues?', wf)
        self.assertNotIn('gh api --paginate "/repos/$GITHUB_REPOSITORY/pulls?', wf)
        self.assertNotIn('"$RUNNER_TEMP/fresh-runs.json" | jq', wf)
        self.assertIn('--worker "$worker" --runs "$RUNNER_TEMP/observed-runs.json"', wf)
        self.assertIn('--issues "$RUNNER_TEMP/issues.json"', wf)
        self.assertIn('--prs "$RUNNER_TEMP/prs.json"', wf)

    def test_provider_inventory_history_is_still_strictly_bounded(self):
        helper = (ROOT / 'artifacts/wow-engine/v17/engineering_intake_api.py').read_text()
        self.assertIn("LIMITS = {'issues': 5000, 'prs': 2000, 'runs': 999}", helper)
        self.assertIn('WORKER_RUN_HISTORY_TRUNCATED', helper)
        self.assertIn('ENGINEERING_INTAKE_DUPLICATE_ID', helper)
        self.assertIn('ENGINEERING_INTAKE_TOTAL_MISMATCH', helper)
        self.assertIn('ENGINEERING_INTAKE_REPOSITORY_UNTRUSTED', helper)


if __name__ == '__main__':
    unittest.main()
