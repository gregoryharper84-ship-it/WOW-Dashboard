# WOW Specialized Swarm — v1 experimental adapter

**Status:** research-only design and executable manifest/validation prototype. Not wired to production, a fitted model, or Antigravity API; the native Antigravity /swarm command must be invoked in the Antigravity App/CLI. No new durable controller, database migration, credential, vendor or paid service.

## Why this shape

WOW already has sport_scout_registry.py sport teams, sport_research_brief.py five research workers, mandatory matchup_discovery.py and sport_scout_enrichment.py handoff. Adding another orchestration service would duplicate the existing durable multi-agent path. The adapter reads these canonical contracts and produces a bounded manifest plus paste-ready /swarm prompt. Antigravity owns parallel execution and its shared board; V17 owns data, fitted scoring, governance and publication.

## Launch from a repo checkout

~~~sh
python artifacts/wow-engine/tools/wow_specialized_swarm.py \
  --sport basketball_nba \
  --event-id VERIFIED_OFFICIAL_EVENT_ID \
  --starts 2026-12-01T00:00:00Z \
  --max-parallel 2 --format json
python artifacts/wow-engine/tools/wow_specialized_swarm.py \
  --sport basketball_nba \
  --event-id VERIFIED_OFFICIAL_EVENT_ID \
  --starts 2026-12-01T00:00:00Z \
  --max-parallel 2 --format prompt
python -m pytest artifacts/wow-engine/tests/test_wow_specialized_swarm.py -q
~~~

Paste the second command's output into Antigravity App/CLI. Or invoke the repo skill /wow-specialized-swarm and instruct it to build the manifest and start native /swarm. The skill is discovered from .agents/skills/wow-specialized-swarm/SKILL.md when that branch is in your Antigravity workspace. It does not configure a hosted plugin, run unattended, or submit to live WOW endpoints.

## Ownership and limits

| Role | Existing ID | Allowed work |
|---|---|---|
| Source | wow.source-provenance-researcher | Origin, time and rights |
| Personnel | wow.participant-status-researcher | Injuries, lineup, workload |
| Matchup | wow.matchup-context-researcher | Opponent-specific game scripts |
| History | wow.history-comparables-researcher | Time-correct comparable form |
| Market | wow.market-settlement-researcher | Exact quotes, movement and settlement |
| Verifier | Native independent Swarm verifier | Adversarial evidence check, not certified probability/release acceptance |

Default peak simultaneous research workers: **2**. The script generates [2,2,1] waves, not five concurrent paid agents. One native verifier examines each wave. The built-in monitor observes stalls. No non-transient 429 retry loop.

Supported profiles come from matchup_discovery.profile_for; NFL, NCAAF, NBA, WNBA, NCAAB, MLB and NHL have sport-specific Scout teams already. Soccer, tennis, combat, golf and cricket can receive profile-specific matchup checks but currently fall back to **generic Scout research** in sport_scout_registry and must not be described as formally certified sport specialists.

## Acceptance boundary

1. Preflight rejects unknown sport, ambiguous ID, naive timestamp, post-start pregame run, or non-false execution flag.
2. Five canonical researcher briefs generated with stable local event key, no copied controller or probability owner.
3. Reports reject wrong event/worker, unsourced claims, future/non-pregame evidence, missing source rights, embedded fitted probabilities, and any asserted protected/execution authority.
4. valid_report=true only means **structural screening**. The native verifier and V17 source policy must inspect evidence separately. Research-only output is ineligible to rank/qualify/publish.
5. Existing qualified pick coverage, independent release checks and production fidelity must be verified separately. Default acceptance delta for this isolated adapter: **zero production changes and zero qualified picks**.

## Production follow-through, only after independent review

Integrate this adapter **as an optional formatter** in the existing Multi-Scout handoff—not a new queue—and first replay one real event's read-only source evidence end to end. Verify an actual persisted handoff only after source-rights review and exact-event/time reconciliation. Protected or probability-related changes must follow exact-head independent QA/Release approval, original V17 fitted-specialist routing, and production readback. No automatic promotion or trading at any point.
