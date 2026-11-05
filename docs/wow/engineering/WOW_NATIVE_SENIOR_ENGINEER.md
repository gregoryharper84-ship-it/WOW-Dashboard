# WOW Native Senior Engineer — Vendor-Independent Review Foundation

**Objective:** WOW-ENG-NATIVE-REVIEW-20261105  
**Owner:** Existing WOW Engineering / AMRT, coordinated by WOW_ECOSYSTEM_CONDUCTOR  
**Reviewer:** Independent Engineering Review Agent + System Architect Agent  
**Challenge:** SIRT; independent QA and Release own their existing protected receipts  
**Status:** SOURCE-ONLY FOUNDATION; not a live PR reviewer or autonomous coding agent  
**Safety:** can_execute=false. V17_TERMINAL_REDUCER retains global sporting terminal authority.

## Decision

WOW owns the code, prompts, packet format, memory and training/evaluation ledger for the senior engineering reviewer. **No CodeRabbit API, account, configuration, subscription or license is required.** Do not require the external CodeRabbit GitHub App for this module. The module uses only Python's standard library and has no network/client dependency.

The source implementation is v17/native_senior_review.py, with adversarial tests in test_v17_native_senior_review.py. It currently builds/validates context; it **does not invoke a model, scan a live PR, post comments, run linters, fix files or confer PASS**.

## Openly documented techniques, independently reimplemented

1. **Intent:** Load incident ID, linked scope and acceptance criteria from existing canonical GitHub issue/PR.
2. **Diff:** Bind each changed file, full patch and source content to the authenticated exact SHA. No quiet exclusion or semantic verdict on a truncated diff.
3. **Dependency context:** AST-parse Python sources *as data* and find import-based upstream/downstream references. This graph is incomplete for dynamic imports, reflection, non-Python call paths and runtime wiring, and is not a proof of coverage.
4. **Risk lenses:** Ask architecture, API, model-integrity, data and governance questions according to touched paths. These are questions, **not detected bugs**.
5. **Static evidence:** Future integration may run existing trusted CodeQL or optional free locally pinned Semgrep CE, Ruff, Bandit and workflow linters; collect tool identity, version, rule ID and offending lines. Do not execute PR-supplied scripts, installs or plugins in a privileged job.
6. **AI analysis:** Existing approved coding/reasoning provider, or an explicitly approved zero-cost/self-hosted provider, may receive the bounded packet in an isolated worker. Provider is replaceable. Model recommendations need source-grounded, attributable findings and a separate verification pass. No fallback to billable model without opt-in.
7. **Verification:** Recheck each finding on exact head; require a concrete failure scenario, minimal reproduction, affected caller, negative test, nearest preserved-positive case and evidence status. Text matching only establishes that a cited excerpt exists; it **does not prove the defect**.
8. **Delivery/learning:** Return typed findings to the *existing* incident/AMRT single-writer lease, not a new queue. Preserve independently verified true/false positives and reviewed counterexamples for future targeted improvement. Never train on unverifiable verdicts as facts.

## Review execution contract

Existing GitHub/Engineering infrastructure should supply:

- Authenticated repository, PR number, base_sha, head_sha, source-PR/incident identity and exact changed paths.
- A complete bounded patch set and full changed-file contents; known relevant caller/dependency source.
- Approved severity taxonomy, negative acceptance criteria, nearest previously good behavior and prior verified defect references.
- Reviewer version, prompt/checklist version, source hash, model/provider version if used, token/cost receipt only if actually reported.
- Explicit final classification: advisory finding, tested-confirmed, tested-rejected, incomplete context, stale source or independent verification needed.

build_review_packet(...) returns a hash-pinned packet and **never asserts GitHub authenticity**. Callers must authenticate refs in the trusted intake. Missing changed content, oversized material or invalid Python yields CONTEXT_INCOMPLETE, no partial-source model invocation. preflight_review_finding(...) rejects stale SHA, invented file/excerpt and packet tampering, while deliberately stopping at EVIDENCE_LOCATED_NOT_SEMANTICALLY_VERIFIED.

## Integration into the existing engineering organization

    Authenticated GitHub PR/incident
            |
            v
    Existing Conductor + AMRT intake (dedupe, canonical issue, exact head)
            |
            v
    WOW Native Senior Review context packet (this source-only PR)
            |
            +--> existing static analyzers / bounded sandbox (next integration)
            |
            +--> optional approved reasoning agent (next integration)
            |
            v
    Finding verification: source evidence + reproducible negative test
            |
            v
    AMRT investigator -> single-writer Engineering implementation
            |
            v
    SIRT challenge -> independent QA -> protected Release -> production evidence

This augments, rather than supersedes, .agents/skills/wow-engineering-independent-review-agent/SKILL.md and the already existing independent semantic security reviewer. Their auth, review decisions and protected checks are unchanged. *Never* present AI review as independent QA, CODEOWNER approval, CI success, release authorization or live model validation.

## Cost and security constraints

- No CodeRabbit dependency, paid feature, new GitHub App, new schedule, production DB, secret or external service.
- Zero model inference cost for the current deterministic module. Future LLM costs are unknown until provider/model selection, real usage and a spend cap are established. No silent purchases or overages.
- No execution of untrusted code during extraction; parse with Python AST only. Isolate later tool-assisted scans, deny secrets and unsafe network privileges, cap time/memory/files and treat PR text as untrusted instructions.
- One protected, authenticated source owner and immutable SHA per review; stale heads invalidate previous analysis.
- No auto-merge, no automatic reviewer approval, no modification to calibrated probabilities, fitted model weights or terminal reducer.

## Acceptance, deployment and rollback

**Phase 1 — source-only context** (this PR): source+negative-path unit tests at exact head, no runtime auth change, documentation of known limitations. Independent source acceptance required. A CI PASS is not production activation.

**Phase 2 — useful reviewer:** integrate authenticated read-only GitHub input and exact file/diff retrieval, sandboxed tool scans and bounded optional model review through the existing worker. Post advisory comments using scoped identity only after independent acceptance. Validate on at least one known buggy PR and one known-good PR, with repeated cross-file regression counterexamples.

**Phase 3 — Engineering feedback loop:** reconcile all findings against canonical issues; demonstrate no repeated/duplicate work, correct severity, repeatability, false-positive adjudication, single-writer handoff and independent source/production acceptance.

**Success measures:** coverage with full source, confirmed P0/P1 recall on a versioned labeled WOW repair set, reviewer precision, reviewer false-positive count, correct positives preserved, time/cost per independently accepted fix. Report UNKNOWN when samples/receipts are absent.

**Rollback:** deactivate future reviewer invocation and retain the immutable test/evidence record. This source-only module can be removed without touching release workflows or production scoring.
