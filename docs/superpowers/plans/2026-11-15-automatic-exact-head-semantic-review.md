# Automatic Exact-Head Semantic Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Make protected P/C changes receive an automatic, independent, exact-head semantic review from the existing QA App, without new Apps, Gemini, or API-key inference spend.

**Architecture:** The existing protected-main security-review workflow becomes automatic on PR lifecycle events. A prepare phase captures and classifies the exact diff; P/C changes are reviewed through the existing Claude Code OAuth action; a publish phase revalidates the PR and emits the QA App check. Routine R changes receive an explicitly attributed deterministic pass without inference. The repository ruleset remains a separate admin-owned activation step.

**Tech Stack:** Python 3.11, GitHub Actions, GitHub Checks API, existing QA GitHub App 5245832, existing \`wow-claude-agent\` composite action, pytest/unittest.

**Spec:** \`docs/superpowers/specs/2026-11-15-automatic-exact-head-semantic-review-design.md\`

## Global Constraints

- Preserve exactly \`custom_gpt_identity=WOW_BETTING_ENGINE\`, \`runtime_generation=V17_ACTIVE\`, \`terminal_authority=V17_TERMINAL_REDUCER\`, \`can_execute=false\`, \`DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true\`.
- Never checkout or execute PR-head code in the privileged reviewer workflow.
- QA App 5245832 retains checks:write only; no pull-request write permission or new App.
- Use existing Claude Code OAuth only; no Gemini or Anthropic API-key call.
- One retry only for typed transient provider failure; never retry REJECT, malformed output, auth/quota, stale identity or policy denial.
- Preserve the five existing ruleset checks and strict mode; add the semantic check only through the separate owner-admin ruleset update.
- Never merge, deploy, certify a model, produce probabilities, or enable live execution from this review gate.

## Review Focus

- PR head changes during inference: must publish HOLD on the old SHA and never grant the new SHA a pass.
- Base SHA changes during inference: must publish HOLD; strict ruleset freshness remains enabled.
- A prior QA App REJECT exists for the same head: no retry or PASS may overwrite it.
- Claude output is missing, malformed, wrong-schema or semantically REJECT: publish a typed non-success.
- Diff inventory is incomplete, truncated, duplicated or ambiguous: fail closed; do not classify as routine.
- Only typed transient provider failures may trigger one retry; 401/403/404 and semantic findings do not.
- Routine classifier must inspect changed paths and diff content; PR labels and author text never control it.

---

### Task 1: Exact-head preparation, diff classification and publication

**Files:**
- Modify: \`.github/scripts/wow_simple_security_review.py\`
- Modify: \`.github/scripts/tests/test_wow_simple_security_review.py\`
- Modify: \`artifacts/wow-engine/test_wow_governance_v2.py\`

**Interfaces:**
- Add \`requires_semantic_review(target: dict) -> tuple[bool, str]\`.
- Add \`prepare_review() -> None\`, CLI \`--prepare\`, which writes \`wow-independent-security-target.json\` and outputs \`target_ready\`, \`review_required\`, \`prompt\`, and \`schema\`.
- Add \`publish_review() -> None\`, CLI \`--publish\`, which validates action output, re-captures exact PR diff, checks head/base/digest and sticky REJECT, writes the decision receipt and publishes the existing QA App check.
- Add reviewer identities \`claude-code-oauth\` and \`routine-diff-classifier\`; use existing failure codes only.

- [ ] **Step 1: Write failing tests**
  - Add tests for routine README-only diff → no semantic inference.
  - Add tests for workflow permissions, migration grants, gateway/auth and probability/calibration changes → semantic review required.
  - Add tests for unknown/ambiguous or incomplete diff → review required or HOLD, never routine.
  - Add tests for prepare/publish exact head, base SHA and diff digest binding.
  - Add tests for sticky same-SHA REJECT and malformed/missing structured output.
- [ ] **Step 2: Run RED**
  - Run: \`python -m pytest -q artifacts/wow-engine/test_wow_governance_v2.py\` and the focused unittest suite in \`.github/scripts/tests/test_wow_simple_security_review.py\`.
  - Expected: FAIL because the classifier and prepare/publish CLI do not yet exist and the workflow is still manual-only.
- [ ] **Step 3: Implement the smallest prepare/publish path**
  - Reuse existing \`capture\`, \`prompt_for\`, \`parse_verdict\`, \`prior_semantic_reject\`, \`require_live_identity\`, \`make_receipt\` and \`publish_check\`.
  - For P/C, require Claude structured output. For positively identified R-only diffs, publish a distinct routine-classification PASS. For any capture or identity error, publish HOLD when an authentic event PR/head SHA is available.
- [ ] **Step 4: Run GREEN**
  - Run the same focused commands.
  - Expected: all new classifier, identity, sticky-reject and output-validation tests pass; existing reviewer negative tests remain green.
- [ ] **Step 5: Commit**
  - \`feat: prepare and publish exact-head semantic review\`

### Task 2: Typed transient retry from the existing Claude OAuth action

**Files:**
- Modify: \`.github/actions/wow-claude-agent/claude_execution_outcome_diagnostic.py\`
- Modify: \`.github/actions/wow-claude-agent/action.yml\`
- Modify: \`artifacts/wow-engine/test_v17_claude_execution_outcome_diagnostic.py\`
- Modify: \`artifacts/wow-engine/test_v17_claude_structured_output_fallback.py\`

**Interfaces:**
- Diagnostic script writes safe \`reason\` and boolean \`retryable\` outputs to \`GITHUB_OUTPUT\`.
- Retryable only for 408, 429, 500, 502, 503, 504, 529 or a typed transport failure.
- Composite action exposes \`failure_reason\` and \`retryable\`; it never emits provider response text or secrets.

- [ ] **Step 1: Write failing tests** for transient status outputs, non-retryable 401/403/404, successful SDK result, missing/invalid execution file, secret redaction and composite output wiring.
- [ ] **Step 2: Run RED**
  - Run: \`python -m pytest -q artifacts/wow-engine/test_v17_claude_execution_outcome_diagnostic.py artifacts/wow-engine/test_v17_claude_structured_output_fallback.py\`
  - Expected: FAIL because the diagnostic currently emits log-only output and the composite action exposes no retryability output.
- [ ] **Step 3: Implement typed outputs**
  - Keep the existing OAuth-only call path; the workflow passes an empty API key.
  - Expose diagnostic outputs from the composite action. Do not add a retry inside the action.
- [ ] **Step 4: Run GREEN**
  - Run the same focused command.
  - Expected: transient statuses are retryable; auth, quota, malformed output and semantic outcomes are not.
- [ ] **Step 5: Commit**
  - \`feat: expose typed Claude transient retry diagnostics\`

### Task 3: Automatic protected-main workflow

**Files:**
- Modify: \`.github/workflows/wow-v17-simple-security-review.yml\`
- Modify: \`artifacts/wow-engine/test_wow_governance_v2.py\`

**Interfaces:**
- Trigger: \`pull_request_target\` for opened, synchronize, reopened and ready_for_review; retain exact-head \`workflow_dispatch\`.
- Checkout protected \`main\` only; never checkout PR head.
- Existing QA App token is minted with \`permission-checks: write\` only.
- Run \`--prepare\`, the existing Claude action with OAuth only, at most one typed transient retry, then \`--publish\` with \`if: always()\`.
- Check name: \`WOW Independent Security Review\`, QA App ID 5245832.

- [ ] **Step 1: Write failing workflow-contract tests** for the trigger, protected-main checkout, read-only permissions, QA App checks-only token, OAuth-only provider, typed retry condition, always-run publisher, no Gemini/API key and no PR-head checkout.
- [ ] **Step 2: Run RED**
  - Run: \`python -m pytest -q artifacts/wow-engine/test_wow_governance_v2.py\`
  - Expected: FAIL because the workflow currently supports only manual dispatch and the legacy provider script.
- [ ] **Step 3: Update the existing workflow**
  - Do not add another workflow or reviewer App.
  - Keep the manual dispatch path for exact-head diagnostics.
- [ ] **Step 4: Run GREEN**
  - Run: \`python -m pytest -q artifacts/wow-engine/test_wow_governance_v2.py\`
  - Expected: workflow contract tests pass.
- [ ] **Step 5: Commit**
  - \`feat: automate exact-head semantic review workflow\`

### Task 4: Branch CI, review and ruleset activation

**Files:**
- Review the complete branch diff against main.
- No production migration or deployment files are changed by this task.

- [ ] **Step 1: Open a draft PR for the isolated reviewer-gate branch** so the candidate-head regression workflows run.
- [ ] **Step 2: Verify RED/GREEN evidence from actual GitHub Actions runs** for each changed test file; do not infer local results because this runtime has no repository clone/network access.
- [ ] **Step 3: Inspect exact-head required checks, CodeRabbit findings, TestDriver findings, review threads and commit SHA.**
- [ ] **Step 4: Require independent non-author sign-off for this P-class workflow/action change. Do not self-approve or merge it.**
- [ ] **Step 5: Owner-admin activation** — update ruleset #24907422 to add \`WOW Independent Security Review\` pinned to integration ID 5245832; preserve all five existing checks and \`strict_required_status_checks_policy=true\`. Read back the effective ruleset.
- [ ] **Step 6: Trigger fresh exact-head PASS, REJECT, sticky-REJECT and stale-head canaries; confirm check provenance and no execution authority.**
- [ ] **Step 7: After gate activation, update PR #2670 to a fresh head and require the new check before proceeding with its protected release path.**

## Completion contract

The reviewer-gate code is not DONE until the PR is merged through independent protected review, the ruleset readback includes the QA-App-pinned check, exact-head PASS/REJECT/stale-head canaries are observed, tests pass at head, and the workflow has not changed any trading or probability authority. A passing reviewer check alone is not a production deployment.
