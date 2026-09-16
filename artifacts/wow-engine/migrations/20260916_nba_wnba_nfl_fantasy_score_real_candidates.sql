-- V17 real-data NBA/WNBA/NFL Fantasy Score research-candidate builder.
-- Candidate-only. Never certifies, promotes, publishes, ranks, or executes.

create table if not exists public.wow_fantasy_score_source_cache (
  source_id text primary key,
  sport text not null,
  source_url text not null,
  captured_at timestamptz not null default now(),
  http_status integer not null check (http_status = 200),
  raw_sha256 text not null check (raw_sha256 ~ '^[0-9a-f]{64}$'),
  raw_bytes bigint not null check (raw_bytes > 0),
  raw_body text not null,
  research_only boolean not null default true check (research_only = true),
  can_execute boolean not null default false check (can_execute = false)
);
alter table public.wow_fantasy_score_source_cache enable row level security;
revoke all on public.wow_fantasy_score_source_cache from public, anon, authenticated;
grant all on public.wow_fantasy_score_source_cache to service_role;

create or replace function public.wow_v17_csv_fields(p_line text)
returns text[]
language sql
immutable
strict
as $$
  select array_agg(replace(coalesce(m[1],m[2]), '""', '"') order by ord)
  from regexp_matches(p_line, '(?:^|,)(?:"((?:[^"]|"")*)"|([^,]*))', 'g') with ordinality r(m,ord)
$$;
revoke all on function public.wow_v17_csv_fields(text) from public, anon, authenticated;
grant execute on function public.wow_v17_csv_fields(text) to service_role;

create or replace function public.wow_v17_fantasy_fetch_source(
  p_source_id text,
  p_sport text,
  p_url text,
  p_expected_sha256 text
)
returns text
language plpgsql
security invoker
set search_path = public, extensions, pg_temp
as $$
declare
  v_body text;
  v_sha text;
  v_http extensions.http_response;
begin
  if p_expected_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'FANTASY_SCORE_SOURCE_EXPECTED_SHA_INVALID:%', p_source_id;
  end if;
  select raw_body, raw_sha256 into v_body, v_sha
  from public.wow_fantasy_score_source_cache where source_id=p_source_id;
  if v_body is not null then
    if v_sha <> p_expected_sha256 then
      raise exception 'FANTASY_SCORE_CACHED_SOURCE_HASH_MISMATCH:%', p_source_id;
    end if;
    return v_body;
  end if;
  v_http := extensions.http_get(p_url);
  if v_http.status <> 200 or length(coalesce(v_http.content,'')) < 1000 then
    raise exception 'FANTASY_SCORE_SOURCE_FETCH_INVALID:%:%', p_source_id, v_http.status;
  end if;
  v_body := v_http.content;
  v_sha := encode(digest(convert_to(v_body,'UTF8'),'sha256'),'hex');
  if v_sha <> p_expected_sha256 then
    raise exception 'FANTASY_SCORE_SOURCE_HASH_MISMATCH:%:%', p_source_id, v_sha;
  end if;
  insert into public.wow_fantasy_score_source_cache(
    source_id,sport,source_url,http_status,raw_sha256,raw_bytes,raw_body,research_only,can_execute
  ) values (p_source_id,upper(p_sport),p_url,200,v_sha,octet_length(v_body),v_body,true,false);
  return v_body;
end;
$$;
revoke all on function public.wow_v17_fantasy_fetch_source(text,text,text,text) from public, anon, authenticated;
grant execute on function public.wow_v17_fantasy_fetch_source(text,text,text,text) to service_role;

create or replace function public.wow_v17_build_nba_wnba_nfl_fantasy_candidates(
  p_training_code_sha text,
  p_activate boolean default false
)
returns jsonb
language plpgsql
security invoker
set search_path = public, extensions, pg_temp
as $$
declare
  v_nba text;
  v_wnba text;
  v_nfl24 text;
  v_nfl25 text;
  v_lane text;
  v_events integer;
  v_train_events integer;
  v_cal_events integer;
  v_test_events integer;
  v_source_rows bigint;
  v_train_rows bigint;
  v_cal_rows bigint;
  v_test_rows bigint;
  v_residual_total bigint;
  v_residual_persisted bigint;
  v_player_means jsonb;
  v_cohort_means jsonb;
  v_residuals jsonb;
  v_profile jsonb;
  v_payload jsonb;
  v_source_hash text;
  v_model_family text;
  v_model_version text;
  v_transform text;
  v_specialist text;
  v_sport text;
  v_stat_type text := 'FANTASY_SCORE';
  v_artifact_checksum text;
  v_artifact_id uuid;
  v_line_max numeric;
  v_results jsonb := '[]'::jsonb;
begin
  if coalesce(p_training_code_sha,'') !~ '^[0-9a-f]{40}$' then
    raise exception 'FANTASY_SCORE_TRAINING_CODE_SHA_INVALID';
  end if;

  v_nba := public.wow_v17_fantasy_fetch_source(
    'SPORTSDATAVERSE_ESPN_NBA_PLAYER_BOX_2026_7A4868DA', 'NBA',
    'https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_nba_player_boxscores/player_box_2026.csv',
    '7a4868da67ae7f6ebfba23389e04220be2e3f69d2e56a3942c02aa56f1becc89');
  v_wnba := public.wow_v17_fantasy_fetch_source(
    'SPORTSDATAVERSE_ESPN_WNBA_PLAYER_BOX_2026_F4F1FCA5', 'WNBA',
    'https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_wnba_player_boxscores/player_box_2026.csv',
    'f4f1fca50f6517b8006683d2da416f526e8141b6829f0800b04f46a93f4622b8');
  v_nfl24 := public.wow_v17_fantasy_fetch_source(
    'NFLVERSE_PLAYER_WEEK_REG_2024_3DDC45A8', 'NFL',
    'https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_2024.csv',
    '3ddc45a84f759aa348ce465ae001752c530575455717657cdfe1f8abfcdb4759');
  v_nfl25 := public.wow_v17_fantasy_fetch_source(
    'NFLVERSE_PLAYER_WEEK_REG_2025_E5E0615B', 'NFL',
    'https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_2025.csv',
    'e5e0615b3d96a3eaebfaee91e55afb4a4e7fe0caf057454177bcd7d6ad4bcfc2');

  create temp table tmp_fs_rows(
    lane text not null,
    event_bucket text not null,
    event_id text not null,
    player_id text not null,
    player_name text not null,
    cohort text not null,
    c1 numeric not null default 0,c2 numeric not null default 0,c3 numeric not null default 0,
    c4 numeric not null default 0,c5 numeric not null default 0,c6 numeric not null default 0,
    c7 numeric not null default 0,c8 numeric not null default 0,c9 numeric not null default 0,
    c10 numeric not null default 0,
    fantasy_score numeric not null,
    primary key(lane,event_id,player_id)
  ) on commit drop;

  -- ESPN NBA/WNBA player box scores. The release schema is frozen by raw SHA-256.
  insert into tmp_fs_rows
  with sources(lane,body) as (values ('NBA',v_nba),('WNBA',v_wnba)),
  parsed as (
    select lane, public.wow_v17_csv_fields(line) a
    from sources, lateral regexp_split_to_table(body,E'\n') with ordinality t(line,n)
    where n>1 and line<>''
  ), r as (
    select lane,a,
      coalesce(nullif(a[28],''),'0')::numeric pts,
      coalesce(nullif(a[21],''),'0')::numeric reb,
      coalesce(nullif(a[22],''),'0')::numeric ast,
      coalesce(nullif(a[23],''),'0')::numeric stl,
      coalesce(nullif(a[24],''),'0')::numeric blk,
      coalesce(nullif(a[25],''),'0')::numeric tov
    from parsed
    where array_length(a,1)=50 and a[3]='2' and a[6]<>'' and a[7]<>''
      and a[12] ~ '^[0-9]+(\.[0-9]+)?$' and a[12]::numeric > 0
  )
  select lane,a[4],a[1],a[6],a[7],'ALL',pts,reb,ast,stl,blk,tov,0,0,0,0,
         pts + reb*1.2 + ast*1.5 + blk*3 + stl*3 - tov
  from r;

  -- nflverse weekly player stats. c10 is an exact score-equivalent transform:
  -- raw 2PT conversions + 3*(special-team return TD + fumble-recovery TD).
  -- At the PrizePicks 2PT weight of 2 this preserves the two additional 6-point
  -- NFL scoring categories without changing the established 10-component bridge.
  insert into tmp_fs_rows
  with sources(body) as (values (v_nfl24),(v_nfl25)),
  parsed as (
    select public.wow_v17_csv_fields(line) a
    from sources, lateral regexp_split_to_table(body,E'\n') with ordinality t(line,n)
    where n>1 and line<>''
  ), r as (
    select a,
      coalesce(nullif(a[15],''),'0')::numeric pass_yd,
      coalesce(nullif(a[16],''),'0')::numeric pass_td,
      coalesce(nullif(a[17],''),'0')::numeric ints,
      coalesce(nullif(a[34],''),'0')::numeric rush_yd,
      coalesce(nullif(a[35],''),'0')::numeric rush_td,
      coalesce(nullif(a[47],''),'0')::numeric rec_yd,
      coalesce(nullif(a[45],''),'0')::numeric rec,
      coalesce(nullif(a[48],''),'0')::numeric rec_td,
      coalesce(nullif(a[97],''),'0')::numeric fum_lost,
      coalesce(nullif(a[27],''),'0')::numeric p2,
      coalesce(nullif(a[40],''),'0')::numeric r2,
      coalesce(nullif(a[55],''),'0')::numeric rec2,
      coalesce(nullif(a[64],''),'0')::numeric return_td,
      coalesce(nullif(a[90],''),'0')::numeric fum_rec_td
    from parsed
    where array_length(a,1)=150 and a[9]='REG' and a[1]<>'' and a[3]<>''
      and a[4] in ('QB','RB','WR','TE') and a[10]<>''
  )
  select 'NFL',a[7]||'-'||lpad(a[8],2,'0'),a[10],a[1],a[3],a[4],
         pass_yd,pass_td,ints,rush_yd,rush_td,rec_yd,rec,rec_td,fum_lost,
         p2+r2+rec2+3*return_td+3*fum_rec_td,
         pass_yd*.04 + pass_td*4 - ints + rush_yd*.1 + rush_td*6
           + rec + rec_yd*.1 + rec_td*6 - fum_lost
           + (p2+r2+rec2)*2 + return_td*6 + fum_rec_td*6
  from r;

  foreach v_lane in array array['NBA','WNBA','NFL'] loop
    drop table if exists tmp_fs_events;
    drop table if exists tmp_fs_train;
    drop table if exists tmp_fs_cohort_prior;
    drop table if exists tmp_fs_player_prior;
    drop table if exists tmp_fs_residual;
    drop table if exists tmp_fs_cmean;
    drop table if exists tmp_fs_pagg;

    create temp table tmp_fs_events on commit drop as
    select event_bucket,row_number() over(order by event_bucket)::integer event_idx
    from (select distinct event_bucket from tmp_fs_rows where lane=v_lane) x;
    select count(*) into v_events from tmp_fs_events;
    if v_events < 7 then raise exception 'FANTASY_SCORE_EVENTS_INSUFFICIENT:%',v_lane; end if;
    v_train_events := greatest(1,floor(v_events*.70)::integer);
    v_cal_events := greatest(1,floor(v_events*.15)::integer);
    if v_train_events+v_cal_events>=v_events then v_cal_events:=greatest(1,v_events-v_train_events-1); end if;
    v_test_events := v_events-v_train_events-v_cal_events;

    create temp table tmp_fs_train on commit drop as
    select r.*,e.event_idx from tmp_fs_rows r join tmp_fs_events e using(event_bucket)
    where r.lane=v_lane and e.event_idx<=v_train_events;
    select count(*) into v_source_rows from tmp_fs_rows where lane=v_lane;
    select count(*) into v_train_rows from tmp_fs_train;
    select count(*) into v_cal_rows from tmp_fs_rows r join tmp_fs_events e using(event_bucket)
      where r.lane=v_lane and e.event_idx>v_train_events and e.event_idx<=v_train_events+v_cal_events;
    select count(*) into v_test_rows from tmp_fs_rows r join tmp_fs_events e using(event_bucket)
      where r.lane=v_lane and e.event_idx>v_train_events+v_cal_events;
    if v_train_rows<500 or v_cal_rows<50 or v_test_rows<50 then
      raise exception 'FANTASY_SCORE_SPLIT_ROWS_INSUFFICIENT:%:%:%:%',v_lane,v_train_rows,v_cal_rows,v_test_rows;
    end if;

    create temp table tmp_fs_cmean on commit drop as
    select cohort,count(*)::numeric n,avg(c1) m1,avg(c2) m2,avg(c3) m3,avg(c4) m4,avg(c5) m5,
      avg(c6) m6,avg(c7) m7,avg(c8) m8,avg(c9) m9,avg(c10) m10
    from tmp_fs_train group by cohort;
    create temp table tmp_fs_pagg on commit drop as
    select player_id,player_name,cohort,count(*)::numeric n,avg(c1) a1,avg(c2) a2,avg(c3) a3,
      avg(c4) a4,avg(c5) a5,avg(c6) a6,avg(c7) a7,avg(c8) a8,avg(c9) a9,avg(c10) a10
    from tmp_fs_train group by player_id,player_name,cohort;

    create temp table tmp_fs_cohort_prior on commit drop as
    with ea as (
      select cohort,event_idx,count(*)::numeric n,sum(c1)s1,sum(c2)s2,sum(c3)s3,sum(c4)s4,sum(c5)s5,
        sum(c6)s6,sum(c7)s7,sum(c8)s8,sum(c9)s9,sum(c10)s10
      from tmp_fs_train group by cohort,event_idx
    ) select cohort,event_idx,
      sum(n) over(partition by cohort order by event_idx rows between unbounded preceding and 1 preceding) pn,
      sum(s1) over(partition by cohort order by event_idx rows between unbounded preceding and 1 preceding) ps1,
      sum(s2) over(partition by cohort order by event_idx rows between unbounded preceding and 1 preceding) ps2,
      sum(s3) over(partition by cohort order by event_idx rows between unbounded preceding and 1 preceding) ps3,
      sum(s4) over(partition by cohort order by event_idx rows between unbounded preceding and 1 preceding) ps4,
      sum(s5) over(partition by cohort order by event_idx rows between unbounded preceding and 1 preceding) ps5,
      sum(s6) over(partition by cohort order by event_idx rows between unbounded preceding and 1 preceding) ps6,
      sum(s7) over(partition by cohort order by event_idx rows between unbounded preceding and 1 preceding) ps7,
      sum(s8) over(partition by cohort order by event_idx rows between unbounded preceding and 1 preceding) ps8,
      sum(s9) over(partition by cohort order by event_idx rows between unbounded preceding and 1 preceding) ps9,
      sum(s10) over(partition by cohort order by event_idx rows between unbounded preceding and 1 preceding) ps10
    from ea;
    create temp table tmp_fs_player_prior on commit drop as
    with pe as (
      select player_id,event_idx,count(*)::numeric n,sum(c1)s1,sum(c2)s2,sum(c3)s3,sum(c4)s4,sum(c5)s5,
        sum(c6)s6,sum(c7)s7,sum(c8)s8,sum(c9)s9,sum(c10)s10
      from tmp_fs_train group by player_id,event_idx
    ) select player_id,event_idx,
      sum(n) over(partition by player_id order by event_idx rows between unbounded preceding and 1 preceding) pn,
      sum(s1) over(partition by player_id order by event_idx rows between unbounded preceding and 1 preceding) ps1,
      sum(s2) over(partition by player_id order by event_idx rows between unbounded preceding and 1 preceding) ps2,
      sum(s3) over(partition by player_id order by event_idx rows between unbounded preceding and 1 preceding) ps3,
      sum(s4) over(partition by player_id order by event_idx rows between unbounded preceding and 1 preceding) ps4,
      sum(s5) over(partition by player_id order by event_idx rows between unbounded preceding and 1 preceding) ps5,
      sum(s6) over(partition by player_id order by event_idx rows between unbounded preceding and 1 preceding) ps6,
      sum(s7) over(partition by player_id order by event_idx rows between unbounded preceding and 1 preceding) ps7,
      sum(s8) over(partition by player_id order by event_idx rows between unbounded preceding and 1 preceding) ps8,
      sum(s9) over(partition by player_id order by event_idx rows between unbounded preceding and 1 preceding) ps9,
      sum(s10) over(partition by player_id order by event_idx rows between unbounded preceding and 1 preceding) ps10
    from pe;
    create temp table tmp_fs_residual on commit drop as
    select t.cohort,t.event_bucket,t.event_id,t.player_id,
      t.c1-(case when pp.pn>0 then (pp.ps1+(cp.ps1/cp.pn)*3)/(pp.pn+3) else cp.ps1/cp.pn end) r1,
      t.c2-(case when pp.pn>0 then (pp.ps2+(cp.ps2/cp.pn)*3)/(pp.pn+3) else cp.ps2/cp.pn end) r2,
      t.c3-(case when pp.pn>0 then (pp.ps3+(cp.ps3/cp.pn)*3)/(pp.pn+3) else cp.ps3/cp.pn end) r3,
      t.c4-(case when pp.pn>0 then (pp.ps4+(cp.ps4/cp.pn)*3)/(pp.pn+3) else cp.ps4/cp.pn end) r4,
      t.c5-(case when pp.pn>0 then (pp.ps5+(cp.ps5/cp.pn)*3)/(pp.pn+3) else cp.ps5/cp.pn end) r5,
      t.c6-(case when pp.pn>0 then (pp.ps6+(cp.ps6/cp.pn)*3)/(pp.pn+3) else cp.ps6/cp.pn end) r6,
      t.c7-(case when pp.pn>0 then (pp.ps7+(cp.ps7/cp.pn)*3)/(pp.pn+3) else cp.ps7/cp.pn end) r7,
      t.c8-(case when pp.pn>0 then (pp.ps8+(cp.ps8/cp.pn)*3)/(pp.pn+3) else cp.ps8/cp.pn end) r8,
      t.c9-(case when pp.pn>0 then (pp.ps9+(cp.ps9/cp.pn)*3)/(pp.pn+3) else cp.ps9/cp.pn end) r9,
      t.c10-(case when pp.pn>0 then (pp.ps10+(cp.ps10/cp.pn)*3)/(pp.pn+3) else cp.ps10/cp.pn end) r10
    from tmp_fs_train t join tmp_fs_cohort_prior cp on cp.cohort=t.cohort and cp.event_idx=t.event_idx
    left join tmp_fs_player_prior pp on pp.player_id=t.player_id and pp.event_idx=t.event_idx
    where cp.pn>0;
    select count(*) into v_residual_total from tmp_fs_residual;
    if v_residual_total<1000 then raise exception 'FANTASY_SCORE_RESIDUAL_HISTORY_INSUFFICIENT:%:%',v_lane,v_residual_total; end if;

    if v_lane in ('NBA','WNBA') then
      select coalesce(jsonb_object_agg(pa.player_name,jsonb_build_object(
        'points',(pa.a1*pa.n+cm.m1*3)/(pa.n+3),'rebounds',(pa.a2*pa.n+cm.m2*3)/(pa.n+3),
        'assists',(pa.a3*pa.n+cm.m3*3)/(pa.n+3),'steals',(pa.a4*pa.n+cm.m4*3)/(pa.n+3),
        'blocks',(pa.a5*pa.n+cm.m5*3)/(pa.n+3),'turnovers',(pa.a6*pa.n+cm.m6*3)/(pa.n+3)
      ) order by pa.player_name),'{}'::jsonb) into v_player_means
      from tmp_fs_pagg pa join tmp_fs_cmean cm using(cohort)
      join (select player_name,count(distinct player_id)n from tmp_fs_pagg group by player_name) nc using(player_name)
      where nc.n=1;
      select jsonb_build_object(v_lane,jsonb_build_object('points',m1,'rebounds',m2,'assists',m3,'steals',m4,'blocks',m5,'turnovers',m6))
        into v_cohort_means from tmp_fs_cmean;
      with ranked as (
        select *,row_number() over(order by md5(event_bucket||'|'||event_id||'|'||player_id)) rn from tmp_fs_residual
      ) select jsonb_build_object(v_lane,jsonb_agg(jsonb_build_object(
        'points',r1,'rebounds',r2,'assists',r3,'steals',r4,'blocks',r5,'turnovers',r6
      ) order by rn)),count(*) into v_residuals,v_residual_persisted from ranked where rn<=2000;
      v_profile:=jsonb_build_object(
        'profile_id',case when v_lane='NBA' then 'PRIZEPICKS_NBA_FANTASY_SCORE_2025_07_10_VERIFIED_V1' else 'PRIZEPICKS_WNBA_BASKETBALL_FANTASY_SCORE_2026_09_VERIFIED_V1' end,
        'verified',true,
        'source_url','https://www.prizepicks.com/playbook-article/how-to-play-prizepicks-nba-fantasy-scoring-system',
        'verification_scope',case when v_lane='NBA' then 'NBA_OFFICIAL_SCORING_CHART' else 'PRIZEPICKS_BASKETBALL_SCORING_CHART_APPLIED_TO_WNBA_RESEARCH_CANDIDATE' end,
        'weights',jsonb_build_object('points',1,'rebounds',1.2,'assists',1.5,'steals',3,'blocks',3,'turnovers',-1));
      v_sport:=v_lane;
      v_model_family:=v_lane||'_FANTASY_SCORE_EMPIRICAL_RESIDUAL_V1';
      v_transform:=v_lane||'_ESPN_PLAYER_BOX_RESIDUAL_V1';
      v_specialist:=case when v_lane='NBA' then 'wow.nba-dfs-fantasy-score-expert@1' else 'wow.wnba-dfs-fantasy-score-expert@1' end;
      select raw_sha256 into v_source_hash from public.wow_fantasy_score_source_cache where source_id=case when v_lane='NBA' then 'SPORTSDATAVERSE_ESPN_NBA_PLAYER_BOX_2026_7A4868DA' else 'SPORTSDATAVERSE_ESPN_WNBA_PLAYER_BOX_2026_F4F1FCA5' end;
    else
      select coalesce(jsonb_object_agg(pa.player_name,jsonb_build_object(
        'passing_yards',(pa.a1*pa.n+cm.m1*3)/(pa.n+3),'passing_td',(pa.a2*pa.n+cm.m2*3)/(pa.n+3),
        'interceptions',(pa.a3*pa.n+cm.m3*3)/(pa.n+3),'rushing_yards',(pa.a4*pa.n+cm.m4*3)/(pa.n+3),
        'rushing_td',(pa.a5*pa.n+cm.m5*3)/(pa.n+3),'receiving_yards',(pa.a6*pa.n+cm.m6*3)/(pa.n+3),
        'receptions',(pa.a7*pa.n+cm.m7*3)/(pa.n+3),'receiving_td',(pa.a8*pa.n+cm.m8*3)/(pa.n+3),
        'fumbles_lost',(pa.a9*pa.n+cm.m9*3)/(pa.n+3),'two_point_conversions',(pa.a10*pa.n+cm.m10*3)/(pa.n+3)
      ) order by pa.player_name),'{}'::jsonb) into v_player_means
      from tmp_fs_pagg pa join tmp_fs_cmean cm using(cohort)
      join (select player_name,count(distinct player_id)n from tmp_fs_pagg group by player_name) nc using(player_name)
      where nc.n=1;
      select jsonb_object_agg(cohort,jsonb_build_object(
        'passing_yards',m1,'passing_td',m2,'interceptions',m3,'rushing_yards',m4,'rushing_td',m5,
        'receiving_yards',m6,'receptions',m7,'receiving_td',m8,'fumbles_lost',m9,'two_point_conversions',m10
      ) order by cohort) into v_cohort_means from tmp_fs_cmean;
      with ranked as (
        select *,row_number() over(partition by cohort order by md5(event_bucket||'|'||event_id||'|'||player_id)) rn from tmp_fs_residual
      ), per as (
        select cohort,jsonb_agg(jsonb_build_object(
          'passing_yards',r1,'passing_td',r2,'interceptions',r3,'rushing_yards',r4,'rushing_td',r5,
          'receiving_yards',r6,'receptions',r7,'receiving_td',r8,'fumbles_lost',r9,'two_point_conversions',r10
        ) order by rn) vals,count(*) n from ranked where rn<=500 group by cohort
      ) select jsonb_object_agg(cohort,vals order by cohort),sum(n) into v_residuals,v_residual_persisted from per;
      v_profile:=jsonb_build_object(
        'profile_id','PRIZEPICKS_NFL_OFFENSE_FANTASY_SCORE_2025_09_17_EXACT_EQUIVALENT_V1',
        'verified',true,
        'source_url','https://www.prizepicks.com/playbook-article/how-to-play-prizepicks-nfl-fantasy-scoring-system',
        'component_transform',jsonb_build_object('two_point_conversions','raw_2pt + 3*(special_teams_return_td + fumble_recovery_td)'),
        'weights',jsonb_build_object('passing_yards',.04,'passing_td',4,'interceptions',-1,'rushing_yards',.1,'rushing_td',6,
          'receiving_yards',.1,'receptions',1,'receiving_td',6,'fumbles_lost',-1,'two_point_conversions',2));
      v_sport:='NFL'; v_model_family:='NFL_FANTASY_SCORE_EMPIRICAL_RESIDUAL_V2';
      v_transform:='NFLVERSE_WEEKLY_EXACT_PRIZEPICKS_SCORE_EQUIVALENT_V2'; v_specialist:='wow.nfl-dfs-fantasy-score-expert@1';
      select encode(digest(convert_to(string_agg(raw_sha256,'' order by source_id),'UTF8'),'sha256'),'hex') into v_source_hash
      from public.wow_fantasy_score_source_cache where source_id in ('NFLVERSE_PLAYER_WEEK_REG_2024_3DDC45A8','NFLVERSE_PLAYER_WEEK_REG_2025_E5E0615B');
    end if;

    if v_residual_persisted<1000 then raise exception 'FANTASY_SCORE_RESIDUAL_SAMPLE_INVALID:%:%',v_lane,v_residual_persisted; end if;
    select greatest(1,ceil(max(fantasy_score)+10)) into v_line_max from tmp_fs_rows where lane=v_lane;
    v_model_version:=v_model_family||'_'||substr(v_source_hash,1,12)||'_R'||v_residual_persisted;
    v_payload:=jsonb_build_object(
      'artifact_schema_version','FANTASY_SCORE_CANDIDATE_ARTIFACT_V1','lane',v_lane,'player_means',v_player_means,
      case when v_lane='NFL' then 'position_means' else 'cohort_means' end,v_cohort_means,
      'residual_vectors',v_residuals,'scoring_profile',v_profile,
      'fit_contract',jsonb_build_object('whole_event_chronological_split',true,'train_fraction',.70,'calibration_fraction',.15,
        'untouched_test_required',true,'player_shrinkage_prior_games',3,'strictly_prior_residuals',true,
        'residual_sampling',case when v_lane='NFL' then 'DETERMINISTIC_MD5_FIRST_500_PER_POSITION' else 'DETERMINISTIC_MD5_FIRST_2000' end,
        'candidate_only',true));
    v_artifact_checksum:=encode(digest(convert_to(v_payload::text,'UTF8'),'sha256'),'hex');

    if p_activate then
      update public.wow_prop_fitted_model_artifacts set candidate_research_active=false
      where upper(sport)=v_sport and upper(stat_type)=v_stat_type and feature_schema_version='PROP_FEATURES_V1' and candidate_research_active=true;
    end if;
    insert into public.wow_prop_fitted_model_artifacts(
      provider_identity,model_family,model_artifact_version,calibrator_version,sport,stat_type,
      feature_schema_version,feature_transform_version,specialist_version,certification_id,lifecycle_state,
      training_dataset_hash,training_code_sha,artifact_checksum,artifact_format,artifact_payload,
      supported_line_min,supported_line_max,training_rows,validation_metrics,promoted,active,
      probability_publishable,can_execute,candidate_research_active
    ) values (
      'WOW_PROP_FITTED_MODEL_V1',v_model_family,v_model_version,'UNAVAILABLE_CANDIDATE_ONLY',v_sport,v_stat_type,
      'PROP_FEATURES_V1',v_transform,v_specialist,'CANDIDATE-NOT-CERTIFIED-'||substr(v_source_hash,1,16),'CANDIDATE',
      v_source_hash,p_training_code_sha,v_artifact_checksum,'FANTASY_SCORE_CANDIDATE_JSON_V1',v_payload,0,v_line_max,
      v_train_rows,jsonb_build_object('candidate_only',true,'source_rows',v_source_rows,'source_events',v_events,
        'train_rows',v_train_rows,'calibration_rows',v_cal_rows,'untouched_test_rows',v_test_rows,
        'train_events',v_train_events,'calibration_events',v_cal_events,'untouched_test_events',v_test_events,
        'player_mean_keys',(select count(*) from jsonb_object_keys(v_player_means)),
        'residual_vectors_total',v_residual_total,'residual_vectors_persisted',v_residual_persisted,
        'calibration_status','BLOCKED_NO_CERTIFIED_EXACT_LINE_CALIBRATION_ARTIFACT','certification_status','CANDIDATE_ONLY',
        'probability_publishable',false,'rank_eligible',false,'can_execute',false,'scoring_profile_id',v_profile->>'profile_id'),
      false,false,false,false,p_activate
    ) on conflict(provider_identity,model_artifact_version) do update set
      training_code_sha=excluded.training_code_sha,artifact_checksum=excluded.artifact_checksum,
      artifact_payload=excluded.artifact_payload,validation_metrics=excluded.validation_metrics,
      candidate_research_active=excluded.candidate_research_active,promoted=false,active=false,
      probability_publishable=false,can_execute=false
    returning artifact_id into v_artifact_id;
    v_results:=v_results||jsonb_build_array(jsonb_build_object('lane',v_lane,'artifact_id',v_artifact_id,
      'model_artifact_version',v_model_version,'training_rows',v_train_rows,'residual_vectors',v_residual_persisted,
      'candidate_research_active',p_activate,'probability_publishable',false,'rank_eligible',false,'can_execute',false));
  end loop;
  return jsonb_build_object('status','COMPLETED','artifacts',v_results,'probability_publishable',false,'rank_eligible',false,'can_execute',false);
end;
$$;

revoke all on function public.wow_v17_build_nba_wnba_nfl_fantasy_candidates(text,boolean) from public, anon, authenticated;
grant execute on function public.wow_v17_build_nba_wnba_nfl_fantasy_candidates(text,boolean) to service_role;
comment on function public.wow_v17_build_nba_wnba_nfl_fantasy_candidates(text,boolean) is
  'Evidence-only V17 NBA/WNBA/NFL Fantasy Score candidate builder. Never grants publication, ranking, or execution authority.';
