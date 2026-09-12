create table if not exists wow_scout.source_snapshots (
  snapshot_id text primary key,
  provider text not null,
  sport_key text not null,
  capability text not null,
  source_class text not null,
  observed_at timestamptz not null,
  source_status text not null,
  source_code text,
  source_http_status integer,
  payload jsonb,
  payload_hash text,
  prediction_authority boolean not null default false check (prediction_authority = false),
  can_execute boolean not null default false check (can_execute = false),
  created_at timestamptz not null default now(),
  unique(provider, sport_key, capability, payload_hash)
);

create table if not exists wow_scout.provider_entity_map (
  provider text not null,
  sport_key text not null,
  entity_type text not null check (entity_type in ('TEAM','PLAYER','EVENT','VENUE')),
  provider_entity_id text not null,
  provider_entity_key text,
  canonical_entity_id text not null,
  canonical_name text not null,
  aliases text[] not null default '{}',
  verified boolean not null default false,
  verification_source text,
  verified_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key(provider, sport_key, entity_type, provider_entity_id)
);

create table if not exists wow_scout.candidate_source_links (
  candidate_id text not null references wow_scout.candidates(candidate_id) on delete cascade,
  snapshot_id text not null references wow_scout.source_snapshots(snapshot_id) on delete cascade,
  link_status text not null check (link_status in ('LINKED','IDENTITY_UNRESOLVED','IDENTITY_CONFLICT','NOT_RELEVANT')),
  link_reason text,
  provider_entities jsonb not null default '{}'::jsonb,
  linked_at timestamptz not null default now(),
  prediction_authority boolean not null default false check (prediction_authority = false),
  can_execute boolean not null default false check (can_execute = false),
  primary key(candidate_id, snapshot_id)
);

create index if not exists idx_wow_scout_source_snapshots_sport_time
  on wow_scout.source_snapshots(sport_key, observed_at desc);
create index if not exists idx_wow_scout_entity_map_canonical
  on wow_scout.provider_entity_map(sport_key, entity_type, canonical_entity_id);
create index if not exists idx_wow_scout_candidate_source_link_status
  on wow_scout.candidate_source_links(candidate_id, link_status);
