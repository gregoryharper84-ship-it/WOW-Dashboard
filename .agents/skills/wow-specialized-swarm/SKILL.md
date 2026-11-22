---
name: wow-specialized-swarm
description: Run WOW's own deterministic, zero-token, bounded local research workers against an existing canonical sports event snapshot. No Antigravity, hosted LLM or betting execution.
---

# WOW Native Swarm research skill

This optional repo instruction describes how to run WOW's **own** Python research coordinator, `artifacts/wow-engine/tools/wow_specialized_swarm.py`. It is **not** an Antigravity `/swarm` command, a cloud agent request or a second durable execution queue.

- Read `artifacts/wow-engine/docs/wow_specialized_swarm.md` and the existing `v17/sport_scout_registry.py`, `v17/sport_research_brief.py`, `v17/matchup_discovery.py` and `v17/scout_source_policy.py` before changing the worker contract.
- Extract a verified canonical pregame candidate from the existing WOW research path. Do not guess event IDs, invent timestamps or treat a source as licensed without independent review.
- Execute `python artifacts/wow-engine/tools/wow_specialized_swarm.py --candidate /path/to/candidate.json --max-parallel 2 --output /tmp/wow-native-swarm-research-receipt.json` in the repository checkout. It uses local Python only and no paid model provider.
- Inspect all five `worker_reports`, `wave_reviews`, `matchup_discovery` and consolidated `handoff` status. A source-screened item is not a certified betting forecast, and `REVIEW_REQUIRED` facts are not eligible to flow through the handoff.
- Fail closed on unknown event/source, incomplete lineage, source conflicts, stale data, unsupported worker identity, probabilistic model authority, or quota-driven attempts to move work to paid providers. Preserve original fitted specialist ownership and V17 terminal reduction.
- For code changes, reproduce RED, fix the cause, keep regression tests and obtain independent exact-head review. Do not merge to protected main or activate a production call through this skill.

Policy: `custom_gpt_identity=WOW_BETTING_ENGINE`; `runtime_generation=V17_ACTIVE`; `terminal_authority=V17_TERMINAL_REDUCER`; `can_execute=false`; `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`.