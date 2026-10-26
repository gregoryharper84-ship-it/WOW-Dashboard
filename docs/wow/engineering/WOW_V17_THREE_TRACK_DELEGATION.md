# WOW V17 — Triple-D three-track engineering delegation

**Scope:** source-only Class B orchestration/handoff contract. Existing GitHub issue queue, WOW Ecosystem Conductor, Engineering worker, LangGraph, AMRT, specialist model experiment workflow, SIRT and Independent QA/Release remain the only operational authorities. This is **not** a new service or scheduler, not a model, and not a release authorization.

**Controlling rules:** `V17_TERMINAL_REDUCER` sole global terminal authority; `can_execute=false`; no wagers/orders; exact fitted model, calibration and lower-bound proof still required before any governed probability. No service-role credentials in ChatGPT Actions.

## Three linked tracks (one accountable incident owner)

| Track | Existing issue/authority | Concrete queued work | Completion evidence |
| --- | --- | --- | --- |
| **1 — RECOVER (Debug → Dissolve → Deploy)** | AMRT project [#2078](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/2078), Conductor/Engineering; existing P0s #1841 NFL, #1950 NBA, #1952 NHL, #1951 NCAAF, #823 acquisition | Run first-boundary diagnosis, smallest authorized repair, exact-head tests, separate independent QA/Release, matching deployed code and genuine authenticated Action/terminal/persistence readback. Also triage NBA prop acceptance failures under existing P0 owner; do not rewrite unrelated sport tickets. | Independent live verification attached to each **original incident**, not merely a successful PR or worker dispatch. |
| **2 — IMPROVE (Science)** | Active priority board [#1128](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1128), existing `MODEL_IMPROVEMENT_EXPERIMENT_AGENT` and controlling sport-model owners | Receive referenced Track 1 reproductions and confirmed failure modes; make one non-serving fitted-model challenger per supported cohort with immutable pregame replay, counterexamples, time-based holdout and targeted regression. | `RESEARCH_VALIDATED` evidence on a non-serving experiment path; no serving calibration, fitted artifact or model probability changes without Class C certification. |
| **3 — PREVENT (Reliability)** | Independent SIRT [#1563](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1563), existing SIRT external sentinel plus protected QA/Release | Receive validated Track 2 failure/cohort findings; add adversarial regression/monitoring hypotheses for root cause recurrence, source identity, signer parity, probability calibration and production user-path proof. | Independent evidence, mapped regression/monitor and repeat-incident metrics. No SIRT self-approval or blanket PASS. |

## Handoffs are deliberately separated from approvals

```
Existing Conductor / AMRT (Track 1)
    verified investigation receipt (issue + source SHA + root cause + regression)
       ↓ EVIDENCE_ONLY, no release claim
Existing fitted-model research and challenger lane (Track 2)
    research-validated non-serving experiment + replay/holdout/counterexamples
       ↓ EVIDENCE_ONLY, no new calibrated probability claim
Independent SIRT / QA observability (Track 3)
    adversarial regression, recurrence watches and production readbacks
       ↘ defects return to the SAME existing incident/Conductor, not a new ticket
```

A **confirmed diagnosis** may be passed into non-serving research before a production fix is complete, but is never called production verified. This permits safe scientific work without falsely loosening model publication. If Track 2 has no authenticated upstream evidence, it stays `WAITING_FOR_TRACK_1_EVIDENCE`. If Track 3 has no replay/holdout/counterexample-backed Track 2 receipt, it stays `WAITING_FOR_TRACK_2_EVIDENCE`.

The queue compiler in `artifacts/wow-engine/v17/engineering_three_track_handoffs.py` enforces:
- Exact fixed Track 1 → 2 → 3 topology and one existing owner issue per track.
- Dry-run/terminal guard at envelope and each supplied receipt.
- Exact incident/SHA correspondence across the first two handoffs.
- Reproducible root cause evidence required before the science lane.
- Non-serving model experiment path, holdout and counterexample references required before SIRT handoff.
- No production approval, lease acquisition, background dispatcher, model promotion or independent signoff by the compiler.
- Stable handoff fingerprints for deduplication. Authenticated Conductor and existing receipt writer must validate and persist any *actual* handoff separately.

## Queue execution: existing channels, no parallel control plane

Current routing is configured in `artifacts/wow-engine/v17/engineering_three_track_queue.json`. The existing hourly `wow-v17-24h-engineering-closure-loop.yml` **displays** the queue state each run. Track 1 uses existing P0 Engineering/AMRT issue dispatch and writer leases. Track 2 uses the existing non-serving `wow-v17-model-improvement-experiment.yml` path and scientific Class C governance. Track 3 uses SIRT's already scheduled external read-only sentinel and original assurance issue. This source-only change does **not** automatically trigger any of these workflows or claim that the runtime has ingested a handoff.

Evidence-file CLI example:

```bash
cd artifacts/wow-engine
python -m v17.engineering_three_track_handoffs --config v17/engineering_three_track_queue.json --output /tmp/wow-three-track-queue.json
python -m pytest -q test_v17_engineering_three_track_handoffs.py
```

To review a genuine external receipt envelope, add `--evidence /path/to/verified-engineering-evidence.json`. Do not fabricate that envelope to advance a state. Queue snapshots are advisory; runtime activation requires a protected merge, exact-head tests, independent QA/Release and live worker evidence.

## Acceptance and staging

1. **Source complete:** three-track config, deterministic compiler, positive/negative tests, hourly summary integration; exact-head CI checks required.
2. **Review queue admitted:** link existing incidents #2078, #1128, #1563 and record the contract in existing issues; preserve any other active writer/lease.
3. **Track 1 production-ready:** AMRT #1843 still needs authorized integration if open; no incident counts as fixed without live authentic Action-to-persistence proof.
4. **Track 2 execution-ready:** first genuine source/science receipts flow through the existing worker/science experiment route; model promotion remains Class C-gated.
5. **Track 3 operational:** independent SIRT signs regression/recurrence readbacks against production; feedback reopens the original issue or routes through the same Conductor.

**Do not report steps 3–5 as complete from this implementation alone.** The no-duplicate, no-self-approval, no-new-dispatcher and no-wager contracts remain mandatory.
