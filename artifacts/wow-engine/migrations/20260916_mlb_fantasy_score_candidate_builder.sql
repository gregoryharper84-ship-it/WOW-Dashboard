-- V17 real-data MLB Fantasy Score candidate builder.
--
-- Purpose:
--   Build evidence-only MLB hitter/pitcher Fantasy Score candidate artifacts from
--   immutable Retrosplits regular-season player-game rows. Player identity is
--   resolved from a frozen Retrosheet player-index snapshot cached in
--   wow_mlb_research_raw_cache. This function never certifies, promotes, publishes,
--   ranks, or executes a wager.
--
-- Governance invariants:
--   can_execute=false
--   probability_publishable=false
--   active=false
--   promoted=false
--   lifecycle_state='CANDIDATE'
--   candidate_research_active is opt-in and remains constrained by the registry.

create or replace function public.wow_v17_build_mlb_fantasy_score_candidates(
  p_training_code_sha text,
  p_activate boolean default false
)
returns jsonb
language plpgsql
security invoker
set search_path = public, extensions, pg_temp
as $$
declare
  v_identity_cache_id uuid;
  v_identity_body text;
  v_identity_sha text;
  v_http extensions.http_response;
  v_lane text;
  v_expected bigint;
  v_mapped bigint;
  v_identity_coverage numeric;
  v_event_n integer;
  v_train_event_n integer;
  v_cal_event_n integer;
  v_test_event_n integer;
  v_source_rows bigint;
  v_train_rows bigint;
  v_cal_rows bigint;
  v_test_rows bigint;
  v_source_hash text;
  v_player_means jsonb;
  v_cohort_mean jsonb;
  v_residuals jsonb;
  v_residual_total bigint;
  v_residual_persisted bigint;
  v_profile jsonb;
  v_payload jsonb;
  v_artifact_checksum text;
  v_model_family text;
  v_model_version text;
  v_stat_type text;
  v_specialist text;
  v_transform text;
  v_artifact_id uuid;
  v_line_max numeric;
  v_results jsonb := '[]'::jsonb;
begin
  if coalesce(p_training_code_sha, '') !~ '^[0-9a-f]{40}$' then
    raise exception 'FANTASY_SCORE_TRAINING_CODE_SHA_INVALID';
  end if;

  -- Freeze player identity before fitting. Replays always prefer the frozen cache.
  select cache_id, raw_body, raw_sha256
    into v_identity_cache_id, v_identity_body, v_identity_sha
  from public.wow_mlb_research_raw_cache
  where source_label = 'RETROSHEET_PLAYER_IDENTITY_20260916'
    and http_status = 200
    and research_only = true
    and can_execute = false
    and raw_body is not null
  order by created_at desc
  limit 1;

  if v_identity_cache_id is null then
    begin
      v_http := extensions.http_get('http://www.retrosheet.org/downloads/csvplayers.html');
    exception when others then
      raise exception 'FANTASY_SCORE_IDENTITY_FETCH_FAILED:%', sqlstate;
    end;
    if v_http.status <> 200 or length(coalesce(v_http.content, '')) < 1000000 then
      raise exception 'FANTASY_SCORE_IDENTITY_FETCH_INVALID';
    end if;
    v_identity_body := v_http.content;
    v_identity_sha := encode(digest(convert_to(v_identity_body, 'UTF8'), 'sha256'), 'hex');
    insert into public.wow_mlb_research_raw_cache(
      source_url, http_status, content_type, raw_body, raw_bytes, raw_sha256,
      row_count, header_line, schema_sha256, source_label, research_only, can_execute
    ) values (
      'http://www.retrosheet.org/downloads/csvplayers.html',
      v_http.status,
      coalesce(v_http.content_type, 'text/html'),
      v_identity_body,
      octet_length(v_identity_body),
      v_identity_sha,
      (select count(*) from regexp_split_to_table(v_identity_body, E'\n') line
        where line ~ E'^[a-z0-9-]{8}\t'),
      'RetroID<TAB>Use Name<TAB>Full Name<TAB>First<TAB>Last<TAB>AKA',
      encode(digest(convert_to('retroid,use_name,full_name,first,last,aka', 'UTF8'), 'sha256'), 'hex'),
      'RETROSHEET_PLAYER_IDENTITY_20260916',
      true,
      false
    ) returning cache_id into v_identity_cache_id;
  end if;

  if v_identity_sha !~ '^[0-9a-f]{64}$' then
    raise exception 'FANTASY_SCORE_IDENTITY_HASH_INVALID';
  end if;

  create temp table tmp_fs_identity(
    person_key text primary key,
    player_name text not null
  ) on commit drop;

  insert into tmp_fs_identity(person_key, player_name)
  select trim(split_part(line, E'\t', 1)), trim(split_part(line, E'\t', 2))
  from regexp_split_to_table(v_identity_body, E'\n') line
  where line ~ E'^[a-z0-9-]{8}\t'
    and trim(split_part(line, E'\t', 2)) <> ''
  on conflict (person_key) do nothing;

  if (select count(*) from tmp_fs_identity) < 10000 then
    raise exception 'FANTASY_SCORE_IDENTITY_SNAPSHOT_INSUFFICIENT';
  end if;

  -- Generic component slots keep the chronological fitting logic identical for
  -- hitter and pitcher lanes. Component names are restored only at serialization.
  create temp table tmp_fs_source(
    lane text not null,
    event_date date not null,
    event_id text not null,
    person_key text not null,
    player_name text not null,
    c1 numeric not null,
    c2 numeric not null,
    c3 numeric not null,
    c4 numeric not null,
    c5 numeric not null,
    c6 numeric not null default 0,
    c7 numeric not null default 0,
    c8 numeric not null default 0,
    c9 numeric not null default 0,
    fantasy_score numeric not null,
    primary key(lane, event_id, person_key)
  ) on commit drop;

  -- Require identity coverage before filtering to mapped rows.
  select count(*) into v_expected
  from public.wow_mlb_retrosplits_rows r
  where r.season_phase = 'R' and coalesce(r.b_g, 0) > 0 and coalesce(r.b_pa, 0) > 0;
  select count(*) into v_mapped
  from public.wow_mlb_retrosplits_rows r
  join tmp_fs_identity i on i.person_key = r.person_key
  where r.season_phase = 'R' and coalesce(r.b_g, 0) > 0 and coalesce(r.b_pa, 0) > 0;
  if v_expected = 0 or v_mapped::numeric / v_expected < 0.99 then
    raise exception 'FANTASY_SCORE_HITTER_IDENTITY_COVERAGE_INSUFFICIENT:%/%', v_mapped, v_expected;
  end if;

  insert into tmp_fs_source(lane,event_date,event_id,person_key,player_name,c1,c2,c3,c4,c5,c6,c7,c8,c9,fantasy_score)
  select
    'MLB_HITTER', r.game_date, r.game_key, r.person_key, i.player_name,
    greatest(0, coalesce(r.b_h,0) - coalesce(r.b_2b,0) - coalesce(r.b_3b,0) - coalesce(r.b_hr,0)),
    coalesce(r.b_2b,0), coalesce(r.b_3b,0), coalesce(r.b_hr,0), coalesce(r.b_r,0),
    coalesce(r.b_rbi,0), coalesce(r.b_bb,0), coalesce(r.b_hp,0), coalesce(r.b_sb,0),
    greatest(0, coalesce(r.b_h,0) - coalesce(r.b_2b,0) - coalesce(r.b_3b,0) - coalesce(r.b_hr,0))*3
      + coalesce(r.b_2b,0)*5 + coalesce(r.b_3b,0)*8 + coalesce(r.b_hr,0)*10
      + coalesce(r.b_r,0)*2 + coalesce(r.b_rbi,0)*2 + coalesce(r.b_bb,0)*2
      + coalesce(r.b_hp,0)*2 + coalesce(r.b_sb,0)*5
  from public.wow_mlb_retrosplits_rows r
  join tmp_fs_identity i on i.person_key = r.person_key
  where r.season_phase = 'R' and coalesce(r.b_g, 0) > 0 and coalesce(r.b_pa, 0) > 0;

  select count(*) into v_expected
  from public.wow_mlb_retrosplits_rows r
  where r.season_phase = 'R' and coalesce(r.p_gs, 0) > 0 and coalesce(r.p_tbf, 0) > 0;
  select count(*) into v_mapped
  from public.wow_mlb_retrosplits_rows r
  join tmp_fs_identity i on i.person_key = r.person_key
  where r.season_phase = 'R' and coalesce(r.p_gs, 0) > 0 and coalesce(r.p_tbf, 0) > 0;
  if v_expected = 0 or v_mapped::numeric / v_expected < 0.99 then
    raise exception 'FANTASY_SCORE_PITCHER_IDENTITY_COVERAGE_INSUFFICIENT:%/%', v_mapped, v_expected;
  end if;

  insert into tmp_fs_source(lane,event_date,event_id,person_key,player_name,c1,c2,c3,c4,c5,c6,c7,c8,c9,fantasy_score)
  select
    'MLB_PITCHER', r.game_date, r.game_key, r.person_key, i.player_name,
    case when coalesce(r.p_w,0) > 0 then 1 else 0 end,
    case when coalesce(r.p_out,0) >= 18 and coalesce(r.p_er,0) <= 3 then 1 else 0 end,
    coalesce(r.p_so,0), coalesce(r.p_out,0), coalesce(r.p_er,0), 0,0,0,0,
    (case when coalesce(r.p_w,0) > 0 then 1 else 0 end)*6
      + (case when coalesce(r.p_out,0) >= 18 and coalesce(r.p_er,0) <= 3 then 1 else 0 end)*4
      + coalesce(r.p_so,0)*3 + coalesce(r.p_out,0) - coalesce(r.p_er,0)*3
  from public.wow_mlb_retrosplits_rows r
  join tmp_fs_identity i on i.person_key = r.person_key
  where r.season_phase = 'R' and coalesce(r.p_gs, 0) > 0 and coalesce(r.p_tbf, 0) > 0;

  foreach v_lane in array array['MLB_HITTER','MLB_PITCHER'] loop
    drop table if exists tmp_fs_events;
    drop table if exists tmp_fs_train;
    drop table if exists tmp_fs_cohort_prior;
    drop table if exists tmp_fs_player_prior;
    drop table if exists tmp_fs_residual;

    create temp table tmp_fs_events on commit drop as
    select event_date, event_id,
           row_number() over(order by event_date,event_id)::integer as event_idx
    from (select distinct event_date,event_id from tmp_fs_source where lane=v_lane) e;

    select count(*) into v_event_n from tmp_fs_events;
    if v_event_n < 7 then
      raise exception 'FANTASY_SCORE_EVENTS_INSUFFICIENT:%', v_lane;
    end if;
    v_train_event_n := greatest(1, floor(v_event_n * 0.70)::integer);
    v_cal_event_n := greatest(1, floor(v_event_n * 0.15)::integer);
    if v_train_event_n + v_cal_event_n >= v_event_n then
      v_cal_event_n := greatest(1, v_event_n - v_train_event_n - 1);
    end if;
    v_test_event_n := v_event_n - v_train_event_n - v_cal_event_n;

    create temp table tmp_fs_train on commit drop as
    select s.*, e.event_idx
    from tmp_fs_source s join tmp_fs_events e using(event_date,event_id)
    where s.lane=v_lane and e.event_idx <= v_train_event_n;

    select count(*) into v_source_rows from tmp_fs_source where lane=v_lane;
    select count(*) into v_train_rows from tmp_fs_train;
    select count(*) into v_cal_rows
      from tmp_fs_source s join tmp_fs_events e using(event_date,event_id)
      where s.lane=v_lane and e.event_idx > v_train_event_n
        and e.event_idx <= v_train_event_n + v_cal_event_n;
    select count(*) into v_test_rows
      from tmp_fs_source s join tmp_fs_events e using(event_date,event_id)
      where s.lane=v_lane and e.event_idx > v_train_event_n + v_cal_event_n;

    select encode(digest(convert_to(string_agg(
      md5(concat_ws('|',event_date::text,event_id,person_key,c1,c2,c3,c4,c5,c6,c7,c8,c9)),
      '' order by event_date,event_id,person_key), 'UTF8'),'sha256'),'hex')
      into v_source_hash
    from tmp_fs_source where lane=v_lane;

    -- Prior cohort state by whole event: current-event rows never leak into a baseline.
    create temp table tmp_fs_cohort_prior on commit drop as
    with event_agg as (
      select event_idx, count(*)::numeric n,
             sum(c1) s1,sum(c2) s2,sum(c3) s3,sum(c4) s4,sum(c5) s5,
             sum(c6) s6,sum(c7) s7,sum(c8) s8,sum(c9) s9
      from tmp_fs_train group by event_idx
    )
    select event_idx,
      sum(n) over(order by event_idx rows between unbounded preceding and 1 preceding) pn,
      sum(s1) over(order by event_idx rows between unbounded preceding and 1 preceding) ps1,
      sum(s2) over(order by event_idx rows between unbounded preceding and 1 preceding) ps2,
      sum(s3) over(order by event_idx rows between unbounded preceding and 1 preceding) ps3,
      sum(s4) over(order by event_idx rows between unbounded preceding and 1 preceding) ps4,
      sum(s5) over(order by event_idx rows between unbounded preceding and 1 preceding) ps5,
      sum(s6) over(order by event_idx rows between unbounded preceding and 1 preceding) ps6,
      sum(s7) over(order by event_idx rows between unbounded preceding and 1 preceding) ps7,
      sum(s8) over(order by event_idx rows between unbounded preceding and 1 preceding) ps8,
      sum(s9) over(order by event_idx rows between unbounded preceding and 1 preceding) ps9
    from event_agg;

    create temp table tmp_fs_player_prior on commit drop as
    with player_event as (
      select person_key,event_idx,count(*)::numeric n,
             sum(c1) s1,sum(c2) s2,sum(c3) s3,sum(c4) s4,sum(c5) s5,
             sum(c6) s6,sum(c7) s7,sum(c8) s8,sum(c9) s9
      from tmp_fs_train group by person_key,event_idx
    )
    select person_key,event_idx,
      sum(n) over(partition by person_key order by event_idx rows between unbounded preceding and 1 preceding) pn,
      sum(s1) over(partition by person_key order by event_idx rows between unbounded preceding and 1 preceding) ps1,
      sum(s2) over(partition by person_key order by event_idx rows between unbounded preceding and 1 preceding) ps2,
      sum(s3) over(partition by person_key order by event_idx rows between unbounded preceding and 1 preceding) ps3,
      sum(s4) over(partition by person_key order by event_idx rows between unbounded preceding and 1 preceding) ps4,
      sum(s5) over(partition by person_key order by event_idx rows between unbounded preceding and 1 preceding) ps5,
      sum(s6) over(partition by person_key order by event_idx rows between unbounded preceding and 1 preceding) ps6,
      sum(s7) over(partition by person_key order by event_idx rows between unbounded preceding and 1 preceding) ps7,
      sum(s8) over(partition by person_key order by event_idx rows between unbounded preceding and 1 preceding) ps8,
      sum(s9) over(partition by person_key order by event_idx rows between unbounded preceding and 1 preceding) ps9
    from player_event;

    create temp table tmp_fs_residual on commit drop as
    select t.event_date,t.event_id,t.person_key,t.player_name,
      t.c1 - case when pp.pn>0 then (pp.ps1 + (cp.ps1/cp.pn)*3)/(pp.pn+3) else cp.ps1/cp.pn end r1,
      t.c2 - case when pp.pn>0 then (pp.ps2 + (cp.ps2/cp.pn)*3)/(pp.pn+3) else cp.ps2/cp.pn end r2,
      t.c3 - case when pp.pn>0 then (pp.ps3 + (cp.ps3/cp.pn)*3)/(pp.pn+3) else cp.ps3/cp.pn end r3,
      t.c4 - case when pp.pn>0 then (pp.ps4 + (cp.ps4/cp.pn)*3)/(pp.pn+3) else cp.ps4/cp.pn end r4,
      t.c5 - case when pp.pn>0 then (pp.ps5 + (cp.ps5/cp.pn)*3)/(pp.pn+3) else cp.ps5/cp.pn end r5,
      t.c6 - case when pp.pn>0 then (pp.ps6 + (cp.ps6/cp.pn)*3)/(pp.pn+3) else cp.ps6/cp.pn end r6,
      t.c7 - case when pp.pn>0 then (pp.ps7 + (cp.ps7/cp.pn)*3)/(pp.pn+3) else cp.ps7/cp.pn end r7,
      t.c8 - case when pp.pn>0 then (pp.ps8 + (cp.ps8/cp.pn)*3)/(pp.pn+3) else cp.ps8/cp.pn end r8,
      t.c9 - case when pp.pn>0 then (pp.ps9 + (cp.ps9/cp.pn)*3)/(pp.pn+3) else cp.ps9/cp.pn end r9
    from tmp_fs_train t
    join tmp_fs_cohort_prior cp using(event_idx)
    left join tmp_fs_player_prior pp on pp.event_idx=t.event_idx and pp.person_key=t.person_key
    where cp.pn > 0;

    select count(*) into v_residual_total from tmp_fs_residual;
    if v_residual_total < 2000 then
      raise exception 'FANTASY_SCORE_RESIDUAL_HISTORY_INSUFFICIENT:%:%', v_lane,v_residual_total;
    end if;

    -- Full-train cohort baseline.
    if v_lane='MLB_HITTER' then
      select jsonb_build_object(
        'singles',avg(c1),'doubles',avg(c2),'triples',avg(c3),'home_runs',avg(c4),
        'runs',avg(c5),'rbi',avg(c6),'walks',avg(c7),'hbp',avg(c8),'stolen_bases',avg(c9)
      ) into v_cohort_mean from tmp_fs_train;
    else
      select jsonb_build_object(
        'wins',avg(c1),'quality_starts',avg(c2),'strikeouts',avg(c3),
        'outs_recorded',avg(c4),'earned_runs',avg(c5)
      ) into v_cohort_mean from tmp_fs_train;
    end if;

    -- Player names are accepted only when one Retrosheet identity owns the name.
    if v_lane='MLB_HITTER' then
      with name_counts as (
        select player_name,count(distinct person_key) n from tmp_fs_train group by player_name
      ), pa as (
        select person_key,player_name,count(*)::numeric n,
          avg(c1) a1,avg(c2) a2,avg(c3) a3,avg(c4) a4,avg(c5) a5,
          avg(c6) a6,avg(c7) a7,avg(c8) a8,avg(c9) a9
        from tmp_fs_train group by person_key,player_name
      )
      select coalesce(jsonb_object_agg(pa.player_name,jsonb_build_object(
        'singles',(pa.a1*pa.n + (v_cohort_mean->>'singles')::numeric*3)/(pa.n+3),
        'doubles',(pa.a2*pa.n + (v_cohort_mean->>'doubles')::numeric*3)/(pa.n+3),
        'triples',(pa.a3*pa.n + (v_cohort_mean->>'triples')::numeric*3)/(pa.n+3),
        'home_runs',(pa.a4*pa.n + (v_cohort_mean->>'home_runs')::numeric*3)/(pa.n+3),
        'runs',(pa.a5*pa.n + (v_cohort_mean->>'runs')::numeric*3)/(pa.n+3),
        'rbi',(pa.a6*pa.n + (v_cohort_mean->>'rbi')::numeric*3)/(pa.n+3),
        'walks',(pa.a7*pa.n + (v_cohort_mean->>'walks')::numeric*3)/(pa.n+3),
        'hbp',(pa.a8*pa.n + (v_cohort_mean->>'hbp')::numeric*3)/(pa.n+3),
        'stolen_bases',(pa.a9*pa.n + (v_cohort_mean->>'stolen_bases')::numeric*3)/(pa.n+3)
      ) order by pa.player_name),'{}'::jsonb) into v_player_means
      from pa join name_counts nc using(player_name) where nc.n=1;

      select jsonb_agg(jsonb_build_object(
        'singles',r1,'doubles',r2,'triples',r3,'home_runs',r4,'runs',r5,
        'rbi',r6,'walks',r7,'hbp',r8,'stolen_bases',r9
      ) order by sample_order), count(*)
      into v_residuals,v_residual_persisted
      from (
        select *, md5(event_date::text||'|'||event_id||'|'||person_key) sample_order
        from tmp_fs_residual
        order by sample_order,event_id,person_key
        limit 2000
      ) s;

      v_profile := jsonb_build_object(
        'profile_id','PRIZEPICKS_MLB_HITTER_FANTASY_SCORE_2025_07_11_VERIFIED_V1',
        'verified',true,
        'source_url','https://www.prizepicks.com/playbook-article/how-to-play-prizepicks-mlb-fantasy-scoring-system',
        'weights',jsonb_build_object('singles',3,'doubles',5,'triples',8,'home_runs',10,'runs',2,'rbi',2,'walks',2,'hbp',2,'stolen_bases',5)
      );
      v_model_family := 'MLB_HITTER_FANTASY_SCORE_EMPIRICAL_RESIDUAL_V1';
      v_stat_type := 'HITTER_FANTASY_SCORE';
      v_specialist := 'wow.mlb-hitter-fantasy-score-expert@1';
      v_transform := 'MLB_HITTER_RETROSPLITS_RESIDUAL_V1';
    else
      with name_counts as (
        select player_name,count(distinct person_key) n from tmp_fs_train group by player_name
      ), pa as (
        select person_key,player_name,count(*)::numeric n,
          avg(c1) a1,avg(c2) a2,avg(c3) a3,avg(c4) a4,avg(c5) a5
        from tmp_fs_train group by person_key,player_name
      )
      select coalesce(jsonb_object_agg(pa.player_name,jsonb_build_object(
        'wins',(pa.a1*pa.n + (v_cohort_mean->>'wins')::numeric*3)/(pa.n+3),
        'quality_starts',(pa.a2*pa.n + (v_cohort_mean->>'quality_starts')::numeric*3)/(pa.n+3),
        'strikeouts',(pa.a3*pa.n + (v_cohort_mean->>'strikeouts')::numeric*3)/(pa.n+3),
        'outs_recorded',(pa.a4*pa.n + (v_cohort_mean->>'outs_recorded')::numeric*3)/(pa.n+3),
        'earned_runs',(pa.a5*pa.n + (v_cohort_mean->>'earned_runs')::numeric*3)/(pa.n+3)
      ) order by pa.player_name),'{}'::jsonb) into v_player_means
      from pa join name_counts nc using(player_name) where nc.n=1;

      select jsonb_agg(jsonb_build_object(
        'wins',r1,'quality_starts',r2,'strikeouts',r3,'outs_recorded',r4,'earned_runs',r5
      ) order by sample_order), count(*)
      into v_residuals,v_residual_persisted
      from (
        select *, md5(event_date::text||'|'||event_id||'|'||person_key) sample_order
        from tmp_fs_residual
        order by sample_order,event_id,person_key
        limit 2000
      ) s;

      v_profile := jsonb_build_object(
        'profile_id','PRIZEPICKS_MLB_PITCHER_FANTASY_SCORE_2025_07_11_VERIFIED_V1',
        'verified',true,
        'source_url','https://www.prizepicks.com/playbook-article/how-to-play-prizepicks-mlb-fantasy-scoring-system',
        'weights',jsonb_build_object('wins',6,'quality_starts',4,'strikeouts',3,'outs_recorded',1,'earned_runs',-3)
      );
      v_model_family := 'MLB_PITCHER_FANTASY_SCORE_EMPIRICAL_RESIDUAL_V1';
      v_stat_type := 'PITCHER_FANTASY_SCORE';
      v_specialist := 'wow.mlb-pitcher-fantasy-score-expert@1';
      v_transform := 'MLB_PITCHER_RETROSPLITS_RESIDUAL_V1';
    end if;

    if v_residual_persisted <> 2000 then
      raise exception 'FANTASY_SCORE_RESIDUAL_SAMPLE_INVALID:%:%',v_lane,v_residual_persisted;
    end if;

    select greatest(1,ceil(max(fantasy_score)+10)) into v_line_max
    from tmp_fs_source where lane=v_lane;

    v_model_version := v_model_family||'_'||substr(v_source_hash,1,12)||'_R2000';
    v_payload := jsonb_build_object(
      'artifact_schema_version','FANTASY_SCORE_CANDIDATE_ARTIFACT_V1',
      'lane',v_lane,
      'player_means',v_player_means,
      'cohort_means',jsonb_build_object(v_lane,v_cohort_mean),
      'residual_vectors',jsonb_build_object(v_lane,v_residuals),
      'scoring_profile',v_profile,
      'identity_snapshot',jsonb_build_object(
        'provider','RETROSHEET',
        'cache_id',v_identity_cache_id,
        'sha256',v_identity_sha,
        'source_url','http://www.retrosheet.org/downloads/csvplayers.html'
      ),
      'fit_contract',jsonb_build_object(
        'whole_event_chronological_split',true,
        'train_fraction',0.70,
        'calibration_fraction',0.15,
        'untouched_test_required',true,
        'player_shrinkage_prior_games',3,
        'residual_sampling','DETERMINISTIC_MD5_ORDER_FIRST_2000',
        'training_source','wow_mlb_retrosplits_rows',
        'season_phase','R'
      )
    );
    v_artifact_checksum := encode(digest(convert_to(v_payload::text,'UTF8'),'sha256'),'hex');

    if p_activate then
      update public.wow_prop_fitted_model_artifacts
      set candidate_research_active=false
      where upper(sport)='MLB'
        and upper(stat_type)=v_stat_type
        and feature_schema_version='PROP_FEATURES_V1'
        and candidate_research_active=true;
    end if;

    insert into public.wow_prop_fitted_model_artifacts(
      provider_identity,model_family,model_artifact_version,calibrator_version,
      sport,stat_type,feature_schema_version,feature_transform_version,specialist_version,
      certification_id,lifecycle_state,training_dataset_hash,training_code_sha,
      artifact_checksum,artifact_format,artifact_payload,supported_line_min,supported_line_max,
      training_rows,validation_metrics,promoted,active,probability_publishable,can_execute,
      candidate_research_active
    ) values (
      'WOW_PROP_FITTED_MODEL_V1',v_model_family,v_model_version,'UNAVAILABLE_CANDIDATE_ONLY',
      'MLB',v_stat_type,'PROP_FEATURES_V1',v_transform,v_specialist,
      'CANDIDATE-NOT-CERTIFIED-'||substr(v_source_hash,1,16),'CANDIDATE',v_source_hash,p_training_code_sha,
      v_artifact_checksum,'FANTASY_SCORE_CANDIDATE_JSON_V1',v_payload,0,v_line_max,
      v_train_rows,
      jsonb_build_object(
        'candidate_only',true,
        'source_rows',v_source_rows,
        'source_events',v_event_n,
        'train_rows',v_train_rows,
        'calibration_rows',v_cal_rows,
        'untouched_test_rows',v_test_rows,
        'train_events',v_train_event_n,
        'calibration_events',v_cal_event_n,
        'untouched_test_events',v_test_event_n,
        'identity_source_sha256',v_identity_sha,
        'identity_cache_id',v_identity_cache_id,
        'player_mean_keys',jsonb_object_length(v_player_means),
        'residual_vectors_total',v_residual_total,
        'residual_vectors_persisted',v_residual_persisted,
        'calibration_status','BLOCKED_NO_CERTIFIED_EXACT_LINE_CALIBRATION_ARTIFACT',
        'certification_status','CANDIDATE_ONLY',
        'probability_publishable',false,
        'rank_eligible',false,
        'can_execute',false,
        'official_scoring_profile',v_profile->>'profile_id'
      ),
      false,false,false,false,p_activate
    )
    on conflict(provider_identity,model_artifact_version) do update set
      training_code_sha=excluded.training_code_sha,
      artifact_checksum=excluded.artifact_checksum,
      artifact_payload=excluded.artifact_payload,
      validation_metrics=excluded.validation_metrics,
      candidate_research_active=excluded.candidate_research_active,
      promoted=false,
      active=false,
      probability_publishable=false,
      can_execute=false
    returning artifact_id into v_artifact_id;

    v_results := v_results || jsonb_build_array(jsonb_build_object(
      'lane',v_lane,
      'artifact_id',v_artifact_id,
      'model_artifact_version',v_model_version,
      'training_dataset_hash',v_source_hash,
      'training_rows',v_train_rows,
      'candidate_research_active',p_activate,
      'probability_publishable',false,
      'rank_eligible',false,
      'can_execute',false
    ));
  end loop;

  return jsonb_build_object(
    'status','COMPLETED',
    'identity_cache_id',v_identity_cache_id,
    'identity_sha256',v_identity_sha,
    'artifacts',v_results,
    'probability_publishable',false,
    'rank_eligible',false,
    'can_execute',false
  );
end;
$$;

revoke all on function public.wow_v17_build_mlb_fantasy_score_candidates(text,boolean) from public;
revoke all on function public.wow_v17_build_mlb_fantasy_score_candidates(text,boolean) from anon, authenticated;
grant execute on function public.wow_v17_build_mlb_fantasy_score_candidates(text,boolean) to service_role;

comment on function public.wow_v17_build_mlb_fantasy_score_candidates(text,boolean) is
  'Evidence-only V17 MLB hitter/pitcher Fantasy Score candidate builder. Never grants publication, ranking, or execution authority.';
