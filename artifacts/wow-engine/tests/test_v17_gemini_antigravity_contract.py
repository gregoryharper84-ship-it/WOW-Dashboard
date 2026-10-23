"""Static regression for optional Gemini/Antigravity workspace integration.

No API network access, account credential, production service or Gemini invocation.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
AGENT_NAMES = ("wow-amrt-recovery", "wow-model-challenger", "wow-scout-evidence")


class GeminiAntigravityContractTests(unittest.TestCase):
    def test_workspace_contract_is_discoverable_and_preserves_governance(self):
        contents = (ROOT / "GEMINI.md").read_text(encoding="utf-8")
        for invariant in (
            "V17_TERMINAL_REDUCER",
            "can_execute=false",
            "WOW_PROP_LANE",
            "LLP_TEAM_BETTING_ENGINE",
            "No consumer-subscription credential reuse",
            "never place, route, modify, approve, or cancel a wager",
            "single",
        ):
            self.assertIn(invariant.lower(), contents.lower(), invariant)

    def test_agent_files_are_distinct_and_have_valid_required_frontmatter(self):
        for name in AGENT_NAMES:
            with self.subTest(agent=name):
                file_path = ROOT / ".agents" / "agents" / name / "agent.md"
                source = file_path.read_text(encoding="utf-8")
                self.assertTrue(source.startswith("---\n"))
                frontmatter = source.split("---\n", 2)[1]
                self.assertIn("name: " + name, frontmatter)
                self.assertIn("description: ", frontmatter)
                self.assertTrue(source.split("---\n", 2)[2].strip())

    def test_no_agent_has_certification_or_wager_authority(self):
        for name in AGENT_NAMES:
            source = (ROOT / ".agents" / "agents" / name / "agent.md").read_text(encoding="utf-8")
            with self.subTest(agent=name):
                self.assertIn("GEMINI.md", source)
                self.assertTrue(
                    any(term in source.lower() for term in ("not a controlling", "cannot act as independent qa", "never fitted-model probability")),
                    name,
                )

    def test_pilot_is_opt_in_and_has_no_api_activation(self):
        pilot = (ROOT / "docs" / "wow" / "engineering" / "WOW_GEMINI_ANTIGRAVITY_PILOT.md").read_text(encoding="utf-8")
        for term in ("local-user activation still required", "NOT enabled", "no competing PR"):
            self.assertIn(term.lower(), pilot.lower())


if __name__ == "__main__":
    unittest.main()
