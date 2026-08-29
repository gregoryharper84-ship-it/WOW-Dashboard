-- WOW MLB forward-shadow unique-event calibration repair — 2026-08-28
--
-- A single official MLB game may legitimately appear in multiple timestamped
-- pregame snapshots so runtime scoring can use fresh inputs. Those repeated
-- snapshots must not become repeated calibration outcomes. This repair keeps
-- the full immutable snapshot/score audit trail while defining exactly one
-- canonical calibration prediction per (spec_id, official_event_id): the
-- earliest qualified pregame scored shadow. Outcome auto-grading and
-- Calibration Health both use that same canonical rule.
--
-- Safety invariants remain unchanged: no probability publication, no
-- production-readiness promotion, and can_execute=false.

create or replace function public.wow_mlb_forward_grade_shadow_event(
    p_shadow_event_id uuid,
    p_official_winner text,
    p_home_score integer,
    p_away_score integer,
    p_settlement_source text default null
) returns jsonb
language plpgsql
set search_path = ''
as $function$
declare
  e public.wow_mlb_forward_shadow_events%rowtype;
  s public.wow_mlb_forward_score_snapshots%rowtype;
  v_home_win boolean;
  v_predicted_side text;
  v_actual_outcome boolean;
  v_brier double precision;
  v_logloss double precision;
  v_grade_id uuid;
begin
  if p_official_winner not in ('HOME','AWAY') then
    raise exception 'official_winner must be HOME or AWAY';
  end if;
  if p_home_score is null or p_away_score is null or p_home_score < 0 or p_away_score < 0 then
    raise exception 'scores must be non-negative';
  end if;

  select * into e
  from public.wow_mlb_forward_shadow_events
  where shadow_event_id = p_shadow_event_id;
  if not found then raise exception 'shadow event not found'; end if;

  if now() < e.event_start_time then
    return jsonb_build_object('status','BLOCKED','reason','EVENT_NOT_YET_STARTED','can_execute',false);
  end if;

  -- Runtime freshness can create multiple pregame shadows for one official
  -- game. Only the earliest qualified scored shadow may become calibration
  -- evidence for that game.
  if exists (
    select 1
    from public.wow_mlb_forward_shadow_events e2
    where e2.spec_id = e.spec_id
      and e2.official_event_id = e.official_event_id
      and e2.shadow_event_id <> e.shadow_event_id
      and e2.feature_hydration_status = 'PASS'
      and e2.starter_identity_provenance = 'PREGAME_TIMESTAMPED'
      and e2.snapshot_timestamp is not null
      and e2.snapshot_timestamp < e2.event_start_time
      and e2.model_score_status like 'SHADOW_SCORED%'
      and exists (
        select 1
        from public.wow_mlb_forward_score_snapshots sx
        where sx.shadow_event_id = e2.shadow_event_id
          and sx.spec_id = e2.spec_id
      )
      and (
        e2.snapshot_timestamp < e.snapshot_timestamp
        or (
          e2.snapshot_timestamp = e.snapshot_timestamp
          and e2.shadow_event_id::text < e.shadow_event_id::text
        )
      )
  ) then
    return jsonb_build_object(
      'status','BLOCKED',
      'reason','NONCANONICAL_DUPLICATE_EVENT_SHADOW',
      'official_event_id',e.official_event_id,
      'can_execute',false
    );
  end if;

  select * into s
  from public.wow_mlb_forward_score_snapshots
  where shadow_event_id = p_shadow_event_id
  order by created_at asc
  limit 1;
  if not found then
    return jsonb_build_object('status','BLOCKED','reason','NO_FROZEN_PREDICTION_TO_GRADE','can_execute',false);
  end if;

  if exists (
    select 1
    from public.wow_mlb_forward_shadow_grades
    where score_snapshot_id = s.score_snapshot_id
  ) then
    return jsonb_build_object('status','BLOCKED','reason','ALREADY_GRADED','can_execute',false);
  end if;

  v_home_win := (p_official_winner = 'HOME');
  v_predicted_side := case
    when s.calibrated_home_probability >= s.calibrated_away_probability then 'HOME'
    else 'AWAY'
  end;
  v_actual_outcome := (v_predicted_side = p_official_winner);
  v_brier := power(
    (case when v_home_win then 1.0 else 0.0 end) - s.calibrated_home_probability,
    2
  );
  if s.calibrated_home_probability > 0 and s.calibrated_home_probability < 1 then
    v_logloss := -(
      (case when v_home_win then 1.0 else 0.0 end) * ln(s.calibrated_home_probability)
      + (case when v_home_win then 0.0 else 1.0 end) * ln(1 - s.calibrated_home_probability)
    );
  else
    v_logloss := null;
  end if;

  insert into public.wow_mlb_forward_shadow_grades (
    shadow_event_id,
    score_snapshot_id,
    spec_id,
    prediction_probability,
    predicted_side,
    official_winner,
    actual_outcome,
    home_score,
    away_score,
    brier_contribution,
    log_loss_contribution,
    prediction_timestamp,
    outcome_timestamp,
    model_spec_id,
    settlement_source
  ) values (
    p_shadow_event_id,
    s.score_snapshot_id,
    s.spec_id,
    s.calibrated_home_probability,
    v_predicted_side,
    p_official_winner,
    v_actual_outcome,
    p_home_score,
    p_away_score,
    v_brier,
    v_logloss,
    s.model_timestamp,
    now(),
    s.spec_id,
    p_settlement_source
  ) returning grade_id into v_grade_id;

  update public.wow_mlb_forward_shadow_events
  set result_status = 'GRADED',
      home_win = v_home_win
  where shadow_event_id = p_shadow_event_id;

  return jsonb_build_object(
    'status','GRADED',
    'grade_id',v_grade_id,
    'predicted_side',v_predicted_side,
    'actual_outcome',v_actual_outcome,
    'brier_contribution',v_brier,
    'log_loss_contribution',v_logloss,
    'can_execute',false
  );
end;
$function$;

create or replace function public.wow_mlb_v2d_assess_calibration_health(p_spec_id uuid default null)
returns jsonb
language plpgsql
set search_path = ''
as $function$
declare
  v_spec_id uuid;
  v_captured_n integer;
  v_eligible_n integer;
  v_graded_n integer;
  v_pending_n integer;
  v_provenance_status text;
  v_forward_status text;
  v_blockers text[] := '{}';
  v_health_status text;
begin
  v_spec_id := coalesce(
    p_spec_id,
    (
      select spec_id
      from public.wow_mlb_v2d_frozen_spec
      where status = 'RESEARCH_FROZEN'
      order by created_at desc
      limit 1
    )
  );
  if v_spec_id is null then
    raise exception 'no frozen v2d spec available';
  end if;

  -- Sample size is official games, not raw snapshot rows. Multiple snapshots
  -- of one game remain preserved in the underlying audit tables.
  select count(distinct official_event_id)
  into v_captured_n
  from public.wow_mlb_forward_shadow_events
  where spec_id = v_spec_id;

  with qualified as (
    select
      e.shadow_event_id,
      e.official_event_id,
      row_number() over (
        partition by e.official_event_id
        order by e.snapshot_timestamp asc, e.shadow_event_id::text asc
      ) as event_rank
    from public.wow_mlb_forward_shadow_events e
    where e.spec_id = v_spec_id
      and e.feature_hydration_status = 'PASS'
      and e.starter_identity_provenance = 'PREGAME_TIMESTAMPED'
      and e.snapshot_timestamp is not null
      and e.snapshot_timestamp < e.event_start_time
      and e.model_score_status like 'SHADOW_SCORED%'
      and exists (
        select 1
        from public.wow_mlb_forward_score_snapshots s
        where s.shadow_event_id = e.shadow_event_id
          and s.spec_id = v_spec_id
      )
  )
  select count(*)
  into v_eligible_n
  from qualified
  where event_rank = 1;

  with qualified as (
    select
      e.shadow_event_id,
      e.official_event_id,
      row_number() over (
        partition by e.official_event_id
        order by e.snapshot_timestamp asc, e.shadow_event_id::text asc
      ) as event_rank
    from public.wow_mlb_forward_shadow_events e
    where e.spec_id = v_spec_id
      and e.feature_hydration_status = 'PASS'
      and e.starter_identity_provenance = 'PREGAME_TIMESTAMPED'
      and e.snapshot_timestamp is not null
      and e.snapshot_timestamp < e.event_start_time
      and e.model_score_status like 'SHADOW_SCORED%'
      and exists (
        select 1
        from public.wow_mlb_forward_score_snapshots s
        where s.shadow_event_id = e.shadow_event_id
          and s.spec_id = v_spec_id
      )
  )
  select count(*)
  into v_graded_n
  from qualified q
  where q.event_rank = 1
    and exists (
      select 1
      from public.wow_mlb_forward_shadow_grades g
      join public.wow_mlb_forward_score_snapshots s2
        on s2.score_snapshot_id = g.score_snapshot_id
      where s2.shadow_event_id = q.shadow_event_id
        and s2.spec_id = v_spec_id
    );

  v_pending_n := v_eligible_n - v_graded_n;
  v_provenance_status := case
    when v_eligible_n > 0 then 'AVAILABLE'
    else 'UNAVAILABLE'
  end;

  v_forward_status := case
    when v_captured_n = 0 then 'NOT_STARTED'
    when v_eligible_n = 0 then 'COLLECTING'
    when v_graded_n = 0 then 'PREDICTIONS_AVAILABLE'
    when v_graded_n < v_eligible_n then 'PARTIALLY_GRADED'
    else 'SUFFICIENT_FOR_REVIEW'
  end;

  if v_forward_status <> 'SUFFICIENT_FOR_REVIEW' then
    v_blockers := array_append(v_blockers, 'FORWARD_SHADOW_NOT_COMPLETED');
  end if;
  if v_provenance_status <> 'AVAILABLE' then
    v_blockers := array_append(v_blockers, 'TIMESTAMPED_PREGAME_STARTER_PROVENANCE_UNAVAILABLE');
  end if;
  if v_graded_n = 0 then
    v_blockers := array_append(v_blockers, 'FRESH_POST_FREEZE_OUTCOME_HOLDOUT_UNAVAILABLE');
  end if;

  v_health_status := case
    when cardinality(v_blockers) = 0 then 'PASS'
    else 'BLOCKED'
  end;

  update public.wow_mlb_v2d_calibration_health
  set assessed_at = now(),
      forward_shadow_status = v_forward_status,
      forward_shadow_n = v_captured_n,
      eligible_shadow_n = v_eligible_n,
      graded_shadow_n = v_graded_n,
      pending_shadow_n = v_pending_n,
      timestamped_pregame_provenance_status = v_provenance_status,
      calibration_health_status = v_health_status,
      blockers = v_blockers,
      probability_publishable = false,
      can_execute = false
  where spec_id = v_spec_id;

  return jsonb_build_object(
    'spec_id',v_spec_id,
    'captured_shadow_n',v_captured_n,
    'eligible_shadow_n',v_eligible_n,
    'graded_shadow_n',v_graded_n,
    'pending_shadow_n',v_pending_n,
    'forward_shadow_status',v_forward_status,
    'timestamped_pregame_provenance_status',v_provenance_status,
    'calibration_health_status',v_health_status,
    'blockers',to_jsonb(v_blockers),
    'probability_publishable',false,
    'can_execute',false
  );
end;
$function$;

create or replace function public.wow_mlb_forward_auto_grade_completed()
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  e record;
  r extensions.http_response;
  v_url text;
  v_body jsonb;
  v_state text;
  v_home_score integer;
  v_away_score integer;
  v_winner text;
  v_grade jsonb;
  v_health jsonb;
  v_checked integer := 0;
  v_graded integer := 0;
  v_not_final integer := 0;
  v_errors integer := 0;
  v_results jsonb := '[]'::jsonb;
begin
  for e in
    with qualified as (
      select
        se.shadow_event_id,
        se.spec_id,
        se.official_event_id,
        se.event_start_time,
        se.result_status,
        se.snapshot_timestamp,
        row_number() over (
          partition by se.spec_id, se.official_event_id
          order by se.snapshot_timestamp asc, se.shadow_event_id::text asc
        ) as event_rank
      from public.wow_mlb_forward_shadow_events se
      where se.feature_hydration_status = 'PASS'
        and se.starter_identity_provenance = 'PREGAME_TIMESTAMPED'
        and se.snapshot_timestamp is not null
        and se.snapshot_timestamp < se.event_start_time
        and se.model_score_status like 'SHADOW_SCORED%'
        and exists (
          select 1
          from public.wow_mlb_forward_score_snapshots ss
          where ss.shadow_event_id = se.shadow_event_id
            and ss.spec_id = se.spec_id
        )
    )
    select shadow_event_id, official_event_id, event_start_time
    from qualified
    where event_rank = 1
      and result_status = 'PENDING'
      and now() >= event_start_time + interval '2 hours'
    order by event_start_time, official_event_id
  loop
    v_checked := v_checked + 1;
    begin
      v_url := format(
        'https://statsapi.mlb.com/api/v1.1/game/%s/feed/live',
        e.official_event_id
      );
      r := extensions.http_get(v_url::varchar);

      if r.status <> 200 then
        v_errors := v_errors + 1;
        v_results := v_results || jsonb_build_array(jsonb_build_object(
          'shadow_event_id',e.shadow_event_id,
          'status','HTTP_ERROR',
          'http_status',r.status
        ));
        continue;
      end if;

      v_body := r.content::jsonb;
      v_state := v_body #>> '{gameData,status,abstractGameState}';
      if v_state <> 'Final' then
        v_not_final := v_not_final + 1;
        v_results := v_results || jsonb_build_array(jsonb_build_object(
          'shadow_event_id',e.shadow_event_id,
          'status','NOT_FINAL',
          'game_state',v_state
        ));
        continue;
      end if;

      v_home_score := nullif(
        v_body #>> '{liveData,linescore,teams,home,runs}',
        ''
      )::integer;
      v_away_score := nullif(
        v_body #>> '{liveData,linescore,teams,away,runs}',
        ''
      )::integer;

      if v_home_score is null
         or v_away_score is null
         or v_home_score = v_away_score then
        v_errors := v_errors + 1;
        v_results := v_results || jsonb_build_array(jsonb_build_object(
          'shadow_event_id',e.shadow_event_id,
          'status','FINAL_SCORE_INVALID'
        ));
        continue;
      end if;

      v_winner := case
        when v_home_score > v_away_score then 'HOME'
        else 'AWAY'
      end;

      v_grade := public.wow_mlb_forward_grade_shadow_event(
        e.shadow_event_id,
        v_winner,
        v_home_score,
        v_away_score,
        v_url
      );

      if coalesce(v_grade->>'status','') = 'GRADED' then
        v_graded := v_graded + 1;
      end if;

      v_results := v_results || jsonb_build_array(jsonb_build_object(
        'shadow_event_id',e.shadow_event_id,
        'status',v_grade->>'status'
      ));
    exception when others then
      v_errors := v_errors + 1;
      v_results := v_results || jsonb_build_array(jsonb_build_object(
        'shadow_event_id',e.shadow_event_id,
        'status','ERROR',
        'error_type',sqlstate
      ));
    end;
  end loop;

  v_health := public.wow_mlb_v2d_assess_calibration_health();

  return jsonb_build_object(
    'status','COMPLETE',
    'checked',v_checked,
    'graded',v_graded,
    'not_final',v_not_final,
    'errors',v_errors,
    'results',v_results,
    'calibration_health',v_health,
    'probability_publishable',false,
    'can_execute',false
  );
end;
$function$;
