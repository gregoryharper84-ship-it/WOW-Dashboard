# SIRT — Three-phase reliability and model progression evidence build

**Change owner:** SIRT independent assurance only.  
**Build state:** Phase 3 evaluator source candidate; NOT a deployed autonomous worker, active production monitor, model certification, or production restoration.  
**Priority:** P0 NFL/NBA/NHL/NCAAF restoration remains ahead of unrelated feature expansion.  
**Safety:** `can_execute=false`; `V17_TERMINAL_REDUCER` remains authoritative.

## Phase 1 — Autonomous recovery (Engineering/AMRT, existing work)

The Engineering Conductor and AMRT own implementation, agent dispatch, atomic writer leases and authorized provider fallback. SIRT independently observes whether the existing repair lifecycle meets its contract.

Re-use:
- Seven-role AMRT Engineering adoption PR #1963 (merged).
- Existing AMRT handoff/closure evaluators and engineering receipts.
- Engineering autonomy #1021 / #1688; provider restoration #2014 / #2008 and currently open PR #2058.
- Four-sport fair dispatch PR #2103 (merged).

**Production evidence required** before asserting Phase 1 operational: authenticated dispatch and completion receipts, per-incident accountable owner, bounded retry/fallback across already authorized providers, lease-safe source patch, no lost or stale work, and independently verified repair rather than a worker heartbeat. An open provider quota or an inactive runtime cannot be relabeled a restored model.

SIRT does not run Engineering workers, assume lease ownership, approve provider spending or write deployment workflows.

## Phase 2 — Product acceptance (Engineering/LLP/WOW Betting Intelligence)

Use the existing source-only `v17.product_outcome_acceptance` evaluator from merged PR #2088 and the existing model acceptance harness #1935 / cross-sport factory #1689. Do not build another acceptance controller.

**Production evidence required**: an exact current sport-market request with controlling fitted specialist, producer-attested calibration/refresh, valid pregame/live boundary, conserved requested inventory, row-level terminal receipts, matching Action/Render/DB run identity, independent QA/Release and SIRT production readback. NBA 27-market replay requires 27 original labels with distinct `MODEL_UNAVAILABLE`, unattempted, invalid-input and validly scored dispositions; no silent omissions or fabricated probabilities.

SIRT audits the resulting evidence; Engineer/AMRT repairs, model owners maintain probability correctness, and Independent Verification remains the separate closure authority.

## Phase 3 — Historical model improvement (this SIRT source build)

New pure functions in `v17.sirt_model_progression_audit`:

- `audit_challenger_replay`: compare champion and challenger Brier scores only for *paired* immutable out-of-sample pregame records from the identical `sport/market_family/cohort_id/event_id/market_id`. Require source snapshots, immutable prediction receipts, calibration references, independently verified settlement, prediction before start, settlement after start, and single artifact version per cohort.
- Refuse a comparison for missing pairs, duplicates, incompatible holdout splits, mismatched settled outcomes, unverified rows, live-model substitutions or inadequate denominators. Default 30 matched pairs is a reporting floor, **not certification sufficiency**.
- `audit_confirmed_recurrence`: identify recurring independently confirmed root causes and incidents missing prevention regression receipts. Unconfirmed symptom similarity cannot establish common cause.
- The offline CLI takes explicit JSON arrays of `observations` and `incidents` and fails closed on missing or unverified evidence. It does not declare a JSON file to be certified production truth.
- Outputs are always `RESEARCH_ONLY_NO_PROMOTION` or evidence holds; the auditor never changes fitted probability, promotes a challenger, certifies an in-play model, opens orders or authorizes release.

### Verification

```bash
cd artifacts/wow-engine
python -m pytest -q test_v17_sirt_model_progression_audit.py

# Optional offline JSON evidence audit (no network, credentials or writes)
python v17/sirt_model_progression_audit.py --evidence-json /path/to/immutable-evidence.json
```

Isolated fixture run: **23 passed locally on October 26, 2026**. The exact GitHub PR head still requires its own CI and protected reviews. This is not live production evidence.

### Integration after source review

Engineering may add a read-only consumer to the existing validation/observability pipeline only after confirming the authoritative production database, canonical record schema and provenance, sample completeness and exact SHA. No new cron, queue, model registry, Conductor, trusted signer or background service is created by this PR. Until then, Phase 3 is implemented as a pure evaluator but **not live/activated**.

### Closure definition

No phase may be called independently complete merely from a source PR, healthy backend, green CI, merged documentation or detached fixture metrics. Phase 3 completion requires a genuine read-only cohort from persisted historical run/settlement records, verified data source coverage, independent review, and an attributable report; champion/challenger promotion remains separately governed Class C model work.
