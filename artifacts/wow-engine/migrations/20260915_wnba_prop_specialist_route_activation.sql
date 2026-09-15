-- WOW V17: declare exact WNBA fitted-prop specialist ownership without
-- granting probability publication or execution authority.
--
-- This migration only advances route ownership. /score-pick-request still
-- requires PROP_PROBABILITY=AVAILABLE plus an exact promoted/active artifact
-- from wow_prop_certified_model_artifact before acquisition/scoring can run.
-- Candidate WNBA artifacts therefore continue to terminate MODEL_UNAVAILABLE
-- until the governed lifecycle promotes them.

create or replace function public.wow_controlling_specialist(
  p_sport text,
  p_prop_type text
)
returns jsonb
language plpgsql
immutable
set search_path to ''
as $function$
declare
  v_sport text := upper(trim(coalesce(p_sport,'')));
  v_key text := upper(regexp_replace(trim(coalesce(p_prop_type,'')), '[^A-Za-z0-9]+', '_', 'g'));
  v_controller text;
  v_supporting jsonb := '[]'::jsonb;
begin
  v_key := trim(both '_' from v_key);

  if v_sport='MLB' and v_key in (
    '1ST_INNING_PITCHES_THROWN',
    'FIRST_INNING_PITCHES_THROWN',
    '1ST_INNING_PITCH_COUNT',
    'FIRST_INNING_PITCH_COUNT'
  ) then
    v_controller := 'wow.mlb-first-inning-pitch-count-expert';
    v_supporting := jsonb_build_array('wow.mlb-pitcher-failure-path-expert');

  elsif v_sport='MLB' and v_key='PITCHING_OUTS' then
    v_controller := 'wow.mlb-pitcher-outs-workload-expert';

  elsif v_sport='MLB' and v_key in ('STRIKES_THROWN','BALLS_THROWN') then
    v_controller := 'wow.mlb-pitcher-pitch-composition-expert';

  elsif v_sport='MLB' and v_key='PLATE_APPEARANCES' then
    v_controller := 'wow.mlb-batter-plate-appearances-expert';

  elsif v_sport='MLB' and (
    v_key like '%STRIKEOUT%'
    or v_key like '%PITCH_COUNT%'
    or v_key like '%WALK%'
    or v_key like '%HIT_ALLOWED%'
    or v_key like '%RUN_ALLOWED%'
  ) then
    v_controller := 'wow.mlb-pitcher-failure-path-expert';

  elsif v_sport='WNBA' and v_key in (
    'POINTS',
    'REBOUNDS',
    'ASSISTS',
    'THREE_POINTERS_MADE'
  ) then
    v_controller := 'wow.wnba-player-prop-probability-expert';

  else
    v_controller := 'MODEL_UNAVAILABLE';
  end if;

  return jsonb_build_object(
    'sport', v_sport,
    'canonical_prop_type', v_key,
    'controlling_specialist', v_controller,
    'supporting_specialists', v_supporting,
    'min_event_tree_simulations',
      case when v_controller='wow.mlb-first-inning-pitch-count-expert' then 25000 else null end,
    'can_execute', false
  );
end;
$function$;

comment on function public.wow_controlling_specialist(text,text) is
'V17 controlling-specialist router. Certified MLB prop families and exact WNBA fitted-candidate families route to their owning specialist; exact fitted-artifact preflight remains mandatory and unsupported/candidate-only routes fail closed. can_execute=false.';
