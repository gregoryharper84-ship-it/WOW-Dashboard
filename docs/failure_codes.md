# WOW V17 — Failure-code registry (incremental)

This versioned document registers newly introduced typed failure codes. It is **not yet a complete backfill** of historical codes; existing runtime/Action contracts remain authoritative until the registry backfill is reviewed. No code here grants probability, model, source-licensing, deployment, or terminal authority.

| Code | Owning module / gate | Exact condition | Disposition |
| --- | --- | --- | --- |
| `SOURCE_SNAPSHOT_ID_CONFLICT` | `artifacts/wow-engine/v17/research_chronology_audit.py` — offline candidate-corpus chronology preflight | The same nonblank source_snapshot_id is associated with a different sport, SHA-256 source payload digest, source publication instant, or source capture instant within one audited corpus. | `RESEARCH_AUDIT_HOLD`; retain row evidence and blockers; do not promote to fitted training or calibrated probability |

Rules: existing non-conflicting duplicate snapshot IDs may be reused only with identical bound source identity and valid per-row chronology. A valid digest authenticates local bytes, **not** historical publication, training rights or an official event identity. Retain `V17_TERMINAL_REDUCER` as sole terminal authority; `can_execute=false`.

Future changes introducing new typed codes should update this registry in the same PR. Backfill of all legacy/current failure codes requires a separate reconciled audit rather than silently claiming this starter file is exhaustive.
