-- Rollback for 20261124_v17_source_capture_attestations.sql (owner approval required).
-- Removing the table makes every attestation read fail, so hydration returns to
-- timestamp_attestation=NOT_ESTABLISHED and rows hold (fail closed).
drop trigger if exists trg_wow_v17_source_capture_attestations_server_received_at
  on public.wow_v17_source_capture_attestations;
drop function if exists public.wow_v17_source_capture_attestations_server_received_at();
drop table if exists public.wow_v17_source_capture_attestations;
