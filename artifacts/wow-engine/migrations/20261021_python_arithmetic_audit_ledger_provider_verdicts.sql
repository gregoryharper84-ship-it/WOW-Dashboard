-- WOW V17 | 2026-10-21 | Follow-up to #1920.
-- The original 20260902 ledger accepts only WOLFRAM_ALPHA, while the active
-- arithmetic verifier is PYTHON_PRIMARY (wolfram_arithmetic_auditor.py is a
-- compatibility facade). Without this provider-specific allowlist, every
-- Python audit INSERT fails and money/settlement remain LEDGER_WRITE_UNPROVEN.
--
-- Deliberately alter ONLY these two CHECK constraints. The immutable
-- prediction FK, receipt hash, append-only update/delete triggers, service-role
-- privileges/RLS, audit_required=true, blocks_model_probability=false and
-- can_execute=false constraints remain unchanged. Legacy receipts are retained.
-- Failed/write-unproven verdicts are NOT legitimate successful audit receipts.

begin;

alter table public.wow_wolfram_arithmetic_audits
    drop constraint if exists wow_wolfram_arithmetic_audits_provider_check;

alter table public.wow_wolfram_arithmetic_audits
    add constraint wow_wolfram_arithmetic_audits_provider_check
    check (provider in ('WOLFRAM_ALPHA', 'PYTHON_PRIMARY'));

alter table public.wow_wolfram_arithmetic_audits
    drop constraint if exists wow_wolfram_arithmetic_audits_verdict_check;

alter table public.wow_wolfram_arithmetic_audits
    add constraint wow_wolfram_arithmetic_audits_verdict_check
    check (
        (
            provider = 'WOLFRAM_ALPHA'
            and verdict in (
                'PASS',
                'WOLFRAM_AUDIT_INPUT_INVALID',
                'WOLFRAM_AUDIT_UNAVAILABLE',
                'WOLFRAM_OUTPUT_INVALID',
                'WOLFRAM_CALCULATION_MISMATCH'
            )
        )
        or
        (
            provider = 'PYTHON_PRIMARY'
            and verdict in (
                'PASS',
                'PYTHON_ARITHMETIC_AUDIT_INPUT_INVALID',
                'PYTHON_ARITHMETIC_OUTPUT_INVALID',
                'COMPUTATION_VERIFICATION_CONFLICT'
            )
        )
    );

comment on table public.wow_wolfram_arithmetic_audits is
    'Immutable prediction-bound deterministic arithmetic audit receipts, '
    'with provider-specific allowed verdicts for legacy WOLFRAM_ALPHA and '
    'active PYTHON_PRIMARY. Not a sporting probability source; '
    'blocks_model_probability=false and can_execute=false.';

commit;
