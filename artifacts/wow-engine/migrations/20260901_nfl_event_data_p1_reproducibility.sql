-- P1 reproducibility hardening.
-- A downloaded hash does not count as training-ready until the exact source
-- bytes have an immutable object reference. Also deduplicate global datasets
-- whose season is NULL (ordinary UNIQUE constraints treat NULLs as distinct).

create unique index if not exists wow_nfl_source_snapshot_content_identity
    on public.wow_nfl_source_snapshots(
        dataset_name,
        coalesce(season, -1),
        content_sha256
    );

create or replace function public.wow_nfl_event_p1_data_readiness()
returns jsonb
language sql
stable
security invoker
set search_path = public
as $$
    with counts as (
        select
            count(*) filter (
                where dataset_name='SCHEDULES'
                  and source_status='CAPTURED'
                  and raw_object_uri is not null
            ) as schedules_snapshots,
            count(*) filter (
                where dataset_name='PLAY_BY_PLAY'
                  and source_status='CAPTURED'
                  and raw_object_uri is not null
            ) as pbp_snapshots,
            count(*) filter (
                where dataset_name='WEEKLY_ROSTERS'
                  and source_status='CAPTURED'
                  and raw_object_uri is not null
            ) as roster_snapshots,
            count(*) filter (
                where dataset_name='INJURIES'
                  and source_status='CAPTURED'
                  and raw_object_uri is not null
            ) as injury_snapshots,
            count(*) filter (
                where source_status='CAPTURED'
                  and raw_object_uri is null
            ) as captured_but_unpreserved
        from public.wow_nfl_source_snapshots
    ), game_counts as (
        select count(*) as training_games from public.wow_nfl_training_games
    ), team_counts as (
        select count(*) as team_game_summaries from public.wow_nfl_game_team_summaries
    )
    select jsonb_build_object(
        'ok', true,
        'phase', 'P1_DATA_BACKBONE',
        'source_family', 'NFLVERSE_PUBLIC_DATA',
        'schedules_snapshots', counts.schedules_snapshots,
        'pbp_snapshots', counts.pbp_snapshots,
        'roster_snapshots', counts.roster_snapshots,
        'injury_snapshots', counts.injury_snapshots,
        'captured_but_unpreserved', counts.captured_but_unpreserved,
        'training_games', game_counts.training_games,
        'team_game_summaries', team_counts.team_game_summaries,
        'historical_data_ready', (
            counts.schedules_snapshots > 0
            and counts.pbp_snapshots > 0
            and counts.roster_snapshots > 0
            and counts.injury_snapshots > 0
            and counts.captured_but_unpreserved = 0
            and game_counts.training_games > 0
            and team_counts.team_game_summaries >= game_counts.training_games * 2
        ),
        'model_status', 'MODEL_UNAVAILABLE',
        'probability_publishable', false,
        'can_execute', false
    )
    from counts cross join game_counts cross join team_counts;
$$;

revoke all on function public.wow_nfl_event_p1_data_readiness()
from anon, authenticated;
