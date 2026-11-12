# WOW V17 — Lean Production Reliability & Triple D Acceptance

**Decision:** Owner-approved operating direction, 2026-10-25. **Implementation state:** SOURCE CANDIDATE; this document does not activate a worker, scheduler, monitor, CI gate, live scorer, or production deployment. Existing governed authorization is still required.

**Goal:** *Debug. Dissolve. Deploy. Verify. Prevent recurrence.* The measurable product is an independently verified, current, calibrated probability (or an attributable honest terminal hold) for each requested supported sporting row—not a green infrastructure dashboard.

## Single existing operating chain

| Function | Accountable outcome | Hard boundary |
| --- | --- | --- |
| WOW Betting Intelligence / LLP and sport specialists | User objective, exact event/market routing, fitted probability, calibrated bounds, complete sport/market ledger | Never fabricate probabilities; one fitted controlling specialist per event; no release authority |
| Engineering / existing Ecosystem Conductor | Repair, code, lease discipline, integration and authorized release handoff | One writer; no parallel controller |
| AMRT within Engineering | Model outage triage, root cause, minimal recovery, replay and closure handoff | Reuse existing seven role handlers; never grant itself security or scoring authority |
| SIRT | Independent reliability diagnosis, drift detection, regression challenge, production behavioral readback | Independent assurance, not an Engineering PR author or duplicate release controller |
| Independent QA / Release | Exact-head independent author/reviewer provenance, protected checks, release authorization | No self-approval; PASS only from actual authenticated receipt |
| V17_TERMINAL_REDUCER | Sole final governed publication disposition | No specialist, Scout, market auditor or PM may override |

Read alongside [AMRT recovery](WOW_AMRT_RECOVERY_TEAM.md), [AMRT registry](WOW_AMRT_TEAM_BOOTSTRAP.md), and the existing V17 system governance. This addendum changes no Class A/B/C authority, signing identity, approvals, model coefficients, calibration thresholds, betting execution capability, credentials or deployment security.

## Eight operating improvements (extend, do not duplicate)

1. **Automatic Triple D admission:** existing event/scheduled Engineering sweeps detect typed failures; deduplicate by incident and stable fingerprint; route via existing Conductor/AMRT manifest and lease only. A source file, issue, or chat message is not an activated watcher. Alert separately when actual scheduled sweeps or heartbeats are absent.
2. **No abandoned blockers:** every admitted item retains incident_id, candidate_id if applicable, current_owner, next_owner, evidence_ref, next_action, reentry_trigger and exact failure stage. A stalled/expired lease is reentered via existing atomic authority, never forcibly stolen. Every candidate must reach an attributable terminal disposition or remain visible as pending.
3. **Provider-independent recovery:** bounded retries for transient errors; escalate provider quotas/timeouts to an already authorized alternate worker, or evaluate verified exact-blob existing source candidates without fresh LLM inference. Do not automatically invoke unapproved paid providers, spoof an Engineering App identity, blindly retry ambiguous writes or exceed an existing spend fence. Deterministic malformed input needs repair, not retry.
4. **Exact production acceptance:** review and CI head must match; protected merge, deployed Render revision and Action revision must reconcile; validate correct fitted model, real model invocation, dynamic calibration/bounds, freshness, full requested slate reconciliation, persistence and terminal receipt; SIRT must independently verify production, not only health or HTTP 200.
5. **Handoff work conservation:** a request/candidate is never silently dropped after Scout, discovery, API ingress, specialist routing, scorer, calibration, terminal reducer, persistence or output. A duplicate thesis is one canonical model row plus traceable source instances. Keep work item owner on every HOLD. Do not mint another queue or failure taxonomy.
6. **Four product metrics:** Product Outcome Completion Rate (supported requests with verified complete delivered outcome), Candidate Conservation Rate (canonical rows with attributable terminal state / canonical rows), First-Pass Completion Rate (supported requests correctly completed with exactly one provider attempt and zero repair cycles), Reliable Decision Availability (rank-eligible verified sporting probabilities / verified eligible candidates in requested scope). Zero or unverified denominator => unknown, never 100%. Break down by sport, market, period, exact-line support, route and model version.
7. **Regression learning:** verified recurring failures become narrow red/green tests and durable incident links; preserve successful comparable cases, exact immutable pregame grading, causal input boundaries and numerical calibration. No universal probability haircuts or Class C promotion without governed out-of-sample evidence. Recurrent source, transport, lease, dispatch and terminal failures need tests at their actual boundary.
8. **In-progress capability:** live/in-play rows are a distinct route and model contract. No pregame leaderboard reuse after kickoff/tipoff/start. Future live specialists require certified live-state hydration, time-lag/clock correctness, event state, opponent/lineup changes, live calibration and lower bound, exact settlement, independent shadow replay, safe hold under missing or late data, and final model acceptance before user-facing ranking.

## Minimum proof before claiming a model restored

The existing AMRT closure gate **still controls infrastructure closure**. The source-only module `v17.product_outcome_acceptance` adds a stricter read-only *model restoration evidence assessment*:

- Real sport/market controlling route and certified fitted artifact; actual model invocation (not market odds, Scout confidence or a guessed percentage)
- Valid raw/calibrated probability and calibrated lower bound; dynamic calibration and failure-path validity where certified
- Correct event/participant, exact pregame/live status, current model timestamp and final-refresh evidence
- Complete requested denominator and row-level terminal receipt, with explicit typed holds and no silent omissions
- Matching authenticated Action/deployment/run identity, immutable storage readback and protected independent QA/Release authorization
- SIRT independent production evidence and regression comparison, including related neighboring routes
- `can_execute=false` and `V17_TERMINAL_REDUCER` invariant

**A completed valid sporting probability survives an unrelated downstream market price failure.** Missing price can hold market/value publication, but cannot be rewritten as MODEL_UNAVAILABLE or erase the already completed sporting probability. Conversely, a missing fitted artifact, bad calibration or stale pregame result cannot be repaired with sportsbook odds, external projections, a generic LLM estimate or an invented lower bound.

State separation remains mandatory: BACKEND_RUNTIME, MODEL_CAPABILITY, REPOSITORY_GOVERNANCE, LIVE_GPT_EDITOR_SYNC. Operational readiness is not proof of an event-level model result.

## Read-only source acceptance audit (no new runtime authority)

- `artifacts/wow-engine/v17/product_outcome_acceptance.py` — consumes attributed external receipts, returns conservation/product metrics and model-specific closure evidence. It never calls remote APIs, changes model or infrastructure state, grants an approval, or creates a runtime failure code.
- `artifacts/wow-engine/test_v17_product_outcome_acceptance.py` — exercises false greens, missing bound/receipt, live-model mismatch, held/scored distinction, unreconciled rows, double counting, zero denominators, identity mismatches, omitted independent verification.
- **Current integration status:** These are source-only evaluators; do not claim them as live telemetry until the existing authenticated worker/validation reporting consumer is explicitly connected and observed on a real production receipt.

Run focused tests from `artifacts/wow-engine`:

```bash
python -m pytest -q test_v17_product_outcome_acceptance.py \
    test_v17_amrt_closure_gate.py test_v17_amrt_operational_lanes.py
```

## Rollout within existing control plane

**Phase 0 / P0 (first):** restore genuinely scored-and-accepted NFL moneyline/publication and currently demanded NBA/NHL/NCAAF routes; prioritize errors affecting user outcome. Check real model support first; don't mark a route restored from green CI, catalog membership or a research-only vector.

**Phase 1 / P0:** integrate recurring typed incident and reentry events in existing AMRT/Engineering worker through a separately reviewed exact-head PR. Activate only authorized provider fallback and previously approved deterministic candidate adoption. Do not add another cron, database, release writer, or bypass an independent check. Reconcile model and action receipts end-to-end.

**Phase 2 / P1:** attach the read-only scorecard to existing validation/Daily observability records after confirming which database is actual production. For each sport/market, publish requested count, eligible count, accepted numerical packages, held/purged reasons, false-green findings and 7/30-day recurrence. Missing sample denominators are unknown, not inferred.

**Phase 3 / P1:** chronological replay and calibration drift monitoring; add dedicated live-model candidates as **research-only** until their own certification and Action acceptance exist. Add minimal automated checks and rollback evidence; production-only changes require production tests.

## Closure and owner-intervention standard

An incident stays owned during any HOLD. The receipt must identify the smallest exact next authorized operation and triggering condition; never keep it as vague "in progress." Independent verification can legitimately HOLD unsafe claims, but Engineering/AMRT must continue non-conflicting authorized work and prepare a bounded repair. Raise user action only when the exact operation truly needs an owner's privilege or explicit authorization; an already-set provider budget or an ordinary temporary quota is not, by itself, a reason to stop unrelated work.

**Terminal safety:** `can_execute=false`; no live wagers, trading, orders or model-probability substitution. **No green by declaration.** A reviewed source PR, passing CI, merged docs, model registry and service health cannot substitute for authentic production probabilities, persisted receipts and independent verification.
