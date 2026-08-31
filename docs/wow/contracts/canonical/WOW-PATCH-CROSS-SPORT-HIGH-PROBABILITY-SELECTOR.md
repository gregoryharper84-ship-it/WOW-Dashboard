═══════════════════════════════════════════════════
WOW PATCH — CROSS-SPORT HIGH PROBABILITY SELECTOR (NEW SKILL)
═══════════════════════════════════════════════════

PATCH ID:          WOW-PATCH-2026-08-05-CROSS-SPORT-HIGH-PROBABILITY-SELECTOR

BASE SPEC:         WOW v16 Clean Core / Framework v2.2.0

PATCH TYPE:        [X] Skill update (adds a new SKILL.md)
                   [ ] Analytical rule
                   [ ] Dashboard code
                   [ ] Spec amendment
                   [ ] Memory update

ORIGIN:            [ ] Postmortem
                   [ ] Pattern identified after N occurrences
                   [X] Proactive model improvement — encodes the
                       structure of a previously successful four-leg
                       cross-sport card (2 tennis match winners, 1 MLB
                       moneyline, 1 WNBA moneyline) into a reusable
                       daily selector skill
                   [ ] External research / competitive analysis

─────────────────────────────────────────────────
PROBLEM STATEMENT
─────────────────────────────────────────────────
There is currently no single skill that scans across sports (tennis,
MLB, WNBA/NBA, NHL, NFL, soccer moneylines, supported props, Kalshi
sports contracts) in one pass and separates results into four distinct
output lanes: highest hit probability, highest calibrated true
probability, best verified edge, and best multi-leg structure with
cross-leg dependence control. wow-high-hit-engine covers part of this
(probability-first ranking, dry-run, no execution) but does not include
this package's weakest-leg elimination or explicit cross-leg dependence
audit (same-injury-thesis, same-player, component/composite overlap,
shared game script, shared weather exposure).

FAILURE TAG(S):    N/A — proactive addition, not a postmortem-driven fix

─────────────────────────────────────────────────
RULE CHANGE
─────────────────────────────────────────────────
AFFECTED SECTION:  New skill file — no existing WOW-MASTER-SPEC.md
                   section is modified. Skill is additive.

CURRENT RULE:
No existing rule. wow-high-hit-engine performs adjacent but not
identical cross-platform probability ranking; no existing skill performs
weakest-leg elimination or the specific cross-leg dependence audit
defined in this package.

NEW RULE:
Add `wow-cross-sport-high-probability-selector` to the active skill
stack, governed by the permanent invariants below, which may not be
altered by any future skill update without a new patch:

    auto_execute=false
    requires_human_confirmation=true
    stake_sizing=false
    bankroll_allocation=false
    NO_PLAY=valid

`can_execute` may be governed by live calibration gates, but the five
invariants above are permanent regardless of calibration maturity —
per existing project governance discussion, execution-layer safety does
not lift with calibration quality; only `can_execute` (candidate
qualification) is milestone-gated.

─────────────────────────────────────────────────
IMPLEMENTATION
─────────────────────────────────────────────────
ANALYTICAL IMPACT:
Adds a fifth research lane alongside PrizePicks/Props, LLP, Kalshi, and
wow-high-hit-engine. Does not change approval logic for any existing
lane — this skill only ranks and surfaces candidates; final approval for
any individual leg still routes through the sport-specific gate
(wow-gate-enforcer, wow-llp-runner, wow-kalshi-sports-gate, etc.) exactly
as it does today. No candidate becomes playable on this skill's output
alone.

DASHBOARD IMPACT:  [ ] Yes
                   [X] No — analytical/skill-layer only, no legacy platform code
                       change required for the skill itself to exist.
                       (NOTE: several fields referenced — e.g.
                       "immutable prediction ledger healthy",
                       "cross-ticket exposure ledger healthy" — assume
                       legacy platform-side ledgers that may not currently exist.
                       If they don't exist, this skill should report
                       those checks as NOT_AVAILABLE rather than silently
                       passing them. This needs confirmation before
                       activation — see CONFLICTS/DEPENDENCIES below.)

IF DASHBOARD: FUNCTION TO MODIFY: N/A at skill-approval stage.

CODE CHANGE: N/A at skill-approval stage.

─────────────────────────────────────────────────
TEST CASE
─────────────────────────────────────────────────
INPUT:
Daily slate with candidates across 3+ sports, one candidate with
calibrated_lower_bound below 0.65, one pair of candidates sharing the
same injury thesis (e.g., both legs depend on the same player's return
from injury on different teams' props).

EXPECTED OUTPUT:
Low-lower-bound candidate excluded from "Best Compact Card" via
weakest-leg elimination; same-injury-thesis pair flagged and at most one
retained per the cross-leg dependence audit; terminal labels for
excluded candidates match the fixed label set (REJECT_NO_EDGE,
REJECT_BAD_STRUCTURE, etc.) — never an invented label.

NEGATIVE TEST (should NOT trigger):
A card of 3 independent legs across 3 different sports, no shared
player/injury thesis, all with calibrated_lower_bound >= 0.65 and
positive lower_bound_edge → all three pass through to "Best Compact
Card" without elimination.

─────────────────────────────────────────────────
CONFLICTS / DEPENDENCIES
─────────────────────────────────────────────────
CONFLICTS WITH:    Possible functional overlap with wow-high-hit-engine
                   for the "highest probability" lane specifically.
                   Needs explicit resolution: does this skill run
                   alongside wow-high-hit-engine (two independent
                   opinions), or does it supersede the probability-
                   ranking portion of it? NOT YET RESOLVED — do not
                   deprecate wow-high-hit-engine as part of this patch.

DEPENDS ON:        References ledgers not confirmed to exist on legacy platform
                   ("immutable prediction ledger", "cross-ticket
                   exposure ledger"). Activation checklist below must
                   confirm these exist or the skill must be shipped
                   with those specific health checks defaulting to
                   NOT_AVAILABLE rather than assumed healthy.

SUPERSEDES:        None — additive only, pending resolution above.

─────────────────────────────────────────────────
DEPLOYMENT ORDER
─────────────────────────────────────────────────
[X] Step 1 — Claude confirms patch against active spec (no conflicts
    beyond the wow-high-hit-engine overlap flagged above) — DONE
[ ] Step 2 — Claude updates WOW-MASTER-SPEC.md section (N/A — skill-only,
    no spec section change needed)
[ ] Step 3 — ChatGPT reviews for conflicts, resolves relationship to
    wow-high-hit-engine, confirms ledger dependencies are real or stubs
    the health checks accordingly
[ ] Step 4 — PR review via wow-pr-checker skill (if any legacy platform-side
    ledger work is triggered by Step 3)
[ ] Step 5 — Deploy to legacy platform (only if Step 3 surfaces dashboard work)
[ ] Step 6 — Smoke test via wow-smoke-test skill (if applicable)
[ ] Step 7 — Log to WOW-SHARED-NOTES.md patch queue

STATUS:            [X] Proposed
                   [ ] Approved — analytical only
                   [ ] Approved — pending dashboard build
                   [ ] Deployed
                   [ ] Rejected — reason: ___

NOTE ON ORIGINAL PACKAGE: The uploaded package's original governance.md
declared "Status: READY_FOR_SHADOW_MODE" without having gone through
this template or ChatGPT review. That status has been reset to Proposed
here. Per WOW governance, Claude does not self-approve patches — this
file is a submission for ChatGPT review, not a confirmation of
activation.

═══════════════════════════════════════════════════
