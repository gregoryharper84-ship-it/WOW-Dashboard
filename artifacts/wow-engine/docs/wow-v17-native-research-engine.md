# WOW V17 — Native Research Engine (NRE)

Owner decision 2026-11-26: free-first, in-house. One engine with modular capabilities, not a new service or
controller. Research only gathers evidence. It never fits, calibrates, certifies, ranks, publishes or executes.

## Flow

```
specialist blocker (assess() + lane receipt)
  -> research task (deterministic identity, dedup, writer lease)
  -> discovery: registered sources -> local index -> GitHub dataset search  (Crossref for literature)
  -> native crawler: approved hosts, robots, per-host rate limit, budgets, ETag / Last-Modified, hashing
  -> extraction -> freshness + cross-source check -> source-rights intelligence
  -> Supabase knowledge index (immutable attempts, documents, rights, findings; full-text search)
  -> Swarm receipt (network-free)  +  factory handoff (no fit)  +  blocker progress
```

## Capabilities (`v17/native_research/`)

| Module | Capability |
|---|---|
| `contract` | Blocker -> task contract, outcomes, progress stages, reschedule rules |
| `fetcher` | WOW Native Crawler |
| `discovery` | Source discovery and search front-ends (GitHub, Crossref, README-published links) |
| `extract` | Document extraction (HTML/markdown/CSV/JSON results tables, league file selection) |
| `verify` | Freshness and cross-source conflict checks |
| `rights` | Source Rights Intelligence |
| `store` | Research Knowledge Base (Supabase) and in-memory twin |
| `learning` | Research Learning: deterministic strategy and source reliability scores |
| `runner` | Orchestration, Swarm receipt, factory handoff, soccer maintenance hook |

## Invariants

- `can_execute=false`, `prediction_authority=false`, `evidence_only=true` on every record.
- `production_use_authorized` cannot be true in the NRE tables (check constraint). Only independent review
  can authorize a source; research only prepares the evidence.
- A software licence is never read as a data licence. A compilation of third-party sources (ESPN, FotMob,
  Soccerway, ...) is marked `unresolved_upstream_rights`.
- Document bodies are stored only when the source's terms permit storage (table check constraint).
- Research never clears the originating blocker; `remaining_blockers` always contains it.
- Unverified, stale or conflicting evidence cannot be handed off (table check constraint + factory receiver).
- A failed search or probe is `ACQUISITION_FAILED`, never a negative result.
- Unchanged blockers are not re-researched without new evidence, a freshness trigger or an authorized retry
  (1h/4h/16h backoff, 3 transient retries per day).
- Spend is $0: only free public APIs and public files. Firecrawl and Tavily stay optional and disabled; the
  NRE never calls them.

## Runtime

- Runs inside the existing soccer maintenance route (`/internal/v17/soccer-model-maintenance`, dispatched by
  the first-six maintenance workflow) after forward evidence, for leagues blocked by
  `SOCCER_SOURCE_RESULTS_STALE`. Bounded to 40 requests and 60 seconds; isolated (never raises, never changes
  the maintenance status). The response carries a `native_research` receipt.
- Swarm: `python tools/wow_specialized_swarm.py --candidate C.json --research-receipt R.json` (local files only).
- Factory: `v17.certified_model_factory.receive_research_handoff()` returns QUEUED / HELD / REJECTED and never
  fits, replays, calibrates or certifies.

## Persistence

Migration `migrations/20261126_v17_native_research_engine.sql` (rollback alongside): `wow_nre_research_tasks`,
`wow_nre_research_attempts`, `wow_nre_documents`, `wow_nre_source_rights`, `wow_nre_findings`,
`wow_nre_search()` (full-text + structured filters) and `wow_nre_claim_task()` (single-writer lease).
RLS on; `service_role` only. Until the migration is applied, the hook reports `NRE_DB_42P01` per task.

## Next capabilities

RSS and sitemap acquisition, GDELT and Crossref for current-season sports, NFL injury-report and NBA lineup
source discovery, and NHL goalie/injury evidence (Incident #1952), ordered by certification readiness.
