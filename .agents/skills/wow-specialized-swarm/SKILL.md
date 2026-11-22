---
name: wow-specialized-swarm
description: Plans and coordinates bounded, read-only WOW V17 sports matchup and player-prop research using Antigravity /swarm, canonical Scout teams, source provenance, and adversarial verification. Use for WOW matchup discovery, sports research swarms, or multi-agent pregame evidence review.
---

# WOW Specialized Swarm — research-only

This skill is a **research coordination adapter**, not a second scoring model, engineering dispatcher, release gate, or background service. Use Antigravity's native /swarm for worker coordination and its existing shared board. Do not create a new persistent worker queue, approval controller, or betting endpoint.

## Prepare

1. Read artifacts/wow-engine/v17/sport_scout_registry.py, sport_research_brief.py, matchup_discovery.py, and sport_scout_enrichment.py. Those are the existing authority for Scout roles, evidence requirements and handoff.
2. Obtain a verified canonical sport_key, official_event_id, and pregame UTC commence_time from existing event discovery. No inferred IDs, schedules, team identities, or fabricated odds. If identity or time is unresolved, report the evidence gap and stop the research **for that event** without blocking unrelated events.
3. Run python artifacts/wow-engine/tools/wow_specialized_swarm.py --help; then use --sport <sport_key> --event-id <official_id> --starts <UTC ISO8601> --format json to inspect the bounded manifest. Use --format prompt to obtain a native /swarm prompt, and start that prompt in the Antigravity App or CLI. Do not claim the Python script itself launched a Swarm.

## Worker ownership and board

Keep workers to at most **two simultaneous active research workers** by default (one allowed for quota pressure; three maximum only if explicitly needed). Use separate research questions; do not let different workers edit the same file. Map native Swarm board nicknames to existing worker contracts:

- @source → wow.source-provenance-researcher: official event identity, allowed data usage and source lineage.
- @personnel → wow.participant-status-researcher: lineups, injuries, minutes, usage and starter confirmation.
- @matchup → wow.matchup-context-researcher: pace, schemes, rest, travel, fatigue and sport-specific game script.
- @history → wow.history-comparables-researcher: opponent-adjusted recent form, sample size and time-correct comparables.
- @market → wow.market-settlement-researcher: exact pregame quotes, line identity, book disagreement and settlement context, **without creating a fitted probability**.

Wave after wave, the independent @verifier reads every submission and challenges stale timestamps, wrong-game joins, missing/contradictory evidence, source rights, and unsupported authority claims. Keep the monitor asleep unless progress genuinely stalls. If quota returns 429, pause the worker rather than repeatedly spending quota; do unrelated deterministic work instead.

The shared board is the native Antigravity Swarm board. Do not persist model inputs, logs, credentials or proprietary provider data in a public GitHub issue/PR or ChatGPT transcript.

## Research submission contract

Every submitted fact must preserve canonical event ID, feature, source ID/class, observed value, UTC captured-at timestamp, and explicit research-rights state (CLEARED_FOR_RESEARCH or REVIEW_REQUIRED). Record missing_evidence and conflicts, including a nonempty reason when no sources were found. Run the adapter's validate_submission function in trusted code before accepting any research record as **structurally well-formed**. Structural validation does not independently verify truth, authorization, source rights or prediction readiness.

No Scout/LLM/Research agent may manufacture, fit, adjust, or qualify a sporting probability, use odds as a fallback probability, silently resolve conflicting sources, claim professional independence, certify its own code, approve its own protected change, place bets/orders, or invoke production model endpoints. A rejected report is a **research hold**; it is not MODEL_UNAVAILABLE and is not a release bypass.

## Downstream and closeout

Send sourced observations through **existing** sport_scout_enrichment / governed research handoff. The certified fitted specialist, if available, may independently consume eligible time-correct features and produce probability through the existing V17 path. The only terminal authority is V17_TERMINAL_REDUCER.

Report: events researched, each worker's received/verified/missing/conflicted evidence, unresolved blockers, independent verifier findings, total active worker peak, and whether any actual existing pipeline receipt was observed. **No evidence = no completion claim**. Test with python -m pytest artifacts/wow-engine/tests/test_wow_specialized_swarm.py -q. Do not claim CI, deployed SHA, Supabase persistence, improved coverage, or production verification unless separately observed.

Immutable controls: custom_gpt_identity=WOW_BETTING_ENGINE; runtime_generation=V17_ACTIVE; terminal_authority=V17_TERMINAL_REDUCER; can_execute=false; DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true.
