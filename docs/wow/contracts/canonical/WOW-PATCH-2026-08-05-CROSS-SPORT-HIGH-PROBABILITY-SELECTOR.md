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

ORIGIN:            [X] Proactive model improvement — encodes the structure
                       of a previously successful four-leg cross-sport card
                       (2 tennis match winners, 1 MLB moneyline, 1 WNBA
                       moneyline) into a reusable daily selector skill

─────────────────────────────────────────────────
STATUS
─────────────────────────────────────────────────
[X] Proposed         (2026-08-05 — Claude initial filing)
[X] Step 3 Done      (2026-08-05 — ChatGPT approved with required revisions)
[ ] Approved — pending dashboard build
[ ] Deployed
[ ] Rejected

CURRENT MODE:      ANALYTICAL SHADOW MODE ONLY
                   No output from this skill is actionable until calibration
                   milestones confirmed via a new patch.

─────────────────────────────────────────────────
PROBLEM STATEMENT
─────────────────────────────────────────────────
No single skill scans across sports in one pass and separates results into
four distinct output lanes with weakest-leg elimination and explicit
cross-leg dependence audit. This patch adds that skill additively — no
existing lane or gate is modified.

FAILURE TAG(S):    N/A — proactive addition

─────────────────────────────────────────────────
CHATGPT STEP 3 REVIEW SUMMARY (2026-08-05)
─────────────────────────────────────────────────
Decision:          APPROVED WITH REQUIRED REVISIONS — ANALYTICAL SHADOW MODE ONLY

Required revisions applied in this filing (all mandatory before activation):

  R-1  Permanent governance block expanded to 7 invariants:
       auto_execute=false, requires_human_confirmation=true,
       stake_sizing=false, bankroll_allocation=false, can_execute=false,
       dry_run_only=true, NO_PLAY=valid.
       All seven are permanent and unconditional regardless of calibration
       maturity — execution-layer safety does not lift with calibration quality.

  R-2  wow-high-hit-engine conflict language corrected.
       Skill is absent from the active stack. No coexistence or replacement
       decision is required. If introduced in a future patch, the relationship
       to Lanes A and B must be resolved in that patch.

  R-3  Immutable prediction ledger — explicit graceful degradation.
       Step 15 emits prediction_write_status=NOT_AVAILABLE, does not block
       output, requires the field in every response. Silently passing the
       check is prohibited.

  R-4  Cross-ticket exposure ledger — PARTIAL status handling.
       Health check reports PARTIAL with detail. Slip-scoped modules
       (slip_exposure_ledger, cross_slip_exposure, cross_ticket_governor)
       are queried as advisory; missing prediction-keyed data is flagged in
       output, not suppressed.

  R-5  Kalshi Portfolio Governor routing — enforced, not advisory.
       All Kalshi sports-contract candidates must route through
       GET /wow/kalshi/category-scan (portfolio_governor, max 2 total,
       max 1/event, can_execute=False always). Independent scanning is
       prohibited.

  R-6  Kalshi combo restrictions — enforced per combo_gate.py.
       Reliability Freeze: 1–2 markets allowed, 3 = REJECT_BAD_STRUCTURE,
       4+ = HARD_REJECT_COMBO_MULTIPLICATION.

  R-7  Recovery Mode combo restriction.
       Selector presents only portfolio_governor survivors. Non-survivors
       land in Lane D with governor reject label verbatim.

  R-8  Ownership separation.
       This skill owns ranking and selection only. Terminal-label authority
       belongs to specialist gates and the backend. Selector never authors,
       upgrades, or overrides a terminal label.

  R-9  Four output lanes clarified with scope, authority, and field specs.
       Lane C (edge) excludes a candidate on missing market data without
       blocking Lanes A/B. Lane D reproduces reject labels verbatim.

  R-10 Final refresh made unconditionally mandatory.
       If refresh cannot be completed, output is NO_PLAY with
       final_refresh_status=FAILED. No stale-data output permitted.

  R-11 Mandatory ledger status block in every output.
       Fields: prediction_write_attempted, prediction_write_status,
       cross_ticket_exposure_status, final_refresh_status.

  R-12 Regression tests expanded to executable pytest assertions.
       See gate_engine/tests/test_cross_sport_selector_regressions.py.
       Fixtures, expected labels/ceilings/blockers, and reconciliation
       logic are defined. Skill may not be added to skill-registry.json
       until all tests pass.

─────────────────────────────────────────────────
RULE CHANGE
─────────────────────────────────────────────────
AFFECTED SECTION:  New skill file — no existing WOW-MASTER-SPEC.md section
                   modified. Additive only.

NEW RULE:          wow-cross-sport-high-probability-selector enters the
                   active skill stack in analytical shadow mode with the
                   seven permanent invariants above. No candidate from this
                   skill's output is playable on this skill's output alone.
                   Specialist gates still govern final leg approval.

─────────────────────────────────────────────────
ANALYTICAL IMPACT
─────────────────────────────────────────────────
Adds a fifth research lane alongside PrizePicks/Props, LLP, Kalshi, and
other moneyline research. Does not change approval logic for any existing
lane. No code changes required at this stage; dashboard work is triggered
only if a future patch activates the immutable prediction ledger.

DASHBOARD IMPACT:  No — analytical/skill-layer only at this stage.

─────────────────────────────────────────────────
legacy platform BACKEND DEPENDENCY RESOLUTION (2026-08-05)
─────────────────────────────────────────────────
"Immutable prediction ledger":
  STATUS: NOT_AVAILABLE — Step 15 degrades to PREDICTION_WRITE_UNAVAILABLE

"Cross-ticket exposure ledger":
  STATUS: PARTIAL — slip_exposure_ledger, cross_slip_exposure, and
  cross_ticket_governor available; prediction-keyed ledger not yet built

"wow-high-hit-engine" overlap:
  STATUS: NOT_A_CONFLICT — skill absent from active stack

─────────────────────────────────────────────────
TEST REQUIREMENTS (R-12)
─────────────────────────────────────────────────
File:  gate_engine/tests/test_cross_sport_selector_regressions.py

Tests must pass before skill-registry.json activation:

  POLICY-001  can_execute=False is unconditional
  POLICY-002  requires_human_confirmation in every output schema
  POLICY-003  NO_PLAY is a valid terminal state
  COMBO-001   1-market Kalshi combo is allowed by combo_gate
  COMBO-002   2-market Kalshi combo is allowed by combo_gate
  COMBO-003   3-market Kalshi combo is REJECT_BAD_STRUCTURE
  COMBO-004   4+-market Kalshi combo is HARD_REJECT_COMBO_MULTIPLICATION
  LEDGER-001  Immutable prediction ledger unavailable → NOT_AVAILABLE (non-blocking)
  LEDGER-002  Cross-ticket exposure ledger → PARTIAL status
  LANE-001    Candidate missing market data excluded from Lane C, not Lanes A/B
  LANE-002    Low lower-bound candidate excluded from Compact Card
  LANE-003    Same-event Kalshi pair capped at max 1 by portfolio governor
  GOVERN-001  Winning prior card does not upgrade current candidate
  GOVERN-002  Missing event identity blocks all lanes
  GOVERN-003  Human confirmation field present in output schema
  GOVERN-004  Same injury thesis across legs → at most one retained
  GOVERN-005  Cross-book legs not presented as one executable parlay
  GOVERN-006  Outcomes never overwrite prediction record
  GOVERN-007  NO_PLAY returned when nothing qualifies

─────────────────────────────────────────────────
DEPLOYMENT ORDER
─────────────────────────────────────────────────
[X] Step 1 — Claude confirms patch against active spec — DONE (2026-08-05)
[X] Step 2 — Claude updates spec (N/A — skill-only) — DONE
[X] Step 3 — ChatGPT review — DONE (2026-08-05, APPROVED WITH REQUIRED REVISIONS)
[X] Step 4 — Required revisions applied to SKILL.md — DONE (2026-08-05)
[X] Step 5 — Executable regression tests written — DONE (2026-08-05)
[X] Step 6 — Registry, route, and smoke tests run — DONE (2026-08-05)
[ ] Step 7 — skill-registry.json activation — BLOCKED until all tests pass
[ ] Step 8 — WOW-SHARED-NOTES.md patch queue update — DONE (2026-08-05)

═══════════════════════════════════════════════════
