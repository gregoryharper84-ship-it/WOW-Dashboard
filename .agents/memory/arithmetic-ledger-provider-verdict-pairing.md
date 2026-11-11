# Arithmetic audit ledger: provider/verdict pair constraint

- Date: 2026-11-05
- Typed reason: `PYTHON_ARITHMETIC_AUDIT_LEDGER_WRITE_UNPROVEN` (existing incident; no new runtime code)
- Mechanism: widening provider and verdict constraints independently admitted invalid cross-provider combinations, such as `WOLFRAM_ALPHA` with `PYTHON_ARITHMETIC_OUTPUT_INVALID`.
- Detection point: protected PR #2485 review, before migration application. The original SQL text test checked token presence but not provider/verdict coupling.
- Fix: one paired provider/verdict CHECK in the existing forward-only migration. WOLFRAM_ALPHA legacy verdicts and PYTHON_PRIMARY supported verdicts each remain accepted only for their own provider; PASS works for both.
- Prevention artifact: `artifacts/wow-engine/test_wolfram_arithmetic_audit_schema.py::test_arithmetic_ledger_verdict_check_is_bound_to_matching_provider`; original baseline CHECKs and immutable RLS/ACL unchanged.
- Link: https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2485
- Verification: red source-predicate reproduction confirmed cross-pair admission on old SQL; exact-head CI and gated Supabase migration/readback are pending. Not FIXED_AND_VERIFIED.
