# WOW Multi-Agent Engineering Team

Status: PROPOSED / IMPLEMENTATION BRANCH

This document defines the repo-backed multi-agent engineering organization for WOW V17.

## Non-negotiable governance

- `custom_gpt_identity=WOW_BETTING_ENGINE`
- `runtime_generation=V17_ACTIVE`
- `terminal_authority=V17_TERMINAL_REDUCER`
- `can_execute=false`
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`
- Exactly one controlling fitted specialist owns each sporting probability row.
- Scout/Research roles gather evidence only.
- No sportsbook implied probability, recent result, external projection, narrative judgment, or generic LLM reasoning may substitute for governed fitted-model probability.
- Typed V17 failures must remain distinct.
- Class C model-behavior changes must remain challenger-only until replay, counterexample review, holdout/forward validation, regression, and governed promotion.

## Team topology

### 1. Engineering Lead / Incident Commander
Owns the durable engineering queue, severity, priority, duplicate detection, implementation leases, and handoffs. It selects the single highest-priority repairable R0/R1 incident for implementation while permitting bounded parallel evidence-gathering and review work.

### 2. Reproduction & Root-Cause Agent
Reproduces the incident, correlates GitHub/Render/Supabase/runtime evidence, isolates the first failing stage, records expected versus observed behavior, and produces a root-cause packet. It does not silently patch around unknown failures.

### 3. Implementation Agent
Receives a demonstrated root cause plus an implementation lease. It makes the smallest safe change, preserves governance, and adds targeted tests. Only one implementation lease may exist per incident.

### 4. Adversarial Test & Review Agent
Independently tests the proposed repair. It checks adjacent lanes, typed failures, candidate retention, exact-once semantics, calibration/normalization invariants where applicable, immutable prediction integrity, `can_execute=false`, terminal authority, and regression risk. The implementer may not self-certify final acceptance.

### 5. Release & Production Verification Agent
Owns the final lifecycle: protected CI -> exact green head -> merge -> exact merged SHA -> deploy -> production acceptance -> Render/Supabase reconciliation -> terminal issue state. A PR merge is never sufficient for `FIXED_AND_VERIFIED`.

### 6. Frontier Intelligence & Continuous Improvement Agent
Continuously monitors two independent lanes:

#### AI frontier
- frontier and specialist models
- agent frameworks and MCP/tool-use advances
- multimodal reasoning and board/document extraction
- coding-agent capabilities
- structured output/schema reliability
- retrieval, long-context, evaluation, observability, tracing
- inference latency/cost efficiency
- databases/search/vector systems
- computer-use/browser automation
- safety, control, and hallucination-reduction techniques

#### Sports-betting / sports-intelligence frontier
- sports data providers and APIs
- odds/exchange feeds and market microstructure
- player tracking and event data
- injury/status/news feeds
- computer vision and automated event extraction
- predictive modeling and calibration research
- line movement/price discovery/closing-line research
- live/in-play modeling
- competitor technology
- sport-specific data/modeling advances across all configured leagues
- data latency, pricing, quotas, licensing, and availability changes

The continuous-improvement agent may create findings, radar entries, experiment proposals, and challenger work. It may not directly promote a Class C probability change.

### 7. Experiment / Challenger Agent
Turns approved improvement hypotheses into isolated challenger implementations and evidence packages. Required lifecycle:

`Discover -> Hypothesis -> Challenger -> Historical Replay -> Counterexample Review -> Holdout/Forward Validation -> Regression -> Recommendation`

Production remains unchanged until the governed promotion process approves it.

## Shared execution rules

### Durable incident state
Every incident must retain:
- engine identity
- incident id
- request/run correlation ids
- severity and priority
- expected vs observed behavior
- typed failure
- reproduction evidence
- current hypothesis/root cause
- current lifecycle stage
- implementation lease owner/expiry
- last verified commit/deploy/run
- next executable action
- terminal outcome

Allowed terminal outcomes:
- `FIXED_AND_VERIFIED`
- `PR_CREATED`
- `EXPERIMENT_CREATED`
- `DUPLICATE`
- `NOT_REPRODUCIBLE`
- `BLOCKED_WITH_EXACT_REASON`
- `DEFERRED_WITH_JUSTIFICATION`

### Single implementation lease
Only one agent may modify code for a given incident at a time. Parallelism is allowed for evidence gathering, test design, independent review, and frontier research.

### R0/R1 preemption
Reliability work preempts improvement work. Continuous-improvement agents may continue low-cost evidence discovery, but no experiment may consume deployment/promotion capacity while an actionable R0/R1 incident is awaiting repair.

### Closure standard
`FIXED_AND_VERIFIED` requires production evidence, not merely a passing local test or merged PR.

### Overnight success rule
A cycle is successful only when there is no unresolved actionable R0/R1 incident that the team had the capability to advance. Otherwise the cycle status is `INCOMPLETE_ENGINEERING_RUN`.

## Continuous-improvement technology radar

Every external finding receives one disposition:
- `ADOPT` — engineering adoption candidate; never implicit permission for a Class C production probability change
- `TRIAL` — bounded prototype or sandbox evaluation
- `ASSESS` — evidence gathering required
- `WATCH` — interesting but not actionable yet
- `REJECT` — incompatible, unsafe, weak evidence, or governance conflict
- `DUPLICATE` — existing capability/hypothesis already tracked

Each radar item must include:
- source/provenance
- discovery timestamp
- lane (`AI_FRONTIER` or `SPORTS_INTELLIGENCE`)
- affected WOW subsystem/sport
- problem/opportunity addressed
- expected benefit category: accuracy, calibration, coverage, reliability, latency, cost, observability, operator UX
- evidence quality
- implementation/change class (A/B/C)
- governance constraints
- experiment requirement
- current disposition

## Productivity metrics

The Lead must track at minimum:
- `confirmed_defects_closed`
- `prs_created`
- `prs_merged`
- `deployments_verified`
- `production_acceptance_passes`
- `unfinished_r0_r1`
- `hard_blocked_count`
- `experiments_created`
- `challengers_validated`
- `frontier_findings_assessed`
- `duplicate_findings_filtered`

A monitoring-only cycle with zero closed high-priority incidents is not considered successful unless every remaining high-priority incident has an exact external blocker.
