-- Align the legacy engineering backlog constraints with the already-governed
-- Continuous Engineering Auditor contract.
--
-- Class B persistence-only repair. No sporting probability behavior, model
-- artifact, calibration, terminal sporting authority, or execution permission
-- is changed.

alter table public.wow_engineering_backlog
    drop constraint if exists wow_engineering_backlog_source_check;

alter table public.wow_engineering_backlog
    add constraint wow_engineering_backlog_source_check
        check (source in ('engineering', 'pm', 'ENGINEERING_AUDITOR'));

alter table public.wow_engineering_backlog
    drop constraint if exists wow_engineering_backlog_priority_check;

alter table public.wow_engineering_backlog
    add constraint wow_engineering_backlog_priority_check
        check (priority in ('P0', 'P1', 'P2', 'P3', 'P4'));
