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
3. **Local check:** `cd artifacts/wow-engine && python -m pytest -q test_nfl_weekly_receipt_conservation.py test_nfl_weekly_receipt_reuse.py` (18 expected tests after adversarial extensions); `python -m py_compile v17/nfl_weekly_receipt_conservation.py`. Keep the initial 12-pass observation as historical only; new exact-head hosted evidence is required.
4. **Hosted acceptance:** Push to dedicated PR. Run exact-head `wow-engine-verify`, `wow-verify`, rapid regression, CodeQL and protected App checks where classified. Independent SIRT adversarial review must reject source authentication and model publication claims, and examine any cross-sport ID false positives.
5. **Integration later (separate owner):** The existing authenticated runtime producer / Week10 owner may consume this offline auditor for readback if its proof and source identity survive independent review. **Do not wire an extra scheduler** or invoke an Action without verified authorization.

## Acceptance boundaries

The only positive status is `COVERAGE_OBSERVED_NOT_CERTIFIED`, requiring an externally supplied exact week schedule of the expected size, one immutable matched pregame prediction per event, and an observed completed async run bearing the exact requested season/week, expected game count, and empty whole-week date selector. Even this status does **not** establish source authentication, certified model output, a calibrated probability or production authorization. Missing Monday is typed and preserved; other Week 4 records cannot enter the denominator.

**Owners:** ChatGPT isolated code/test PR; AMRT/Engineering potential runtime integration; SIRT independent model-governance challenge; Release Authority protected merge. **Status:** review-only, not merged/deployed/certified.

## Adversarial review follow-up (2026-11-12)

- Reproduction tests added **before** the implementation repair: a same-timestamp conflicting publication flag and an after-kickoff reuse of a different game's immutable ID. Invalid request rejection is also asserted. The test-only SHA is `462d2bb13f4ed11b25155649998665481688b11b`; hosted RED confirmation and final GREEN results must be recorded from actual workflow logs rather than assumed.
- Fix: register all canonical event/ID mappings before pregame eligibility filtering; select latest only from trusted pregame `created_at`. A tied latest timestamp with distinct immutable IDs or conflicting flags produces `PICKEM_PREGAME_RECEIPT_ORDER_AMBIGUOUS` and no selected receipt. No publication inference.
- All new `PICKEM_*` typed codes are registered in the existing `artifacts/wow-engine/docs/failure_codes.md` table. SIRT and the authentic protected Apps still own independent Class P acceptance; do not merge from an author's local tests or routine green CI.

## Hosted scope regression RED (2026-11-12)

- CodeRabbit identified a real incomplete-run false-positive: `_weekly_run_matches` previously accepted a partial date-scoped run with matching season/week.
- At test-only SHA `8b080a05e5ddacfea3068489e0e09fdff194dd50`, hosted `wow-v17-rapid-repair` run `43341022329`, job `131609054200` reported **2 failed / 589 passed / 6 warnings**. The two new tests both observed `COVERAGE_OBSERVED_NOT_CERTIFIED` when a partial date selector or a wrong game count should have produced `HOLD`.
- GREEN implementation at follow-up `bad48f679d5ed4a2fb161351e33fa5cf4a5604d1` requires exact producer-owned request fields `expected_game_count` and `requested_slate_dates=[]` in addition to season/week. No new failure code or scorer behavior; mismatch reuses `PICKEM_WEEKLY_RUN_NOT_OBSERVED`. Final hosted GREEN and independent release acceptance must be recorded at the final branch head before promotion.
