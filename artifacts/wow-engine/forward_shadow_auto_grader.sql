-- WOW governed probability engine — forward-shadow automatic grading
-- Applied to Supabase project wow-engine-validation on 2026-08-28.
--
-- Purpose:
--   * grade only already-scored forward-shadow MLB events
--   * use the official MLB Stats API only after the game is Final
--   * write through the existing immutable grading function
--   * recompute Calibration Health after every grading pass
--
-- This does NOT flip G01-G11, make probabilities publishable, or change
-- dry-run governance. can_execute remains false.

create extension if not exists pg_cron;

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
    select shadow_event_id, official_event_id, event_start_time
    from public.wow_mlb_forward_shadow_events se
    where se.result_status = 'PENDING'
      and se.model_score_status like 'SHADOW_SCORED%'
      and now() >= se.event_start_time + interval '2 hours'
      and exists (
        select 1 from public.wow_mlb_forward_score_snapshots ss
        where ss.shadow_event_id = se.shadow_event_id
      )
    order by se.event_start_time
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
          'shadow_event_id', e.shadow_event_id,
          'status', 'HTTP_ERROR',
          'http_status', r.status
        ));
        continue;
      end if;

      v_body := r.content::jsonb;
      v_state := v_body #>> '{gameData,status,abstractGameState}';
      if v_state <> 'Final' then
        v_not_final := v_not_final + 1;
        v_results := v_results || jsonb_build_array(jsonb_build_object(
          'shadow_event_id', e.shadow_event_id,
          'status', 'NOT_FINAL',
          'game_state', v_state
        ));
        continue;
      end if;

      v_home_score := nullif(
        v_body #>> '{liveData,linescore,teams,home,runs}', ''
      )::integer;
      v_away_score := nullif(
        v_body #>> '{liveData,linescore,teams,away,runs}', ''
      )::integer;

      if v_home_score is null
         or v_away_score is null
         or v_home_score = v_away_score then
        v_errors := v_errors + 1;
        v_results := v_results || jsonb_build_array(jsonb_build_object(
          'shadow_event_id', e.shadow_event_id,
          'status', 'FINAL_SCORE_INVALID',
          'home_score', v_home_score,
          'away_score', v_away_score
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

      if coalesce(v_grade->>'status', '') = 'GRADED' then
        v_graded := v_graded + 1;
      end if;

      v_results := v_results || jsonb_build_array(jsonb_build_object(
        'shadow_event_id', e.shadow_event_id,
        'status', v_grade->>'status',
        'winner', v_winner,
        'home_score', v_home_score,
        'away_score', v_away_score
      ));
    exception when others then
      v_errors := v_errors + 1;
      v_results := v_results || jsonb_build_array(jsonb_build_object(
        'shadow_event_id', e.shadow_event_id,
        'status', 'ERROR',
        'error', sqlerrm
      ));
    end;
  end loop;

  v_health := public.wow_mlb_v2d_assess_calibration_health();

  return jsonb_build_object(
    'status', 'COMPLETE',
    'checked', v_checked,
    'graded', v_graded,
    'not_final', v_not_final,
    'errors', v_errors,
    'results', v_results,
    'calibration_health', v_health,
    'probability_publishable', false,
    'can_execute', false
  );
end;
$function$;

select cron.schedule(
  'wow-mlb-forward-shadow-auto-grade',
  '*/15 * * * *',
  $$select public.wow_mlb_forward_auto_grade_completed();$$
);
