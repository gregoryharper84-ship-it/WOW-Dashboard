# Automatic Exact-Head Semantic Review — Design

**Date:** 2026-11-15  
**Status:** Accepted for implementation by the user's explicit “Proceed with the recommendations above” instruction.

## Intent

Remove the manual semantic-review bottleneck for protected WOW changes without weakening trust boundaries, adding a reviewer App, introducing a paid API provider, or granting merge/deploy/trading authority. The existing QA App must publish one attributable review check for the exact PR head and base. The check becomes a required ruleset context after a one-time repository-admin update.

## Goals

- Automatically evaluate every same-repository PR targeting \`main\`.
- Classify the actual diff, not PR labels or author claims. Routine R-class changes receive a deterministic “review not required” PASS; P/C changes receive an independent Claude semantic review.
- Use the existing QA GitHub App (ID \`5245832\`) with \`checks:write\` only.
- Use the existing \`.github/actions/wow-claude-agent\` action with the existing Claude Code OAuth credential only. Do not call the Anthropic API-key path, Gemini, or a new vendor.
- Permit exactly one retry for typed transient provider failures only. Never retry a semantic REJECT, malformed output, stale identity, auth/quota failure, or policy denial.
- Bind the decision to repository, PR number, head SHA, base SHA, complete diff inventory and SHA-256 digest.
- Recheck live PR identity, base SHA, diff digest and prior same-SHA REJECT immediately before publishing.
- Publish \`WOW Independent Security Review\` from QA App 5245832. Preserve sticky REJECT at the same SHA.
- Keep the reviewer read-only, protected-main-only, and unable to merge, deploy, change probabilities, or execute wagers.

## Non-goals

- No GitHub App creation, App permission expansion, credential rotation, new paid API spend, or Gemini fallback.
- No auto-merge, production deploy, Supabase migration, GPT editor update, model certification, probability generation, live trading, or capital allocation in this change.
- No direct ruleset mutation from workflow code. The ruleset update is a separate repository-admin action and must be verified after application.
- No trust in PR code, workflow files from the PR head, labels, review text, or model-produced claims of identity.

## Constraints

- Preserve exactly: \`custom_gpt_identity=WOW_BETTING_ENGINE\`, \`runtime_generation=V17_ACTIVE\`, \`terminal_authority=V17_TERMINAL_REDUCER\`, \`can_execute=false\`, \`DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true\`.
- Keep workflow execution on protected \`main\`; checkout only \`main\`, never the PR head. Read the PR diff as untrusted data.
- Keep \`contents: read\`, \`pull-requests: read\`, \`checks: read\`, \`actions: read\`; mint the existing QA App token with \`checks: write\` only.
- Keep ruleset #24907422 strict required-status-check policy enabled and preserve all five existing required checks.
- Use existing typed failure codes in \`artifacts/wow-engine/docs/failure_codes.md\`; do not create a second failure queue or collapse provider outage into semantic REJECT.
- Unknown/incomplete diff, identity drift, missing output, malformed output, and uncertain classification fail closed.

## Approaches considered

1. **Automatically invoke the existing Claude/Gemini API script.** Rejected: it retains the manual-only trigger, the Gemini fallback, and API-key inference spend; it does not satisfy the current single-Claude/OAuth cost constraint.
2. **Create a separate reviewer App or human-only review lane.** Rejected: a new App adds one-time administration and another operational layer; a human-only lane preserves the bottleneck.
3. **Reuse the current QA App and existing Claude Code OAuth action, with exact-head prepare/review/publish phases.** Chosen: it reuses the installed App, existing protected action, and existing subscription credential; a deterministic diff classifier avoids model calls for routine changes; one exact-head QA App check becomes the policy gate.

## Design

1. Extend \`.github/scripts/wow_simple_security_review.py\` with \`--prepare\` and \`--publish\` modes. Prepare captures the exact same-repository PR, complete patch inventory, head/base SHA and diff digest. It classifies the diff from paths and changed content. P/C or ambiguous changes require semantic review; only positively identified routine R changes skip the model.
2. The workflow invokes \`.github/actions/wow-claude-agent\` with \`anthropic_api_key: ""\`, \`CLAUDE_CODE_OAUTH_TOKEN\`, \`:read-only\`, and the existing bounded JSON review schema. It retries once only when the action returns a typed transient diagnostic.
3. Publish validates the structured JSON again, maps failures to existing canonical reviewer failure codes, re-fetches the PR and diff, checks head/base/digest, checks sticky REJECT, writes the attributable decision artifact, and publishes the QA App check.
4. Change \`.github/workflows/wow-v17-simple-security-review.yml\` to run from protected main on \`pull_request_target\` opened/synchronize/reopened/ready_for_review events and retain exact-head \`workflow_dispatch\` for diagnostics. It must never checkout or execute PR code.
5. Add/adjust tests for classification, exact-head/base drift, incomplete diff, duplicate events, sticky REJECT, malformed/missing model output, typed one-retry behavior, OAuth-only provider use, and the workflow trust boundary.

## Activation and acceptance

- Add required status check \`WOW Independent Security Review\`, pinned to integration ID \`5245832\`, to ruleset #24907422. Keep \`strict_required_status_checks_policy=true\` and the existing five checks unchanged.
- Verify the effective ruleset by GET after the admin update.
- Exercise exact-head PASS, semantic REJECT, same-SHA sticky REJECT, stale head, stale base, malformed output and provider transient recovery.
- Do not merge the protected workflow change or call the reviewer gate fixed until independent sign-off, exact-head tests and ruleset readback are observed.
- After activation, trigger a fresh exact-head review for PR #2670. Only after its QA App review passes may the feature release proceed through its existing protected merge/deploy path.
- The bootstrap workflow change itself requires an independent non-author review; it cannot certify itself.

## Builder decisions

- Use the existing QA App and Claude Code OAuth action; do not add a new App or API provider.
- Routine R changes skip inference only after a deterministic diff classifier positively identifies them; unknown or ambiguous changes require semantic review.
- The check is a review gate only, never a merge, deployment, model, probability, or execution authority.
- The current environment cannot clone the public repository because outbound git/network access is unavailable. Work is isolated on a dedicated GitHub branch instead of a local git worktree; no shared branch or main is written.
