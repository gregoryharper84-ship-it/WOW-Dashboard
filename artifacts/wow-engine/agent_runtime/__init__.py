"""WOW Agent Runtime V1 — Phase 1: durable run ledger, job/queue state
machine, idempotency, deterministic ceiling reducer, and reconciliation.

can_execute = False everywhere in this package. Nothing here places, routes,
modifies, or cancels a live wager or order; see agent_runtime_schema.sql's
hard CHECK constraints for the same invariant enforced at the database layer.
"""
from __future__ import annotations

can_execute = False
DRY_RUN_ONLY = True
