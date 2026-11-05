# WOW Native Senior Engineer — Vendor-Independent Review Foundation

**Objective:** WOW-ENG-NATIVE-REVIEW-20261105  
**Owner:** Existing WOW Engineering / AMRT, coordinated by WOW_ECOSYSTEM_CONDUCTOR  
**Reviewer:** Independent Engineering Review Agent + System Architect Agent  
**Challenge:** SIRT; independent QA and Release own their existing protected receipts  
**Status:** SOURCE IMPLEMENTED / NOT MERGED OR LIVE-ACCEPTED; runner activates only after authorized protected merge. No autonomous code writer  
**Safety:** can_execute=false. V17_TERMINAL_REDUCER retains global sporting terminal authority.

## Decision

WOW owns the code, prompts, packet format, memory and training/evaluation ledger for the senior engineering reviewer. **No CodeRabbit API, account, configuration, subscription or license is required.** Do not require the external CodeRabbit GitHub App for this module. The context/static analyzers use only Python's standard library. A separate trusted-main GitHub API runner uses the repository's scoped GitHub token; the optional AI adapter talks only to a model already hosted at 127.0.0.1.

Implementation: v17/native_senior_review.py (context), v17/native_senior_review_static.py (deterministic risk patterns), v17/native_senior_review_local_ai.py (explicit opt-in loopback inference), .github/scripts/wow_native_senior_review.py (authenticated PR intake, advisory comments and JSON artifacts), and .github/workflows/wow-native-senior-review.yml (protected-main, source-data-only workflow). The runner **is not live until merged and independently verified**; the default workflow never invokes an AI model, edits source, repairs a PR or confers PASS.

## Openly documented techniques, independently reimplemented

1. **Intent:** Load incident ID, linked scope and acceptance criteria from existing canonical GitHub issue/PR.
2. **Diff:** Bind each changed file, full patch and source content to the authenticated exact SHA. No quiet exclusion or semantic verdict on a truncated diff.
3. **Dependency context:** AST-parse Python sources *as data* and find import-based upstream/downstream references. This graph is incomplete for dynamic imports, reflection, non-Python call paths and runtime wiring, and is not a proof of coverage.
4. **Risk lenses:** Ask architecture, API, model-integrity, data and governance questions according to touched paths. These are questions, **not detected bugs**.
5. **Static evidence:** The native AST/unified-diff scanner checks newly added Python source for dynamic execution, shell invocation, unsafe deserialization, TLS disabling, missing HTTP timeouts and V17-specific safety/failure-path hazards; it also checks risky privileged-workflow checkout patterns. These are source-grounded **unconfirmed hypotheses**, not a semantic correctness verdict. Existing CodeQL and future sandboxed linters may complement this; do not execute PR-supplied scripts, installs or plugins in a privileged job.
6. **AI analysis:** The optional local-only Ollama adapter is implemented and requires an already-running trusted localhost model plus an explicit --local-model selection; there is **no external or billable fallback**. It is not enabled in the GitHub-hosted workflow and has no verified resident deployment. Returned claims must match exact changed source and remain unconfirmed until independently reproduced.
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
    WOW Native Senior Review context packet (trusted PR/API intake)
            |
            +--> native static checks (implemented, advisory)
            |
            +--> optional localhost AI (adapter built; not activated)
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

**Phase 1 — source/PR workflow** (this PR): bounded exact-head GitHub intake, changed-source and sibling context, AST static findings, optional localhost model adapter, idempotent advisory PR comments, JSON artifact, tests and documentation. Protected workflow runs only after independent merge. Exact-head source+negative-path CI and independent acceptance are still required; CI PASS is not live reviewer proof.

**Phase 2 — live reviewer activation:** after a protected merge, prove a real ready-for-review same-repo PR is inspected from trusted main, receives one updated bot-owned advisory comment and an attributed JSON artifact at the exact head; demonstrate stale-head rejection and two independent source-fixture controls (known bug, known good). Local AI runs only after an authorized self-hosted model exists. Failure to have an AI runtime must be reported as NOT_RUN/LOCAL_REVIEW_HELD, not a semantic PASS.

**Phase 3 — Engineering feedback loop:** the current JSON exports a nonmutating AMRT handoff for the existing parent incident. AMRT worker ingestion, autonomous reproduction, authenticated issue receipt writes, confirmed false-positive learning, and independent production acceptance **remain unimplemented**. Engineering must reconcile each finding against canonical issues before describing the end-to-end repair loop as complete.

**Success measures:** coverage with full source, confirmed P0/P1 recall on a versioned labeled WOW repair set, reviewer precision, reviewer false-positive count, correct positives preserved, time/cost per independently accepted fix. Report UNKNOWN when samples/receipts are absent.

**Rollback:** disable the native advisory workflow or revert its protected-main implementation, retaining prior immutable GitHub review artifacts. No production scoring, fitted-model, order or release-approval settings require changes.
