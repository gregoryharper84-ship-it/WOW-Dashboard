---
name: WOW Daily lifecycle boundary
description: Durable rules for safe asynchronous canonical daily scans.
---

The canonical daily manifest is the authoritative, immutable contract for a run. Once it is created, every executor argument must come from that persisted row—not a retry payload.

**Why:** A concurrent retry can win the execution claim after another caller has created the manifest. Using request-local values at that point creates a run whose recorded and executed inputs disagree.

**How to apply:** Require a client run identity, atomically create/reuse the manifest, then claim it once with a durable owner lease and executor PID. Launch a detached executor rather than a serving-worker child. Every executor mutation (progress, rows, finalization, error terminalization) must require that same owner and an unexpired lease; heartbeats must never revive an expired lease. On deadline/lease expiry, terminalize first, then TERM the executor's isolated process group and KILL it after a short grace period; the spawning worker must wait in a daemon child-reap thread so interrupted scans do not accumulate zombies. Leave discovery/reconciliation unset when no truthful result exists.

Terminal outcome containment is the sole publication boundary: raw primary and fallback lane cards must not reach the manifest before their canonical IDs, exact-one accounting, prerequisite state, and non-execution flags are normalized.

**Why:** Manifest row inserts are immutable per run/selection. Persisting a raw fallback card first means a later scrubbed or duplicate-contained row cannot replace it, allowing stale probabilities or approval states to survive.

**How to apply:** At finalization, emit exactly one non-executable terminal row per discovered ID. Replace duplicate, missing, unknown, ID-less, or mandatory-prerequisite-failed output with a non-authoritative data-contract row, clear probability/edge/model/calibration publication fields, and mark reconciliation non-authoritative even though the contained board is count-complete.

Prerequisite containment must distinguish an omitted optional field from an explicitly declared missing prerequisite.

**Why:** Treating every absent status/container as an empty failed value converts ordinary scanner cards into data-contract failures and makes a healthy board unreconcilable.

**How to apply:** Evaluate only prerequisite keys present on the emitted card. A present key with an empty, unavailable, or failed value is fail-closed; a key omitted by the producing lane is not evidence of a required-prerequisite failure.