-- WOW v16 Clean Core — immutable pregame prediction write hardening.
--
-- A successful governed prediction must already be immutable when it is
-- admitted to any qualified/user-visible lane. Do not wait until event start.
-- Existing rows are backfilled to their original insert time so no historical
-- prediction remains editable merely because locked_at was previously nullable.
--
-- Historical rows may already be past event_start_time, so the existing UPDATE
-- immutability trigger would correctly reject the one-time backfill. Disable
-- only that UPDATE trigger inside this transaction, perform only the locked_at
-- backfill, re-enable the trigger before changing constraints, and commit only
-- after immediate immutability is restored. Any error rolls the whole sequence
-- back, including trigger state.

begin;

alter table public.wow_predictions
    disable trigger trg_wow_block_post_event_edit;

update public.wow_predictions
set locked_at = coalesce(locked_at, created_at, now())
where locked_at is null;

alter table public.wow_predictions
    enable trigger trg_wow_block_post_event_edit;

alter table public.wow_predictions
    alter column locked_at set default now();

alter table public.wow_predictions
    alter column locked_at set not null;

comment on column public.wow_predictions.locked_at is
    'Immutable pregame ledger lock. Every prediction is locked at insert; qualified publication requires a persisted prediction row and cannot precede this write.';

commit;
