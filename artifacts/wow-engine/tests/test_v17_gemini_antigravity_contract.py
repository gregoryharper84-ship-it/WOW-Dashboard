"""Static regression for optional Gemini/Antigravity workspace integration.

No API network access, account credential, production service or Gemini invocation.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
AGENT_NAMES = ("wow-amrt-recovery", "wow-model-challenger", "wow-scout-evidence")


class GeminiAntigravityContractTests(unittest.TestCase):
    def test_workspace_contract_is_discoverable_and_preserves_governance(self):
        contents = (ROOT / ".agents" / "GEMINI.md").read_text(encoding="utf-8")
        for invariant in (
            "V17_TERMINAL_REDUCER",
            "can_execute=false",
            "WOW_PROP_LANE",
            "LLP_TEAM_BETTING_ENGINE",
            "No consumer-subscription credential reuse",
            "never place, route, modify, approve, or cancel a wager",
            "Work on one assigned issue and one existing authorized branch at a time.",
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
                self.assertIn(".agents/GEMINI.md", source)
                self.assertTrue(
                    any(term in source.lower() for term in ("not a controlling", "cannot act as independent qa", "never fitted-model probability")),
                    name,
                )

    def test_pilot_is_opt_in_and_has_no_api_activation(self):
        pilot = (ROOT / "docs" / "wow" / "engineering" / "WOW_GEMINI_ANTIGRAVITY_PILOT.md").read_text(encoding="utf-8")
        for term in ("local-user activation still required", "NOT enabled", "no competing PR"):
            self.assertIn(term.lower(), pilot.lower())


    def test_existing_engineering_app_accepts_rule_scope_and_rejects_root_bypass(self):
        import importlib.util
        adoption_script = ROOT / "artifacts" / "wow-engine" / "v17" / "engineering_authorized_adoption.py"
        spec = importlib.util.spec_from_file_location("antigravity_source_admission", adoption_script)
        self.assertIsNotNone(spec)
        adoption = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adoption)
        source_paths = (
            ".agents/GEMINI.md",
            ".agents/agents/wow-amrt-recovery/agent.md",
            ".agents/agents/wow-model-challenger/agent.md",
            ".agents/agents/wow-scout-evidence/agent.md",
            ".agents/skills/wow-flint-coding-handoff/SKILL.md",
            "artifacts/wow-engine/tests/test_v17_antigravity_flint_skill.py",
            "artifacts/wow-engine/tests/test_v17_gemini_antigravity_contract.py",
            "artifacts/wow-engine/tests/test_v17_gemini_sidecar_installer.py",
            "docs/wow/engineering/WOW_GEMINI_ANTIGRAVITY_PILOT.md",
            "docs/wow/engineering/WOW_GEMINI_AUTONOMY_SETUP.md",
            "scripts/wow_gemini_sidecar.py",
        )
        files = [{"filename": name, "status": "added"} for name in source_paths]
        self.assertEqual(adoption.source_paths(files), sorted(source_paths))
        with self.assertRaisesRegex(adoption.AdoptionHold, "SOURCE_CHANGE_SCOPE_OR_RENAME_HOLD"):
            adoption.source_paths([{"filename": "GEMINI.md", "status": "added"}])



if __name__ == "__main__":
    unittest.main()
