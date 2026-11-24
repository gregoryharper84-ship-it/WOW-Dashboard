-- PROTECTED Class B | Incident #1950 | migration candidate, NOT APPLIED until owner approval.
-- Server-clock attestation for pregame source captures (NBA, NCAAB, ...).
-- A row proves that a raw source capture hash and its derived feature payload
-- hash were durably committed at `received_at`, a database-owned wall clock
-- that callers cannot supply, backdate or later modify. Rows can only be
-- inserted before the event starts. This is input-timing evidence only: it
-- never certifies a model, sets a probability or authorizes publication.
-- Pattern follows 20261103_nba_event_shadow_prediction_receipts.sql.
create table if not exists public.wow_v17_source_capture_attestations (
  attestation_id uuid primary key default gen_random_uuid(),
  received_at timestamptz not null,
  sport text not null check (sport ~ '^[A-Z]{2,8}$'),
  official_event_id text not null check (length(btrim(official_event_id)) between 1 and 128),
  event_start_time_utc timestamptz not null,
  source_snapshot_id text not null check (source_snapshot_id ~ '^sha256:[0-9a-f]{64}$'),
  source_raw_sha256 text not null check (source_raw_sha256 ~ '^[0-9a-f]{64}$'),
  feature_payload_sha256 text not null check (feature_payload_sha256 ~ '^[0-9a-f]{64}$'),
  attestation_policy text not null default 'SUPABASE_SERVER_CLOCK_RECEIPT_V1'
    check (attestation_policy = 'SUPABASE_SERVER_CLOCK_RECEIPT_V1'),
  can_execute boolean not null default false check (can_execute = false),
  constraint source_capture_snapshot_matches_raw
    check (source_snapshot_id = 'sha256:' || source_raw_sha256),
  constraint source_capture_pregame_only check (received_at < event_start_time_utc),
  constraint source_capture_unique_identity unique (
    sport, official_event_id, source_snapshot_id, feature_payload_sha256
  )
);

alter table public.wow_v17_source_capture_attestations
  alter column received_at drop default;

create or replace function public.wow_v17_source_capture_attestations_server_received_at()
returns trigger
language plpgsql
set search_path to ''
as $function$
begin
  -- Caller-supplied values are never authoritative. BEFORE INSERT runs ahead
  -- of the pregame check constraint, so a post-start insert is rejected.
  new.received_at := pg_catalog.clock_timestamp();
  return new;
end;
$function$;

drop trigger if exists trg_wow_v17_source_capture_attestations_server_received_at
  on public.wow_v17_source_capture_attestations;
create trigger trg_wow_v17_source_capture_attestations_server_received_at
  before insert on public.wow_v17_source_capture_attestations
  for each row execute function public.wow_v17_source_capture_attestations_server_received_at();

alter table public.wow_v17_source_capture_attestations enable row level security;
revoke all on table public.wow_v17_source_capture_attestations from public, anon, authenticated;
revoke update, delete, truncate, references, trigger
  on table public.wow_v17_source_capture_attestations from service_role;
grant select, insert on table public.wow_v17_source_capture_attestations to service_role;
