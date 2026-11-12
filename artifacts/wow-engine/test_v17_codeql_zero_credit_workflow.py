"""Static guardrails for WOW's existing, no-AI-API CodeQL PR scan.

This tests the workflow's admission and credential posture only. It does not
create an approval, certify the absence of vulnerabilities, or execute a model.
"""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
CODEQL = ROOT / ".github/workflows/wow-codeql.yml"


def check_codeql_workflow(text: str) -> None:
    """Reject PR scanning changes that silently increase release authority."""
    required = (
        "\non:\n",
        "\n  pull_request:\n    branches: [main]\n",
        "\n  push:\n    branches: [main]\n",
        "\n  schedule:\n",
        "\n  workflow_dispatch:\n",
        "\n  contents: read\n",
        "\n  security-events: write\n",
        "        language:\n          - python\n          - javascript-typescript\n",
        "          build-mode: none\n",
        "          persist-credentials: false\n",
        "          languages: ${{ matrix.language }}\n",
    )
    for fragment in required:
        if fragment not in text:
            raise ValueError("CODEQL_STATIC_CONTRACT_MISSING")
    forbidden = (
        "pull_request_target:",
        "secrets.",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "id-token: write",
        "contents: write",
        "pull-requests: write",
        "issues: write",
    )
    for fragment in forbidden:
        if fragment in text:
            raise ValueError("CODEQL_PRIVILEGED_PR_OR_PAID_PROVIDER_FORBIDDEN")
    checkout = text.split("      - name: Checkout\n", 1)
    if len(checkout) != 2:
        raise ValueError("CODEQL_CHECKOUT_MISSING")
    section = checkout[1].split("\n      - name:", 1)[0]
    if ("uses: actions/checkout@v6" not in section
            or "persist-credentials: false" not in section
            or "persist-credentials: true" in section):
        raise ValueError("CODEQL_CHECKOUT_CREDENTIALS_UNVERIFIED")


class CodeqlNoApiCreditTests(unittest.TestCase):
    def test_existing_workflow_is_nonprivileged_and_has_no_ai_api_dependency(self):
        check_codeql_workflow(CODEQL.read_text(encoding="utf-8"))

    def test_fail_closed_if_pr_trigger_token_policy_or_external_secret_is_changed(self):
        original = CODEQL.read_text(encoding="utf-8")
        mutations = (
            original.replace("  pull_request:\n", "  pull_request_target:\n", 1),
            original.replace("persist-credentials: false", "persist-credentials: true", 1),
            original.replace("    branches: [main]", "    branches: [dev]", 1),
            original.replace("security-events: write", "contents: write", 1),
            original + "\n      # secrets.OPENAI_API_KEY must never be used\n",
            original.replace("          build-mode: none", "          build-mode: manual", 1),
        )
        for candidate in mutations:
            with self.subTest(mutated=candidate != original):
                self.assertNotEqual(candidate, original)
                with self.assertRaises(ValueError):
                    check_codeql_workflow(candidate)


if __name__ == "__main__":
    unittest.main()
