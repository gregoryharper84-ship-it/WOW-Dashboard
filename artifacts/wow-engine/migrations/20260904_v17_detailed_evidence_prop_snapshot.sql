-- WOW V17 detailed evidence envelope persistence for governed prop snapshots.
--
-- Additive/backward-compatible only:
-- - existing rows remain valid (both columns nullable);
-- - absence of detailed evidence is never a new acquisition blocker;
-- - the existing immutable prop evidence RPC keeps all current readiness gates;
-- - detailed evidence is returned unchanged for the application-side V17
--   feature router, which still grants numerical authority only to the exact
--   controlling specialist/model adapter.
--
-- No probability, calibration, edge, rank, or terminal field is added here.
-- can_execute remains application-governed false.

alter table public.wow_prop_evidence_snapshots
  add column if not exists detailed_evidence jsonb,
  add column if not exists detailed_evidence_fingerprint text;

create or replace function public.wow_prop_evidence_snapshot(
    p_source_snapshot_id uuid,
    p_event_id text,
    p_sport text,
    p_player text,
    p_stat_type text,
    p_line numeric
)
returns jsonb
language plpgsql
stable
set search_path to 'public'
as $function$
declare
    v_row public.wow_prop_evidence_snapshots%rowtype;
    v_game_count integer;
    v_box_count integer;
    v_game_numeric boolean;
    v_box_objects boolean;
    v_role_status text;
    v_opportunity_status text;
begin
    select * into v_row
    from public.wow_prop_evidence_snapshots
    where source_snapshot_id = p_source_snapshot_id;

    if not found then
        return jsonb_build_object(
            'ok', false,
            'code', 'PROP_EVIDENCE_SNAPSHOT_NOT_FOUND',
            'hydration_status', 'FAILED',
            'probability_publishable', false,
            'can_execute', false
        );
    end if;

    if v_row.event_id <> p_event_id
       or upper(v_row.sport) <> upper(p_sport)
       or v_row.player <> p_player
       or upper(v_row.stat_type) <> upper(p_stat_type)
       or v_row.line <> p_line then
        return jsonb_build_object(
            'ok', false,
            'code', 'PROP_EVIDENCE_IDENTITY_MISMATCH',
            'hydration_status', 'FAILED',
            'probability_publishable', false,
            'can_execute', false
        );
    end if;

    select count(*) into v_game_count from jsonb_array_elements(v_row.game_log);
    select count(*) into v_box_count from jsonb_array_elements(v_row.box_score_log);

    select coalesce(bool_and(jsonb_typeof(value) = 'number'), false)
      into v_game_numeric
      from jsonb_array_elements(v_row.game_log);

    select coalesce(bool_and(jsonb_typeof(value) = 'object'), false)
      into v_box_objects
      from jsonb_array_elements(v_row.box_score_log);

    v_role_status := coalesce(v_row.role_status->>'status', v_row.role_status->>'role', '');
    v_opportunity_status := coalesce(v_row.opportunity_ledger->>'status', v_row.opportunity_ledger->>'gate_label', '');

    if v_row.hydration_status <> 'PASS'
       or v_game_count < 10
       or v_box_count < 10
       or not v_game_numeric
       or not v_box_objects
       or v_row.role_timestamp is null
       or v_role_status = ''
       or upper(v_opportunity_status) not in ('PASS','COMPLETE','READY')
       or cardinality(v_row.blockers) > 0 then
        return jsonb_build_object(
            'ok', false,
            'code', 'RUN_INVALID_ACQUISITION_INCOMPLETE',
            'hydration_status', v_row.hydration_status,
            'game_log_count', v_game_count,
            'box_score_log_count', v_box_count,
            'game_log_numeric', v_game_numeric,
            'box_score_log_objects', v_box_objects,
            'role_timestamp', v_row.role_timestamp,
            'blockers', to_jsonb(v_row.blockers),
            'probability_publishable', false,
            'can_execute', false
        );
    end if;

    return jsonb_build_object(
        'ok', true,
        'code', 'PROP_EVIDENCE_READY',
        'source_snapshot_id', v_row.source_snapshot_id,
        'captured_at', v_row.captured_at,
        'event_id', v_row.event_id,
        'event_start_time', v_row.event_start_time,
        'sport', v_row.sport,
        'player', v_row.player,
        'stat_type', v_row.stat_type,
        'line', v_row.line,
        'game_log', v_row.game_log,
        'box_score_log', v_row.box_score_log,
        'role_status', v_row.role_status,
        'role_timestamp', v_row.role_timestamp,
        'opportunity_ledger', v_row.opportunity_ledger,
        'source_timestamps', v_row.source_timestamps,
        'evidence_version', v_row.evidence_version,
        'hydration_status', v_row.hydration_status,
        'opponent_context', v_row.opponent_context,
        'detailed_evidence', v_row.detailed_evidence,
        'detailed_evidence_fingerprint', v_row.detailed_evidence_fingerprint,
        'probability_publishable', false,
        'can_execute', false
    );
end;
$function$;
