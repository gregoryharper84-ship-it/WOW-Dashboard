-- Repair: the V17 arithmetic auditor is local deterministic Python
-- (provider PYTHON_PRIMARY). The original ledger CHECKs only admitted
-- WOLFRAM_ALPHA and WOLFRAM_* verdicts, so every Python receipt insert failed
-- and surfaced as PYTHON_ARITHMETIC_AUDIT_LEDGER_WRITE_UNPROVEN.
-- Additive widening only: legacy Wolfram rows remain valid; immutability,
-- RLS, grants, blocks_model_probability=false and can_execute=false unchanged.

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
        (provider = 'WOLFRAM_ALPHA' and verdict in (
            'PASS',
            'WOLFRAM_AUDIT_INPUT_INVALID',
            'WOLFRAM_AUDIT_UNAVAILABLE',
            'WOLFRAM_OUTPUT_INVALID',
            'WOLFRAM_CALCULATION_MISMATCH'
        ))
        or
        (provider = 'PYTHON_PRIMARY' and verdict in (
            'PASS',
            'PYTHON_ARITHMETIC_AUDIT_INPUT_INVALID',
            'PYTHON_ARITHMETIC_OUTPUT_INVALID',
            'COMPUTATION_VERIFICATION_CONFLICT'
        ))
    );
