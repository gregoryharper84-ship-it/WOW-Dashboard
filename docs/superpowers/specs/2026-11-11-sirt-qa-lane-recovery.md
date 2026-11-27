# SIRT intake and QA evidence-recovery design — 2026-11-11

**Goal:** Prevent supported existing WOW research PRs from silently missing independent SIRT intake, while retaining exact-head QA and Release protections. No new scheduler, reviewer authority, paid provider, terminal, or merge controller.

**Observed root causes:** The existing SIRT intake accepts only two worker-origin branch forms; a pre-existing PR such as #2547 does not enter that workflow. An early QA workflow run may defer when a required native regression check has not yet been published, returning success with no independent QA check. Later triggers did recover for #2547; there is not evidence of a permanent QA failure. The five pinned exact-head checks are authoritative for protected merge, and zero-credit QA does not constitute semantic SIRT review.

**Approach:**
1. Extend the **existing** `wow-v17-engineering-sirt-intake.yml` with a manually dispatched exact PR/SHA intake path and opt-in labeled PR-target path. Branch events remain from trusted main; never checkout or execute PR source.
2. Only same-repository, open, nondraft PRs with one canonical `Incident: #N` may enter. Labeled trigger requires `sirt-review-required` label granted by repository permissions. Manual route requires explicit PR/SHA; never accepts a superseded SHA.
3. Reuse the same idempotent `WOW_SIRT_INTAKE_PR_<PR>_HEAD_<SHA>` marker and existing issue/PR cross-link writer. The new path is intake only, never review PASS, QA PASS, merge or deploy.
4. Serialize all SIRT intake event sources in one repository-scoped `queue: max` writer group; event IDs differ by source and cannot safely deduplicate concurrent issue creation. The old worker-origin incident syntax (including backtick-wrapped numbers) remains accepted, while direct/manual entries require exactly one canonical incident line.\n5. Do not introduce a QA scheduler or duplicate check publisher. Existing QA recovered after the native checks appeared. Add explicit readiness runbook and QA defer diagnostics; use existing manual exact-head QA `workflow_dispatch` if an event is genuinely lost. A later bounded code change needs a separately reproduced persistent race.
6. Keep `can_execute=false`, the existing GitHub ruleset, and independent SIRT verdict. No credentials, provider spend or changes to fitted sporting probabilities.

**Evidence of completion:** Negative-path regression verifies stale SHA, draft, forked and non-incident PRs are not admitted; labels cannot create a PASS; legacy worker path unchanged. Hosted workflow lint and native regression green; protected changes merged only with independent SIRT/QA/Release. Runtime SIRT intake issue and external non-author verdict are distinct proof points.
