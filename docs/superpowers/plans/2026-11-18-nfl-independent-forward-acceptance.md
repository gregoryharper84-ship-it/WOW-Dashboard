# NFL Independent Forward Acceptance — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. TDD RED before GREEN. Keep the existing Project Hub #2076 and source PRs #2706/#2707/#2716.

**Goal:** Convert scheduled challenger capture and two-provider grading into independently auditable, immutable forward evidence with a scientific certification decision gate (not automatic certification).

**Architecture:** Keep current capture/grader and SQL immutability. Add an isolated stdlib read-only audit module accepting exported row + independently acquired source evidence; report errors and matched paired metrics separately. Do not change the serving model or launch another scheduler.

**Tech Stack:** Python 3.11 stdlib, pytest, existing Supabase PostgreSQL and GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-18-nfl-independent-forward-acceptance-design.md`

## Global constraints

- `can_execute=false`, `probability_publishable=false`, `promotion_authorized=false`, `V17_TERMINAL_REDUCER` authority.
- No autotrading, no Pick'em submission, no automatic promotion/certification or model serving changes.
- Do not retroactively create a “pregame” prediction; source clock and DB server timestamps are independent proof requirements.
- NFLverse + ESPN result agreement is research-level evidence, not league-official certification.
- No additional scheduled task, paid source, secret, or independent-review team.

## Review focus

1. Grade record could carry correct Brier with a wrong event: assert canonical identity and sources before arithmetic.
2. A frozen receipt may have prediction timestamp before kickoff but DB created afterward: require BOTH pregame.
3. A matching grade can use a score after an unverified tie: explicitly reject ties and provider conflicts.
4. The champion may be postkickoff or absent: omit pair rather than guessing champion probability.
5. A single favorable retrospective delta could be mislabeled scientifically certified: never produce `CERTIFIED` or promote.

### Task 1 — Pure pregame acceptance (research-only)

**Files:** Create `artifacts/wow-engine/v17/experiments/nfl_prospective/audit_receipts.py`; test `test_audit_receipts.py`.

**Interfaces:** `audit_forecast(row: Mapping[str,Any]) -> dict` returns `status`, `reasons`, `paired_incumbent_eligible`, `can_execute: false`.

- [x] Write failing fixtures for frozen + missing/late DB timestamp, wrong hash, broken source lineage and false publication flags.
- [x] Run scoped pytest and observe RED due to missing function.
- [x] Implement stdlib-only independent validation of exact event, model/source hashes, timestamps and safety flags.
- [x] Run scoped pytest GREEN; ensure valid pregame row remains `PREGAME_RECEIPT_VALID_UNCERTIFIED`.

### Task 2 — Independent grade audit and paired metrics

**Files:** Same `audit_receipts.py`, `test_audit_receipts.py`.

**Interfaces:** `audit_grade(row, source_final=None) -> dict`, `audit_cohort(rows, source_finals) -> dict`.

- [x] Write RED tests for source disagreement, manifest digest mismatch, tie, Brier/log loss mismatch, valid independent final, missing/late incumbent and mixed model versions.
- [x] Recompute selected-side grade and incumbent home-side Brier/log loss from actual final, never from the grader's stored Brier; use source hashes and identity.
- [x] Aggregate frozen/graded/verified/paired coverage with exact statuses; insufficient sample remains `NOT_CERTIFIED`.
- [x] Run tests GREEN; separate “grade integrity verified” from independent NFL-league certification.

### Task 3 — Operational evidence and protected review

**Files:** Same audit source/test, optional JSONL CLI in audit module, design and plan docs.

- [x] Add offline exported-input CLI; all auditing is read-only and can run in CI without database credentials.
- [ ] Run full focused and adjacent existing NFL prospective tests plus `compileall`; report all failures.
- [ ] Open isolated additive PR, verify exact changed files and protected native checks; Codex/owner reviews under existing #1830 governance.
- [ ] Merge only with protected acceptance; do not enable model publication.

### Task 4 — First real live acceptance (necessarily time-dependent)

**Files:** Existing workflow `.github/workflows/wow-v17-nfl-prospective-shadow.yml` and existing DB table; no speculative source mutation.

- [ ] Inspect actual post-Nov18 18:17 UTC `schedule` workflow run and `capture.json`, including run event/ref/SHA and `run_ok` status.
- [ ] Read-only query frozen `2026_11_IND_HOU` row; compare `created_at/prediction_created_at < 2026-11-20 01:15 UTC` plus snapshot and feature hashes.
- [ ] If no row, report exact status (`ALL_HELD`, run absent, credentials, history, schedule/freshness, stale source, immutable rejection), and fix upstream root cause with RED regression rather than weakening gates.
- [ ] After official full time, acquire fresh NFLverse and ESPN source evidence independently and run two-source grader; audit stored math and source hashes with separate checker. Missing/contradictory source => HOLD.

### Task 5 — Scientific model-lift decision (multi-week, not this release)

- [ ] Freeze challenger/model version, eligibility, calendar cutoffs and planned cohort analyses BEFORE seeing outcomes.
- [ ] Accumulate sufficient paired forward grades; require >=100 independently acceptable prospective rows as an initial minimum, with power/precision checks and exact-version isolation; do not automatically declare sufficient n=100.
- [ ] Report Brier, log loss, calibration bias/ECE, lower-bound gap, coverage, paired week-cluster confidence intervals and injury/rest/upset subgroup diagnostics; distinguish descriptive from inferential statistics.
- [ ] Require genuine independent scientific decision with predeclared thresholds: if 95% CI crosses no advantage, `NO_PROVEN_LIFT`; if all gates pass, `READY_FOR_SCIENTIFIC_REVIEW` only.
- [ ] A later protected serving PR may reference signed scientific approval; retain execution disablement in all cases.

## Definition of done

**Research code:** independently tested, immutable source/grade audit implemented and protected-reviewed.
**Operational:** actual future-game server-clock pregame forecast and postgame independent grade verified in connected target environment; source identities and hashes read back.
**Scientific:** sufficient prospective, paired, uncertainty-validated evidence accepted by independent science authority and recorded in existing certification ledger.

Do not call the model certified, the live grader independently verified or the service deployed merely because research-code CI is green.
