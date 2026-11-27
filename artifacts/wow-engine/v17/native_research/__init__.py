"""WOW Native Research Engine (NRE): evidence-only research for specialist blockers.

One engine, modular capabilities (no separate services, no new controller):

- ``contract``  blocker -> deterministic research task, dedup and scheduling
- ``fetcher``   native crawler: allowlist, robots, rate limits, budgets, ETag
- ``discovery`` registered sources, GitHub dataset search, Crossref, local index
- ``extract``   document and dataset extraction
- ``rights``    source-rights intelligence (never authorizes production use)
- ``verify``    freshness and cross-source conflict checks
- ``store``     native knowledge index (Supabase PostgreSQL full-text search)
- ``learning``  deterministic research-strategy performance
- ``runner``    end-to-end orchestration, Swarm receipt and factory handoff

Research never fits, calibrates, certifies, ranks, publishes or executes.
Firecrawl and Tavily are not used here; they stay optional and disabled.
"""
from __future__ import annotations

SCHEMA_VERSION = "WOW_NRE_V1"
ENGINE_VERSION = "WOW_NATIVE_RESEARCH_ENGINE_V1"
RESEARCH_WORKER_ID = "wow.source-provenance-researcher"
AUTHORITY_CEILING = "RESEARCH_INTEREST"
CAN_EXECUTE = False

# Flags carried by every NRE record and receipt.
INERT = {
    "evidence_only": True,
    "prediction_authority": False,
    "production_use_authorized": False,
    "can_execute": CAN_EXECUTE,
}
