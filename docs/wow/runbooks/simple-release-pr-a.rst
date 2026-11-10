WOW Simple Release — PR A candidate (#1908)
=========================================

Disposition: BLOCKED_WITH_EXACT_REASON. Owner: Production Engineering.
Escalation: Security/Release and independent SIRT QA.
This document is not cutover, merge, credential, or deployment authorization.
PR_CREATED and passing fixture tests are not production closure.

Build packet and boundary
-------------------------

* Change: WOW-PATCH-2026-10-20-SIMPLE-RELEASE-A; interactive; Class B / R3.
* Baseline: c2d17be32228e81dcbe1f2ef5926ca57a54ad03e; working branch:
  copilot/class-b-replace-qa-release-approvals. Current protected main, not this
  branch, remains source authority. No schema/API/model changes.
* Problem/root cause (confirmed in source): the current journal's
  ``role_admission`` and sole Render writer require both exact-head App checks.
  Removing the Release context alone would create a ruleset/controller mismatch.
  Native environment self-review prevention also prevents the sole human from
  both dispatching and approving a release.
* Allowed files: ``artifacts/wow-engine/v17/simple_release_policy_candidate.py``,
  ``artifacts/wow-engine/tests/test_v17_simple_release_policy_candidate.py``,
  and this runbook. All existing workflows, QA verifier, journal, credentials,
  rulesets, render.yaml, model and persistence code are unchanged.
* Publication authority: none. SYSTEM_ARCHITECT_AGENT_REQUIRED=true.
  Independent architecture, security, QA, Release and exact-head CODEOWNER owner
  review remain required. No implementer may approve adoption.
* Rollback point for this isolated candidate: the baseline above. Revert these
  three files through a protected PR; no runtime rollback is implied.

The helper is an authenticated GET-only observer, not a new release controller.
It is deliberately not imported by any workflow or writer. It uses the existing
``gh_fetch`` transport; injected transports are for tests, not caller-supplied
JSON artifacts, workflow inputs, flags or branch variables.
``observed_policy`` describes readback, not adoption. ``policy_evidence_valid``
describes only the bounded observation. Even then, ``post_allowed``,
``deploy_authorized`` and ``merge_authorized`` remain false.

Unconditional invariants::

    can_execute=false
    DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
    runtime_generation=V17_ACTIVE
    terminal_authority=V17_TERMINAL_REDUCER
    custom_gpt_identity=WOW_BETTING_ENGINE

No fitted model, probability, calibration, terminal ceiling, wager, order or
capital-allocation behavior changes. Missing evidence never clears a blocker.

Verified source versus unverified service state
----------------------------------------------

The source retains ``RENDER_CREDENTIAL_SCOPE_ATTESTED=False``. #1875 remains a
hard blocker to Render writes, regardless of checks or policy observations.
This session could not perform authenticated environment/ruleset CLI reads:
``gh api`` had no exposed token. Existing GitHub MCP access did not provide an
environment/ruleset administration read endpoint. No live reviewer, environment
rule, credential isolation, App capability or deployment was certified.

Owner CLI observations — October 20, review 6298495522
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The owner subsequently supplied these authenticated CLI observations (reported
evidence, not a fresh capture independently performed by this coding session):

* ``gh api repos/gregoryharper84-ship-it/WOW-Dashboard/environments/wow-release --jq '{name, protection_rules, deployment_branch_policy}'``
  returned ``wow-release`` with only a ``branch_policy`` protection rule and
  ``custom_branch_policies=true``. No ``required_reviewers`` rule was configured.
* ``gh secret list -R "$REPO" --env wow-release`` showed
  ``WOW_RELEASE_APP_PRIVATE_KEY``; ``gh secret list -R "$REPO"`` showed
  repository-scoped ``RENDER_API_KEY``. Only names/scopes were supplied; no
  credential values were supplied or retrieved.

Route A is unavailable: native environment human review is not configured.
CODEOWNERS or repository owner/admin permissions do not configure an environment
reviewer and cannot substitute for an independent run approval. Route B is also
unavailable for authorization until independently verified: the reported output
does not evidence an enabled existing-Release-App custom protection rule.
The custom deployment-protection-rule endpoint was not reported; do not turn
that missing evidence into a claim that its list is empty. Presence of the
App private-key secret does not prove a configured rule or independent decision.
Both routes remain unavailable, not implicitly selected alternatives.

The negative fixture transcribes the reported branch-only fields and removes
unreported bypass, custom-rule and approval evidence. Synthetic environment IDs
and branch-policy details are test scaffolding, not additional live observations.
Separate conditional cases isolate missing versus empty custom-rule readback;
neither conditional case attests live settings. Existing legacy five-check
recognition still does not establish a release-authorization route.

#1875 remains open and owns credential-isolation/writer evidence. The reported
repository secret is not proof of protected-environment or provider-side scope.
This follow-up proposes no environment-rule or secret migration: do not move,
copy, delete or rename secrets, change workflow secret references, or alter any
writer. Any separately authorized proposal must enumerate exact workflow secret
references, the full actual writer/credential-holder inventory (including external
and provider-side writers), authenticated scope evidence and an explicit rollback
before mutation. No blind repository-secret movement, self-review or bypass.
Production mode and ``RENDER_CREDENTIAL_SCOPE_ATTESTED=False`` remain unchanged.

Unsupported authoritative evidence
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The documented environment GET schema does not expose ``can_admins_bypass``.
Absence is UNKNOWN, never false. The candidate returns
``ENVIRONMENT_BYPASS_UNPROVEN`` in that case, including for otherwise valid
reviewer configuration. Positive tests supply a conditional response extension;
they are not evidence that GitHub actually supplies it. Before adoption,
Security must independently establish an authoritative authenticated method
for proving bypass is disabled and review the corresponding verifier change.
Do not inject an attestation Boolean to make this helper pass.
``ENVIRONMENT_ADMIN_BYPASS_AUTHORITATIVE_EVIDENCE_UNSUPPORTED`` is a permanent
adoption blocker in this candidate, even for positive conditional fixtures.
There is no claimed feasible production verifier for this property yet.

The run approvals endpoint returns an array of
``{environments, state, user, comment}``. It has no approval timestamp, SHA or
attempt ID. Human authorization is therefore limited to attempt 1 and exactly
one unambiguous approval for the exact environment ID. Reruns require a fresh
dispatch, not reuse of old approval. Teams without individually verified
membership do not establish an eligible independent human.
``ENVIRONMENT_APPROVAL_TIMESTAMP_AUTHORITATIVE_EVIDENCE_UNSUPPORTED`` likewise
blocks adoption: attempt-1 run-specific history is bounded observation, not
proof of approval time. No invented timestamp or workflow-input attestation
may clear either unsupported-evidence blocker.

Two mutually exclusive candidate routes
---------------------------------------

The authenticated active ruleset selects the observed PR policy:

* Legacy: ruleset 24907422, ``wow-v17-main-agent-separation``, active on main,
  the three Actions checks (App 15368), QA (5245832) and Release (5245853).
  Legacy role admission continues to require both Apps. Environment evidence
  cannot remove that requirement. Required PR merge, deletion/non-fast-forward
  protection, exact source-pinned five contexts and no bypass actors are checked.
  Recognizing legacy does not assert candidate protections already exist.
* Candidate: the same ruleset with ``WOW Release Authority exact-head`` removed.
  The three CI checks and authentic ``WOW Independent QA exact-head`` remain
  source-pinned. Missing stronger protections populate the complete
  ``migration_requirements`` list and return HOLD before environment evaluation.
  Any other context, source or effective-rule mismatch also holds.

Owner-authenticated review 6297970596 reports the current legacy selector as
``["~DEFAULT_BRANCH"]``; CODEOWNER review, last-push approval and strict required
status checking are false. The deterministic fixture transcribes those reported
fields, not a complete independently captured live ruleset. The helper accepts
that selector only with authenticated repository ``default_branch=main`` and
stable repository readback; no literal-ref rewrite is required.

Removing only Release context from that reported legacy state returns all:

* ``RULESET_OWNER_PROTECTION_MISSING:require_code_owner_review``
* ``RULESET_OWNER_PROTECTION_MISSING:require_last_push_approval``
* ``RULESET_CHECKS_NOT_STRICT``

Missing stale-review dismissal adds
``RULESET_OWNER_PROTECTION_MISSING:dismiss_stale_reviews_on_push`` too. These
stronger settings require separate owner authorization and exact authenticated
readback **before** any context-only cutover. This PR changes none of them.

For the candidate release observation, real enabled environment protections,
not an override, select exactly one route:

A. Native required reviewers, ``prevent_self_review=true``, authenticated
   run-specific approval from an eligible human other than both original
   dispatch actor, triggering actor and PR author, with current repository access.
   Eligibility and self-review checks use immutable positive GitHub user IDs;
   login aliases are not identity. Candidate trust-root changes additionally
   require the immutable repository owner's latest exact-head approval and
   cannot be owner-authored. The existing QA verifier remains unchanged.
   If the owner is the only eligible person and also dispatches, this holds.
   Do not enable self-review or introduce a second credential as a shortcut.
B. No native required-reviewer route, and exactly one enabled custom protection
   rule belonging to the existing Release Authority App 5245853. Require its
   independently issued deployment-specific check:
   ``WOW Release Authority deployment``, exact main SHA, completed success,
   ``external_id=wow-release:<run_id>:<run_attempt>:<main_sha>``, exact workflow
   run URL and completion no earlier than that run's start.

Route B is a proposed evidence contract, not a deployed App capability.
An installed/available App is not an enabled protection rule. A PR-wide Release
check is not release-specific authorization. A check is not GitHub's native
custom-rule callback approval: both must be verified before a real release.
Security must prove the existing App independently enforces this contract,
validates authentic requests and cannot be impersonated by the dispatching
workflow minting its own approval. Reuse existing isolated authority only;
no new App, credential, scheduled bot or live controller is created here.
If this cannot be proved with existing capability, hold for Security/Release.
Neither route is currently verified or authorized.

Phase 1 — review and prepare, with legacy enforcement intact
----------------------------------------------------------

1. Review this candidate independently. Require the original three CI + QA +
   Release exact-head checks and CODEOWNER owner review on the final PR head.
   The coding assignment is not approval to adopt the policy. Owner-authored
   trust-root PRs and stale owner approvals remain rejected by the unchanged
   QA verifier. This candidate must receive Class B owner review even though
   it is unwired.
2. Obtain independently authenticated readbacks using an already authorized
   operator identity. Do not print tokens or secret values. Save sanitized
   responses, HTTP status, timestamps and request IDs in the governed evidence
   store, bound to the exact source/run/attempt. From an authenticated shell::

       gh api --include user
       gh api --include repos/gregoryharper84-ship-it/WOW-Dashboard
       gh api --paginate 'repos/gregoryharper84-ship-it/WOW-Dashboard/rulesets?includes_parents=true&per_page=100'
       gh api --include repos/gregoryharper84-ship-it/WOW-Dashboard/rulesets/24907422
       gh api --paginate repos/gregoryharper84-ship-it/WOW-Dashboard/rules/branches/main
       gh api --include repos/gregoryharper84-ship-it/WOW-Dashboard/environments/wow-release
       gh api --paginate repos/gregoryharper84-ship-it/WOW-Dashboard/environments/wow-release/deployment-branch-policies
       gh api --include repos/gregoryharper84-ship-it/WOW-Dashboard/environments/wow-release/deployment_protection_rules
       gh api --include repos/gregoryharper84-ship-it/WOW-Dashboard/git/ref/heads/main

   Also read the current run and approvals from
   ``repos/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/<run_id>`` and
   ``.../actions/runs/<run_id>/approvals``; inspect the exact attempt, eligible
   reviewer identity/access or authentic Release-App decision as applicable.
   Reject unreadable, incomplete or conflicting evidence. Do not use saved
   evidence as future authorization. Confirm ``allow_auto_merge=false``.
3. Verify ``wow-release`` binds the sole protected job, restricts branches to
   exactly main, disables administrator bypass and supports one real independent
   authorization route. Record the owner-dispatch/self-review deadlock if route
   A cannot work; prefer guarded route B only when its capability is proven.
   Separately accept credential isolation/provider scope under #1875.
4. Reconcile #1887 (already merged handoff deduplication) and #1907 (optional
   engineering trigger retirement); do not edit their files or recreate either
   effort. Coordinate #1900 and #1890 before any controller adoption.
5. Merge and verify PR A only through normal protection. Source merge still
   does not activate this helper. A separately reviewed compatible integration
   in the existing sole writer is required for Phase 2; never add another writer.

Phase 2 — separately authorized controlled cutover
-------------------------------------------------

Only after PR A is independently reviewed/merged/verified, reviewer/App
separation and credential isolation are proven, and Security accepts the real
API evidence verifier and compatible existing-controller integration:

1. Freeze release initiation; reconcile any active journal lease or ambiguous
   POST. Independently inventory native Render auto-deploy, hooks, manual
   principals, workflow POSTs and credential holders. No competing writer may
   remain. An API deploy-list snapshot is not a durable service fence.
2. Capture the complete authenticated legacy ruleset, effective rules and
   controller SHA as the rollback pair. The compatible controller must support
   legacy while legacy rules are active and hold on any mismatch.
   Obtain separate explicit owner authorization for each missing
   ``migration_requirements`` setting above. Enable and authenticate readback
   of those stronger protections while retaining all five contexts; record that
   separately authorized before/after diff. No implicit authorization is derived
   from this runbook. If approval or proof is unavailable, HOLD without cutover.
   Resolve the named unsupported environment evidence blockers through an
   independently accepted verifier change; synthetic fixtures cannot resolve them.
3. In one authorized maintenance window, change ruleset 24907422 **only** by
   removing the Release-App PR context from that separately verified stronger
   baseline. Retain all three App-15368 CI contexts,
   App-5245832 QA, strict exact-SHA checking, owner/CODEOWNER/stale-review
   protections, history protections and no bypass actors.
4. Immediately repeat the exact authenticated ruleset/effective-main readbacks
   above. Diff the full before/after documents; require only that intended
   context removal. Hold on drift or unreadability. No unchecked rollout flag.
5. Exercise one real agent-authored PR with exact-head CI + authentic QA +
   applicable exact-head owner approval and authorized manual merge. Verify
   genuine main-push CI, unique merged-PR provenance and current main SHA.
6. Initiate exactly one manual release in the existing ``wow-release`` protected
   job. Its existing writer must recheck main/config immediately before any
   write; independently authorize the exact run/attempt/SHA, prove #1875 scope,
   exclusivity, journal CAS lease and durable intent before a single POST.
   An uncertain response means reconcile, never retry a second POST.
7. Independently verify exact deployed SHA, health and accepted scoring,
   persistence/reconciliation, governance invariants and a durable production
   receipt. No deployment is permitted unless every production gate passes.
   Keep disposition BLOCKED_WITH_EXACT_REASON until independently verified.
8. Only after acceptance, separately retire obsolete Release-App PR-check cron
   and QA polling cron/workflow. Preserve the QA reviewer and all required CI.
   This PR retires nothing.

Rollback and acceptance evidence
--------------------------------

On mismatch, unknown authorization, main drift, competing writer, lease/CAS
failure, ambiguous POST or runtime verification failure: stop new release
initiation, preserve evidence and reconcile the existing journal/provider state.
Do not reset a lease or repeat a POST to obtain green evidence.

An authorized rollback restores the saved legacy ruleset + compatible legacy
controller **together**, then immediately authenticates readback of all five
source-pinned contexts and exact-SHA approval evidence. Keep releases held
throughout any mismatch. Use protected PRs, never direct main writes, force
pushes or an unpaired ruleset rollback. Do not claim an older deployment is
safe without independently verifying its receipt and runtime state.

Acceptance tests (run from ``artifacts/wow-engine``)::

    python -m pytest -q tests/test_v17_simple_release_policy_candidate.py \
      tests/test_v17_github_release_journal.py \
      tests/test_v17_production_release_admission.py \
      test_v17_independent_release_checks.py \
      tests/test_v17_render_release_provenance_gate.py \
      tests/test_v17_render_production_deploy_workflow.py

Coverage joins the new GET fixture integration/negative tests with unchanged
QA trust-root/owner-approval, journal CAS/lock races, ambiguous POST, credential
scope, duplicate-writer, protected-workflow and provenance regressions. These
tests are deterministic fixtures, not authenticated live acceptance.

Official schema references
--------------------------

* https://docs.github.com/en/rest/deployments/environments#get-an-environment
* https://docs.github.com/en/rest/actions/workflow-runs#get-the-review-history-for-a-workflow-run
* https://docs.github.com/en/rest/deployments/protection-rules#get-all-deployment-protection-rules-for-an-environment
* https://docs.github.com/en/rest/actions/workflow-runs#review-custom-deployment-protection-rules-for-a-workflow-run
* https://docs.github.com/en/rest/checks/runs#list-check-runs-for-a-git-reference
