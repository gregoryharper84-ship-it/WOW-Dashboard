---
name: Prob-ledger live handoff doctrine
description: durable rules for rank eligibility lane split, live finalize ordering, and non-fabrication in the gate-engine probability ledger
---
- **Lane split:** `rank_eligible` reflects only the sporting-model lane; market readiness is a separate flag with typed status (STALE_MARKET / REHYDRATE_REQUIRED).
  **Why:** an absent or drifted market must hold only the money/edge lane — a complete model probability still ranks, and an old-line probability must never attach to a new line.
- **Ordering:** any ledger finalize/refresh must run in the live per-row path after specialist model gates but before classifier/gatekeeper. A refresh that only runs while building the report makes rows look eligible in output while their live terminal outcome was decided against an incomplete ledger.
- **Non-fabrication:** ingestion adapters copy Stage-2 fields (incl. upper_bound, model_timestamp, calibration_method) only from real model/caller outputs; absence stays a typed missing field. Never synthesize a bound, timestamp, or provenance string from other fields.
- **Version enforcement:** caller-supplied ledger payloads are version-checked before any adapter reads them; unsupported versions are quarantined with a typed blocker, or their values silently seed the canonical ledger via the adapter's fallback picks.
