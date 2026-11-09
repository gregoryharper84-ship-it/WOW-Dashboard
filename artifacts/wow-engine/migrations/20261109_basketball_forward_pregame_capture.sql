-- REVIEW_ONLY: not applied by this change. Requires owner authorization (Class B).
-- Append-only forward pregame capture for NBA/WNBA. Retrospective replay rows stay in
-- wow_*_pregame_feature_rows and must never be counted as pre-lock corpus evidence.
create table if not exists public.wow_basketball_forward_pregame_capture (
  capture_id uuid primary key default gen_random_uuid(),
  sport text not null check (sport in ('NBA','WNBA')),
  official_event_id text not null,
  event_start_time timestamptz not null,
  event_status text not null check (event_status in ('scheduled','pre','pregame','not_started')),
  captured_at timestamptz not null default clock_timestamp(),
  source_snapshot_id text not null,
  source_snapshot_timestamp timestamptz not null,
  source_payload_sha256 text not null check (source_payload_sha256 ~ '^[0-9a-f]{64}$'),
  feature_schema_version text not null,
  feature_payload jsonb not null,
  feature_snapshot_sha256 text not null check (feature_snapshot_sha256 ~ '^[0-9a-f]{64}$'),
  history_through_date date not null,
  supersedes_feature_snapshot_sha256 text,
  capture_version text not null,
  can_execute boolean not null default false check (can_execute = false),
  probability_publishable boolean not null default false check (probability_publishable = false),
  unique (sport, official_event_id, feature_snapshot_sha256),
  check (source_snapshot_timestamp <= captured_at)
);

create or replace function public.wow_basketball_forward_capture_guard() returns trigger
language plpgsql as $$
begin
  if tg_op <> 'INSERT' then
    raise exception 'BASKETBALL_FORWARD_CAPTURE_IMMUTABLE';
  end if;
  new.captured_at := clock_timestamp();            -- server clock only; caller value ignored
  if new.captured_at >= new.event_start_time then
    raise exception 'CAPTURE_AT_OR_AFTER_EVENT_START';
  end if;
  return new;
end $$;

drop trigger if exists wow_basketball_forward_capture_insert on public.wow_basketball_forward_pregame_capture;
create trigger wow_basketball_forward_capture_insert before insert on public.wow_basketball_forward_pregame_capture
  for each row execute function public.wow_basketball_forward_capture_guard();
drop trigger if exists wow_basketball_forward_capture_immutable on public.wow_basketball_forward_pregame_capture;
create trigger wow_basketball_forward_capture_immutable before update or delete on public.wow_basketball_forward_pregame_capture
  for each row execute function public.wow_basketball_forward_capture_guard();

alter table public.wow_basketball_forward_pregame_capture enable row level security;
