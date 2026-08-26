---
name: WOW Daily lifecycle boundary
description: Durable rules for safe asynchronous canonical daily scans.
---

The canonical daily manifest is the authoritative, immutable contract for a run. Once it is created, every executor argument must come from that persisted row—not a retry payload.

**Why:** A concurrent retry can win the execution claim after another caller has created the manifest. Using request-local values at that point creates a run whose recorded and executed inputs disagree.

**How to apply:** Require a client run identity, atomically create/reuse the manifest, then claim it once with a durable owner lease and executor PID. Launch a detached executor rather than a serving-worker child. Every executor mutation (progress, rows, finalization, error terminalization) must require that same owner and an unexpired lease; heartbeats must never revive an expired lease. On deadline/lease expiry, terminalize first, then TERM the executor's isolated process group and KILL it after a short grace period; the spawning worker must wait in a daemon child-reap thread so interrupted scans do not accumulate zombies. Leave discovery/reconciliation unset when no truthful result exists.