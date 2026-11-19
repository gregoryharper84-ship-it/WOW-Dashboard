"""Offline tests: a PR-head QA HOLD must not make protected-main CI red.

Do not weaken the signed QA App PR-head check, publication failure handling,
reviewer identity/attestation, or model certification. In particular this tests
workflow orchestration only; no HTTP request, secrets or inference.
"""
from pathlib import Path
import unittest


WORKFLOW = Path(__file__).resolve().parents[2] / "workflows" / "wow-v17-independent-qa-check.yml"


def step(text: str, name: str, following: str | None) -> str:
    marker = "      - name: " + name + "\n"
    start = text.index(marker)
    end = text.index("      - name: " + following + "\n", start + len(marker)) if following else len(text)
    return text[start:end]


class QaMainCheckDecouplingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_external_reviewer_failure_is_handled_without_forcing_main_job_red(self):
        review = step(self.source, "Run isolated read-only Gemini semantic review",
                      "Recheck PR head and diff after review")
        self.assertIn("continue-on-error: true", review)
        self.assertIn("python .github/scripts/wow_v28_gemini_qa_bridge.py review", review)

    def test_source_review_pipeline_runs_only_for_successful_real_model_review(self):
        stages = [
            ("Recheck PR head and diff after review",
             "Build versioned exact-head review artifact"),
            ("Build versioned exact-head review artifact",
             "Upload short-lived exact-head review receipt"),
            ("Upload short-lived exact-head review receipt",
             "Mint dedicated semantic reviewer App token"),
        ]
        for stage, next_stage in stages:
            with self.subTest(stage=stage):
                self.assertIn(
                    "steps.review.outcome == 'success'",
                    step(self.source, stage, next_stage),
                )
        decide = step(self.source, "Decide at exact head (read-only)",
                      "Preserve terminal QA decision for every resolved target")
        self.assertIn("steps.review.outcome == 'success'", decide)
        self.assertIn("steps.policy.outputs.review_active != 'true'", decide)

    def test_failure_uses_real_step_outcome_for_signed_pr_hold(self):
        terminal = step(self.source, "Preserve terminal QA decision for every resolved target",
                        "Upload terminal QA outcome")
        self.assertIn("if: always()", terminal)
        self.assertIn("REVIEW_OUTCOME: ${{ steps.review.outcome }}", terminal)
        self.assertIn("QA_REVIEW_EXECUTION_FAILED:$failed_step", terminal)
        self.assertIn("can_execute", self.source)
        self.assertIn("merge_or_release_authorized", self.source)

    def test_pr_head_check_publication_remains_fail_closed_and_not_ignored(self):
        publisher = step(self.source, "Publish exact-head check", None)
        self.assertIn("if: always()", publisher)
        self.assertIn("GH_TOKEN: ${{ steps.app_token.outputs.token }}", publisher)
        self.assertIn("gh api --method POST", publisher)
        self.assertIn("repos/$GITHUB_REPOSITORY/check-runs", publisher)
        self.assertIn("QA_CHECK_PUBLISH_FAILED", publisher)
        self.assertIn("exit 1", publisher)
        self.assertNotIn("continue-on-error: true", publisher)
        self.assertIn("head_sha: .head_sha", publisher)
        self.assertIn("conclusion: .conclusion", publisher)


if __name__ == "__main__":
    unittest.main()
