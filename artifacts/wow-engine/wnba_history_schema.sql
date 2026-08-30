-- WOW WNBA raw player-game history V1.
--
-- Bronze/source-of-record data plane for first-party WNBA Stats LeagueGameLog
-- rows. This table is intentionally NOT a training-ready feature store. The
-- official game-log feed does not prove starter/role state, so every row enters
-- with role_evidence_status=UNRESOLVED and training materialization blocked.
-- No probability/model/certification state is stored here.

create table if not exists public.wow_wnba_player_game_logs (
    source_row_id uuid primary key default gen_random_uuid(),
    season integer not null,
    season_type text not null,
    game_id text not null,
    game_date date not null,
    player_id text not null,
    player_name text not null,
    team_abbreviation text not null,
    matchup text not null,
    minutes numeric,
    pts integer,
    reb integer,
    ast integer,
    fg3m integer,
    source_identity text not null default 'WNBA_STATS_LEAGUE_GAME_LOG',
    source_retrieved_at timestamptz not null,
    source_payload_hash text not null,
    raw_row jsonb not null,
    role_evidence_status text not null default 'UNRESOLVED',
    training_materialization_status text not null default 'BLOCKED_ROLE_EVIDENCE',
    ingested_at timestamptz not null default now(),
    can_execute boolean not null default false,
    constraint wow_wnba_player_game_logs_season check (season >= 1997),
    constraint wow_wnba_player_game_logs_season_type check (season_type in ('Regular Season','Playoffs')),
    constraint wow_wnba_player_game_logs_minutes check (minutes is null or (minutes >= 0 and minutes <= 60)),
    constraint wow_wnba_player_game_logs_pts check (pts is null or pts >= 0),
    constraint wow_wnba_player_game_logs_reb check (reb is null or reb >= 0),
    constraint wow_wnba_player_game_logs_ast check (ast is null or ast >= 0),
    constraint wow_wnba_player_game_logs_fg3m check (fg3m is null or fg3m >= 0),
    constraint wow_wnba_player_game_logs_source_identity check (source_identity = 'WNBA_STATS_LEAGUE_GAME_LOG'),
    constraint wow_wnba_player_game_logs_raw_object check (jsonb_typeof(raw_row) = 'object'),
    constraint wow_wnba_player_game_logs_role_status check (role_evidence_status in ('UNRESOLVED','RESOLVED')),
    constraint wow_wnba_player_game_logs_materialization_status check (
        training_materialization_status in ('BLOCKED_ROLE_EVIDENCE','READY_FOR_MATERIALIZATION','MATERIALIZED','REJECTED')
    ),
    constraint wow_wnba_player_game_logs_never_execute check (can_execute = false),
    constraint wow_wnba_player_game_logs_source_unique unique (
        season, season_type, game_id, player_id, source_identity
    )
);

alter table public.wow_wnba_player_game_logs enable row level security;

create index if not exists wow_wnba_player_game_logs_player_date_idx
    on public.wow_wnba_player_game_logs (player_id, game_date desc);

create index if not exists wow_wnba_player_game_logs_materialization_idx
    on public.wow_wnba_player_game_logs (role_evidence_status, training_materialization_status, game_date desc);
