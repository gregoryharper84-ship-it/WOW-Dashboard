# NFL Full-Week Receipt Conservation — Independent Read-Only Auditor

> For future agentic workers: use `superpowers:executing-plans` or `superpowers:subagent-driven-development` with RED/GREEN regression tests. This plan was implemented locally before review and is not certification.

**Objective ID:** WOW-V17-NFL-WEEKLY-EXACT-CONSERVATION-20261112

**Goal:** Prevent a partial 2026 Week 10 NFL Pick'em coverage readback from being mistaken for a 14/14 qualified model slate, without touching scoring, source acquisition, calibration, terminal policy, or existing owner branches.

**Reason:** The project accepted source reports of 13 distinct Week 10 fitted predictions and no observed Week 10 async submission. A prior `LIKE 'NFL:2026_10_%'` textual query included an unrelated October Week 4 key, creating an apparent 14-event count. Later stored prediction flags on Week 10 are `model_probability_publishable=false` despite older `true` records. Neither a historical `true` flag nor a caller's source label certifies a current prediction.

## Nonoverlap and authority

- New `artifacts/wow-engine/v17/nfl_weekly_receipt_conservation.py` and one standalone test file; do **not** edit the already owned runtime scorer (`nfl_pickem_runtime`), forward-cohort grader (#2599), immutable lookup (#2404), display board (#2533), NHL #2637, Freebuff #2638, NFL count-props #2605, or #2642 opportunity reacquisition.
- Reads *caller-supplied* canonical week schedule, immutable NFL event prediction rows, and async Pick'em run rows. Cannot independently authenticate the schedule, Action authorization or source byte hashes; reports those unresolved even for coverage-observed output.
- Fixed `season` and `week` comparisons against official game IDs; no `LIKE` prefix or event-key fallback. Repeats of immutable per-game predictions select the most recent **pregame** record, so old publishability cannot supersede a newer hold.
- Never write to any production data store, submit a scoring request, emit fitted probabilities, rank picks, claim a terminal release, or execute a wager.

## Plan and verification

1. **TDD RED:** Create 12 tests for missing Monday game, false Week 4 prefix collision, 13-vs-14 official count, stale historical `true` flag, duplicate schedule event, postkickoff-only prediction, kickoff mismatch, missing source snapshot ID, wrong-week async run, valid-but-uncertified full coverage, wrong-week event ID, malformed/unhashable source fields. Initial RED 12 failure cases with `NotImplementedError`.
2. **GREEN:** Implement pure `audit_weekly_receipts(schedule_rows,prediction_rows,run_rows,season,week,expected_game_count,schedule_snapshot_id)`. Return per-event deterministic receipts and global typed blockers; preserve `V17_TERMINAL_REDUCER`, `probability_publishable=false`, `rank_eligible=false`, `can_execute=false`.
3. **Local check:** `cd artifacts/wow-engine && python -m pytest -q test_nfl_weekly_receipt_conservation.py` → `12 passed` in isolated source tree; `python -m py_compile v17/nfl_weekly_receipt_conservation.py`.
4. **Hosted acceptance:** Push to dedicated PR. Run exact-head `wow-engine-verify`, `wow-verify`, rapid regression, CodeQL and protected App checks where classified. Independent SIRT adversarial review must reject source authentication and model publication claims, and examine any cross-sport ID false positives.
5. **Integration later (separate owner):** The existing authenticated runtime producer / Week10 owner may consume this offline auditor for readback if its proof and source identity survive independent review. **Do not wire an extra scheduler** or invoke an Action without verified authorization.

## Acceptance boundaries

The only positive status is `COVERAGE_OBSERVED_NOT_CERTIFIED`, requiring an externally supplied exact week schedule of the expected size, one immutable matched pregame prediction per event, and an observed completed async run bearing the requested season/week. Even this status does **not** establish source authentication, certified model output, a calibrated probability or production authorization. Missing Monday is typed and preserved; other Week 4 records cannot enter the denominator.

**Owners:** ChatGPT isolated code/test PR; AMRT/Engineering potential runtime integration; SIRT independent model-governance challenge; Release Authority protected merge. **Status:** review-only, not merged/deployed/certified.
