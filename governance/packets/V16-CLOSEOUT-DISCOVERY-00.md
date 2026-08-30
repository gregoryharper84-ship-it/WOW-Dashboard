═══════════════════════════════════════════════════
V16-CLOSEOUT-DISCOVERY-00 — Read-Only Repository & Runtime Evidence Audit
═══════════════════════════════════════════════════

STATUS:            Read-only reconnaissance. NO code changes, NO commits,
                   NO branch creation, NO migrations, NO deployments.

PURPOSE:           Produce repository-grounded evidence for the certification
                   step to build the authoritative V16 Closeout Matrix. This
                   evidence packet — not narrative summary, not prior
                   session memory — is what gets certified against. Claude
                   Code is the evidence collector here, not the architecture
                   authority.

GOVERNANCE:        Preserve WOW_VERSION=WOW_v16_CLEAN_CORE, can_execute=false,
                   DRY_RUN_ONLY=true. This packet touches nothing that
                   could alter those. If any discovery step would require
                   a write, a migration, or a deploy to complete, STOP that
                   step, report why, and mark it BLOCKED_NEEDS_WRITE_ACCESS
                   rather than proceeding.

§15 NOTICE:        If reconnaissance surfaces anything matching a CLAUDE.md
                   §15 stop condition (governance change, model-probability
                   behavior change, unclear authority, superseded/stale
                   phase reference), do NOT resolve it. Emit a line starting
                   exactly with:
                       STOP_15: <one-line description>
                   for each occurrence, in addition to the normal section
                   content. The workflow parses for this literal marker.

─────────────────────────────────────────────────
SECTION A — REPOSITORY STATE
─────────────────────────────────────────────────
- Current main branch name, latest commit SHA, latest commit date/author
- Open PRs: number, title, target branch, mergeable status, CI status,
  last-updated date, one-line description of scope
- Stale/abandoned branches (no activity 30+ days) with last commit SHA
- Branch protection rules currently configured on main (or absence thereof)
- Required CI checks currently enforced (or absence thereof)

─────────────────────────────────────────────────
SECTION B — TERMINAL-CEILING / LABEL RESOLUTION
─────────────────────────────────────────────────
Grep and report every distinct implementation (file + line range) of:
- terminal_label / terminal_ceiling / lowest_ceiling / final_ceiling /
  strict_ceiling resolution logic
- label_rank / label_priority / label_order tables (numeric or otherwise)
- Any place that merges multiple stage blockers into one final label
- Any place that converts labels to numeric rank for comparison
List which production publication path (see Section C) consumes which
resolver. Note any two paths that use different resolvers.

─────────────────────────────────────────────────
SECTION C — ACTIVE PUBLICATION / SCORING PATHS
─────────────────────────────────────────────────
For each of: /score-prop, /score-event, /score-pick-request, NCAAF
acceptance/scoring, Full Model finalizer, slip/card finalizer, and any
other reachable production scoring or publication route —
report: file, entrypoint function, which resolver (Section B) it calls,
whether it's currently deployed/reachable, and whether tests exist that
exercise it end-to-end (not just a helper function in isolation).

─────────────────────────────────────────────────
SECTION D — DUPLICATE / LEGACY RUNTIME PATHS
─────────────────────────────────────────────────
Report any: old Flask scoring code, duplicate FastAPI route installers,
alternate/legacy calibration or model registry code, dead feature
transforms, superseded migrations, shadow-only code, old Custom GPT
contracts, or any ingress path that bypasses the canonical scoring
entrypoints. For each: REACHABLE_IN_PRODUCTION or LEGACY_NOT_RUNTIME,
with the evidence for that classification (import graph, route
registration, deploy config — not assumption).

─────────────────────────────────────────────────
SECTION E — FITTED-MODEL / CALIBRATION REGISTRY COVERAGE
─────────────────────────────────────────────────
For each of: MLB pitcher strikeouts, MLB full-game event/moneyline,
MLB 1IP, WNBA POINTS/REBOUNDS/ASSISTS/3PM, NCAAF moneyline/event —
report current state against these SEPARATE fields (do not collapse):
MODEL_EXISTS, MODEL_REGISTERED, MODEL_CERTIFIED, MODEL_RUNTIME_AVAILABLE,
PROBABILITY_PUBLISHABLE, MONEY_QUALIFIED_ALLOWED, FINAL_APPROVED_ALLOWED.
Cite the file/artifact/registry entry for each claim.

─────────────────────────────────────────────────
SECTION F — PREDICTION → OUTCOME → GRADING PERSISTENCE
─────────────────────────────────────────────────
For each relevant table (wow_predictions, wow_outcomes,
wow_event_predictions, wow_event_outcomes, wow_recommendation_records,
wow_recommendation_outcomes, wow_recommendation_positions, and any
sport-specific equivalents): does a write path exist, does a grading/
settlement path exist, is settlement automatic or manual, can an orphan
prediction (written but never graded) currently occur — with evidence.

─────────────────────────────────────────────────
SECTION G — DEPLOYMENT TOPOLOGY VS REPOSITORY MANIFEST
─────────────────────────────────────────────────
Compare root render.yaml (and any other deployment manifests in-repo)
against actual live Render services. Report every live service NOT
declared in a manifest (starting with wow-odds-proxy — confirm its
live config, whether it's dashboard-managed outside the Blueprint, and
whether that's documented anywhere in-repo as an intentional exception)
and every manifest-declared service that is NOT currently live.

─────────────────────────────────────────────────
SECTION H — SUPABASE SECURITY POSTURE VERIFICATION
─────────────────────────────────────────────────
For wow_prop_evidence_snapshots specifically: confirm RLS enabled,
enumerate current policies, confirm grants are limited to postgres/
service_role with no unintended anon/authenticated access. Then repeat
the same check for every other table tagged as immutable/evidence/
prediction-record in the schema. Report per-table, not just the one
already flagged.

─────────────────────────────────────────────────
SECTION I — CLAUDE.md VERSION-BOUNDARY / STALE-STATE CONFLICTS
─────────────────────────────────────────────────
Report every place in CLAUDE.md (or governing docs) where a stated
current state conflicts with a later section, a stated phase is
marked superseded in one place but referenced as current elsewhere,
or an A.2/migration-model reference appears stale relative to repo
evidence. Quote the conflicting passages with section references —
do not resolve them, just surface them.

─────────────────────────────────────────────────
OUTPUT FORMAT
─────────────────────────────────────────────────
One evidence packet, Section A through I, in order. Every claim must
cite a file path, line range, table name, config key, or command
output — no narrative summaries without an evidence anchor. Where
evidence is genuinely unavailable (e.g., requires prod DB access
Claude Code doesn't have), mark that item EVIDENCE_UNAVAILABLE and
say what access would be needed, rather than inferring an answer.

DO NOT:
- Modify any file
- Create any branch or commit
- Propose fixes or architecture (that is the certification/architecture
  step's job next)
- Classify closeout status (PASS/FIX/DEFER/etc.)

END OF PACKET. Return evidence only. Do not proceed to CLOSEOUT-01.
═══════════════════════════════════════════════════
