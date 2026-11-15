"""V28 reviewer App installation preflight: no network or secrets."""
import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "wow_v28_reviewer_app_preflight.py"
spec = importlib.util.spec_from_file_location("wow_v28_reviewer_app_preflight", SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class TestReviewerAppPreflight(unittest.TestCase):
    def setUp(self):
        self.app = {"app_id": 5765620, "repository_selection": "selected",
                    "account": {"login": "gregoryharper84-ship-it"},
                    "permissions": {"contents": "read", "pull_requests": "write", "metadata": "read"},
                    "events": []}
        self.repos = [{"full_name": "gregoryharper84-ship-it/WOW-Dashboard"}]

    def test_single_repo_minimal_scopes(self):
        m.validate_installation([self.app], self.repos)

    def test_wrong_app_id_holds(self):
        with self.assertRaisesRegex(m.ReviewerConfigHold, "SCOPE"):
            m.validate_installation([{**self.app, "app_id": 1}], self.repos)

    def test_extra_repo_holds(self):
        with self.assertRaisesRegex(m.ReviewerConfigHold, "EXCLUSIVE"):
            m.validate_installation([self.app], self.repos + [{"full_name": "second/repo"}])

    def test_all_repository_install_holds(self):
        with self.assertRaisesRegex(m.ReviewerConfigHold, "SCOPE"):
            m.validate_installation([{**self.app, "repository_selection": "all"}], self.repos)

    def test_permission_escalation_holds(self):
        changed = {**self.app, "permissions": {**self.app["permissions"], "checks": "write"}}
        with self.assertRaisesRegex(m.ReviewerConfigHold, "SCOPE"):
            m.validate_installation([changed], self.repos)

    def test_webhook_event_subscription_holds(self):
        with self.assertRaisesRegex(m.ReviewerConfigHold, "SCOPE"):
            m.validate_installation([{**self.app, "events": ["pull_request"]}], self.repos)

    def test_different_repo_holds(self):
        with self.assertRaisesRegex(m.ReviewerConfigHold, "SCOPE"):
            m.validate_installation([self.app], [{"full_name": "another/repo"}])


if __name__ == "__main__":
    unittest.main()
