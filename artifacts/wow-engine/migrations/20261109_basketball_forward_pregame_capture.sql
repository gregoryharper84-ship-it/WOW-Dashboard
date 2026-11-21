-- REVIEW_ONLY: not applied by this change. Requires owner authorization (Class B).
-- Append-only forward pregame capture for NBA/WNBA. Retrospective replay rows stay in
-- wow_*_pregame_feature_rows and must never be counted as pre-lock corpus evidence.
-- The database re-verifies, from the PERSISTED raw bytes, the hashes and the event start that the
-- writer claims, so a caller cannot inflate the start time or manufacture a source digest.
create table if not exists public.wow_basketball_forward_pregame_capture (
  capture_id uuid primary key default gen_random_uuid(),
  sport text not null check (sport in ('NBA','WNBA')),
  official_event_id text not null,
  event_start_time timestamptz not null,
  event_status text not null check (event_status in ('scheduled','pre','pregame','not_started')),
  captured_at timestamptz not null default clock_timestamp(),
  source_authority text not null check (source_authority in ('BALLDONTLIE_GAME_V1')),
  source_snapshot_id text not null,
  source_snapshot_timestamp timestamptz not null,
  source_payload_text text not null,                       -- raw source bytes as received (UTF-8)
  source_payload_sha256 text not null check (source_payload_sha256 ~ '^[0-9a-f]{64}$'),
  source_event_identity_verified boolean not null check (source_event_identity_verified = true),
  feature_schema_version text not null,
  feature_payload jsonb not null,
  feature_payload_canonical text not null,                 -- exact canonical text that was hashed
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
declare src jsonb; src_game jsonb; src_start timestamptz;
begin
  if tg_op <> 'INSERT' then
    raise exception 'BASKETBALL_FORWARD_CAPTURE_IMMUTABLE';
  end if;
  new.captured_at := clock_timestamp();                    -- server clock only; caller value ignored
  -- hashes must match the bytes actually persisted
  if encode(sha256(convert_to(new.source_payload_text, 'UTF8')), 'hex') <> new.source_payload_sha256 then
    raise exception 'CAPTURE_SOURCE_PAYLOAD_HASH_MISMATCH';
  end if;
  if encode(sha256(convert_to(new.feature_payload_canonical, 'UTF8')), 'hex') <> new.feature_snapshot_sha256
     or new.feature_payload_canonical::jsonb <> new.feature_payload then
    raise exception 'CAPTURE_FEATURE_HASH_MISMATCH';
  end if;
  -- the persisted source, not the caller, decides identity and start time
  begin
    src := new.source_payload_text::jsonb;
    src_game := coalesce(src->'data', src);
    src_start := (src_game->>'datetime')::timestamptz;
  exception when others then
    raise exception 'CAPTURE_SOURCE_PAYLOAD_MALFORMED';
  end;
  if (src_game->>'id') is distinct from new.official_event_id then
    raise exception 'CAPTURE_SOURCE_EVENT_MISMATCH_OFFICIAL_EVENT_ID';
  end if;
  if src_start is null or src_start <> new.event_start_time then
    raise exception 'CAPTURE_SOURCE_EVENT_MISMATCH_EVENT_START_TIME';
  end if;
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
revoke all on table public.wow_basketball_forward_pregame_capture from public, anon, authenticated;
