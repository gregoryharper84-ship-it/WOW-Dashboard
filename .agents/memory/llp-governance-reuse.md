---
name: LLP V16.1 governance reuse is legacy-compatibility only
description: Reuse gate_engine/llp_governance.py for explicitly V16-compatible market/edge classification; never use LLPLabel/effective_label as V17 sporting probability, rank eligibility, terminal disposition or wager approval.
---

# LLP governance reuse — compatibility boundary

## Controlling generation and authority

The original `gate_engine/llp_governance.py` implements **LLP-PATCH-2026-06-27 Execution Governance v16.1**. Its `LLPLabel` enum (APPROVED, PLAYABLE, WATCH, SCOUT, REJECT, CUT) and `run_llp_governance()` validator are preserved for compatible historical/legacy routes. This is a compatibility fact, **not** V17 authority.

The current generation is V17 per `docs/wow/GENERATION_STATUS.md` and `docs/wow/LLP-V17-AUTHORITY-OVERLAY-2026-09-10.md`.

- `V17_TERMINAL_REDUCER` is the **sole global terminal authority**.
- `LLP_TEAM_BETTING_ENGINE` routes exact team/event winners and upsets to one registered fitted sport-specific specialist. `WOW_PROP_LANE` routes player/scalar props to one exact specialist.
- No `LLPLabel`, legacy `effective_label`, historic edge floor, market prior, sportsbook odds, or recent-form estimate is a V17 model probability, calibrated lower bound, rank eligibility flag, publication approval, or execution authorization.
- For V17, `can_execute=false` and dry-run-only/no live wagers or market orders remain invariant.
- Missing market/edge data cannot erase an otherwise valid sporting probability when the active V17 model contract preserves it. V17 typed model and Action failures remain distinct; a legacy validator may not relabel them.
- V17 release, model activation and live Custom GPT editor parity require their *own* exact-head and live receipts; this memory entry cannot attest them.

## Reuse without authority escalation

For **explicitly legacy V16-compatible** LLP market/edge validation or Kalshi sports bridge logic, reuse the existing `gate_engine/llp_governance.py` module rather than duplicating `LLPLabel`, `cap_label`, or its tested classification rules. In those legacy responses `effective_label` is a **legacy local market/classification outcome only**. Do not pass it to a V17 global terminal reducer as an override and do not present `LLP_PLAYABLE` or `LLP_APPROVED` as betting permission.

V17 code may import a **specific nonterminal validation primitive** from the old module if its controlling V17 contract explicitly requires it (for example, `gate_engine/moneyline/routing_policy.py` uses persisted calibration-ledger read/validation checks). Such import never implicitly imports V16 label authority, universal market-first gating, execution rights, or legacy probability thresholds.

Before changing a route, inspect actual call sites, V17 specialist registry, current Action/receipt schema and relevant regression tests. A legacy Flask/Kalshi endpoint still running in the repository is **not evidence** that its older terminal labels govern V17. Do not remove existing legacy validators or weaken their own fail-closed behavior merely to silence the naming conflict.

## Historical unit-test note (preserved)

Importing `artifacts/flask-scoring-api/app.py` at module scope was reported to hang in an older session because of cron/DB startup side effects. For legacy pure-function tests, consider extracting the actual function source by AST line range with only necessary dependencies, rather than importing the whole server. Live environment behavior must still be checked through authenticated runtime evidence when affected.

## Acceptance

A change affecting this guidance must prove: (1) V16-compatible classification still works in its own route, (2) V17 exact specialist probability and typed failure semantics remain untouched, (3) no legacy `effective_label` can replace V17 terminal output, (4) current protected repository checks and independent App attestations pass, and (5) `can_execute=false`. Merely editing these instructions does not prove a live model/runtime repair.

**Tracking:** GOV-2026-11-04-002 / issue #2440. Documentation-only compatibility clarification, not a production feature activation.
