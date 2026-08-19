---
name: WOW Daily lifecycle boundary
description: Durable rules for safe asynchronous canonical daily scans.
---

The canonical daily manifest is the authoritative, immutable contract for a run. Once it is created, every worker argument must come from that persisted row—not a retry payload.

**Why:** A concurrent retry can win the execution claim after another caller has created the manifest. Using request-local values at that point creates a run whose recorded and executed inputs disagree.

**How to apply:** Require a client run identity, atomically create or reuse the manifest, then claim exactly once. Keep HTTP acknowledgement work bounded; make the manifest schema ready before serving requests. Terminalize on normal completion, caught exception, whole-run deadline, unexpected child exit, and expiry reaping. Treat failed row persistence as a terminal failure rather than a successful completed manifest.