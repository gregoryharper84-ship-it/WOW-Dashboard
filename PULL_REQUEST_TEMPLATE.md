Title: test(v17): accept queued verifier semantics for wow-engine-verify (allow literal false or PR expression)

Summary:

This PR relaxes the artifact contract test for the wow-engine-verify workflow so that it accepts the intentional change in PR #1736 that sets `cancel-in-progress: false` to queue same-ref PR verifier runs instead of superseding them. The other verifier workflow (wow-verify) still requires the original `cancel-in-progress: ${{ github.event_name == 'pull_request' }}` expression.

Rationale:

- PR #1736 changed the workflow semantics intentionally to prevent superseding verification runs.
- The existing contract test asserted that the PR-expression must be present; that assertion is now too strict for wow-engine-verify and caused the CI failure.
- This change is narrowly scoped to a single test file under artifacts/wow-engine and does not alter any workflow files.

Files changed:
- artifacts/wow-engine/test_v17_render_checkspass_ci_contract.py

Signed-off-by: GitHub Copilot <copilot@example.com>
