---
paths:
  - ".github/workflows/**"
  - ".github/scripts/**"
  - ".github/actions/**"
  - "artifacts/wow-engine/v17/agent_identity_policy.py"
  - "supabase/**"
  - "artifacts/wow-engine/**/migrations/**"
---
# Protected release, workflow and data rules

- Investigate trust-root and reviewer-identity requirements before editing a gate. Never bypass or relabel a deterministic governance denial as a retryable infrastructure failure.
- Changing CI is not equivalent to obtaining independent QA; exact-SHA check identity and approved non-author review remain mandatory.
- Preserve RLS, service-role privilege separation, immutable ledgers, minimal credentials and idempotent writes. Test migrations on ephemeral or approved nonproduction environments before production.
- Verify separate GitHub, Render runtime, Supabase schema, model artifact, Action response and GPT editor state.
- Do not force-push, rewrite rescue lineage or modify protected main directly.

## CI failures must retain their original causes

Classify red checks before changing code: `APPLICATION_REGRESSION`, `CI_ENVIRONMENT_GAP`, `DEPENDENCY_VERSION_DRIFT`, `DATABASE_SCHEMA_BOOTSTRAP_GAP`, `MISSING_EXTERNAL_SERVICE_BY_DESIGN`, `PREEXISTING_TEST_FAILURE`, or `UNKNOWN`. Capture the exact failed command, log and tested SHA; a green retry is not evidence that a deterministic failure was corrected. Do not delete assertions, add skip guards solely to hide real regressions, open governance bypasses, or use production credentials in CI. For production-adjacent changes, test the actual release/runtime surface after authorized deployment.
