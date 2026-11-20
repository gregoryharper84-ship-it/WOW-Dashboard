# NFL Prospective Capture → Independent Grade → Scientific Review (Design)

**Status:** DESIGN / RESEARCH-ONLY — no automatic certification or trading.

**Owner:** existing WOW V17 NFL team-event model and scientific-validation workstream; Project Hub #2076. Reuse merged PRs #2706, #2707 and #2716. Do not create another approval team, model registry, scheduler or trading path.

## Problem and actual evidence (November 18, 2026, 14:xx UTC)

The prospective runner and PostgreSQL clock/immutable-write trigger exist; PR #2716 moved seasonal sweeps to 12:17 and 18:17 UTC and widened pregame eligibility to 48 hours. The connected `wow-engine-validation` Supabase project has one historical challenger record (October 5), zero November 17+ challenger forecasts, and zero graded challenger records. A verified `SCHEDULES` source snapshot was acquired November 18 13:11:38 UTC. GitHub PR verification is not a real `schedule` / protected-`main` capture, and a pregame capture cannot be graded until after a real final. The record set is not enough to certify a challenger.

The existing `runner.run_grade` reconciles NFLverse and ESPN but is still the writer being audited. Its output `POSTGAME_TWO_SOURCE_RESEARCH_ONLY` explicitly does not confer independent NFL-league certification. A second *read-only* cross-check and version-cohort evaluation is required. `can_execute=false` is a safety invariant, **not an error to fix**.

## Approaches considered

1. **Trust the runner's green workflow status** — least code, but fails: an idle window and write success do not independently prove frozen identity, outcome, or accuracy.
2. **Reuse the current writer, add a separate read-only acceptance auditor (selected)** — independently recomputes record/provenance/settlement arithmetic from exported immutable rows and source manifests; no new tables or secrets in PR CI; costs no extra provider account. Its reports are research evidence, not a certification authority.
3. **Build a separate second scoring/settlement production service** — stronger process isolation at substantial complexity and cost, duplicates owner work, rejects Triple D simplicity.

## Architecture

```
Existing protected-main cron (12:17/18:17 UTC)
  -> canonical schedule SHA + fixed challenger trainer
  -> immutable PostgreSQL pregame row (DB clock < kickoff)
  -> after final: existing ESPN + NFLverse score reconciliation
  -> one-time graded row + source manifest
  -> separate read-only acceptance audit of an exported cohort
  -> frozen paired incumbent-vs-challenger metrics + uncertainty
  -> independent scientific decision -> optional separate governed model release
```

**Source authority:** A row alone cannot certify its own source authenticity. The audit requires a separately obtained exact-source result record with a matching event/kickoff/teams, ESPN/NFLverse identities and raw hashes. A check of manifest/hash shape or Brier math alone is an **integrity check, not independent result verification**. The `audit_receipts.py` module checks persisted row/manifest math and explicitly refuses to call the result independently attested unless the caller supplies an independently acquired final. No fabricated finals.

## Gates and state labels

- **G0 — Capture execution:** GitHub run must actually be `schedule` or approved `workflow_dispatch` on protected `main`, have immutable `capture.json`, `run_ok=true`, and a matching database row. Differentiate `NO_EVENTS_IN_CAPTURE_WINDOW`, `EVIDENCE_ALL_HELD`, absent run, stale snapshot, already-frozen and true `CAPTURED`. A green PR test is not G0.
- **G1 — Authentic pregame prediction:** `created_at` and `prediction_created_at` strict before exact kickoff, exact canonical event/side, approved source snapshot SHA and fetched timestamp, feature inputs hash, model artifact hash, immutable version/calibrator lineage; safety flags false. Do not backfill.
- **G2 — Independently reconciled grade:** game completed, separate ESPN and NFLverse evidence with matching event, kickoff, teams and non-tied exact final; audited grade record is one-way and arithmetic (`brier`,`log_loss`,`hit`) independently recomputes. Raw source digests must match persisted grade manifest. Conflict/missing evidence => `HOLD`, never infer a win.
- **G3 — Paired forward cohort:** Use only *frozen at the same event cutoff* incumbent receipt stored in challenger research metadata; exclude unmatched events from comparative accuracy claims. Track coverage, paired Brier/log loss and winner accuracy, outcome calibration, exact challenger model version, lineages and week-clustered uncertainty. Retrospective replay (108 pairs) is contextual, not prospective.
- **G4 — Scientific decision:** prespecified cohort and model versions; require sufficient independent forward sample and passing incumbent NFL certification metrics as context (existing checklist requires >=100 eligible forward grades), plus paired uncertainty excluding no-improvement for claimed **superiority**. Calibrated lower bounds and subgroup stability require independent review. If uncertainty spans no lift, status is `NOT_PROVEN`, regardless of favorable point estimate. Passing numerical checks produces only `READY_FOR_INDEPENDENT_SCIENTIFIC_REVIEW` and never writes `CERTIFIED` or modifies runtime serving.
- **G5 — Separate authorized release:** only after G4, reuse protected main, independent accepted certification catalog, trusted terminal `V17_TERMINAL_REDUCER`, `can_execute=false`. Live wager execution or Pick'em submission is not part of this project.

## Stop conditions and regression focus

- Forged `prediction_created_at`, missing server `created_at`, or either at/after kickoff -> reject G1.
- Mismatched model/event/source hash or unexpected mutable probability flag -> reject G1.
- A graded row with contradictory sources, tie, missing final, missing raw hash or wrong grade arithmetic -> reject G2.
- Incumbent receipt written after kickoff, wrong event kickoff or absent -> no paired grade, never substitute public odds.
- Duplicate official events and mixed challenger model family/versions -> reject cohort/certification; no post-hoc selection.

## Operational milestones

- **First available capture:** protected-main sweep Nov 18 at 18:17 UTC; verify actual GitHub run + immutable DB row for game `2026_11_IND_HOU` (Nov 20 01:15 UTC), with no false assertion before it occurs.
- **First grading opportunity:** only after the Colts–Texans game is final and both external sources independently publish exact matching evidence. November 19 local kickoff is not the grading time.
- **Sample development:** collect genuine frozen head-to-head forecasts over many slates; execute the predeclared cross-version comparison, signed scientific review, and only then a distinct serving rollout.

**Non-goals:** no paid feeds, no new autonomous release controller, no new approval team, no database write by auditor, no backfilled prospective evidence, no automatic model promotion, no wagers. Preserve `can_execute=false` and `V17_TERMINAL_REDUCER` sole terminal authority.
