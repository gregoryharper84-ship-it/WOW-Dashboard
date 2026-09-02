-- WOW v17: add optional opponent-lineup evidence to the governed prop
-- evidence snapshot table and its read RPC.
--
-- Context (postmortem patch WOW-PATCH-2026-09-02, GitHub issues #116/#119 --
-- the 2026-09-01 Manaea strikeout-suppression miss): the MLB pitcher
-- strikeout adapter (prop_model_adapters.mlb_pitcher_so_failure_path_nb_v1_adapter)
-- already has a reviewed, fitted opponent-strikeout-rate multiplier
-- (`opp_factor`) but nothing upstream ever populated it, because
-- `wow_prop_evidence_snapshots` had no column to hold opponent-lineup
-- evidence and `wow_prop_evidence_snapshot()` had nothing to return. This
-- migration adds a single, optional, additive `opponent_context` jsonb
-- column and threads it through the read RPC unchanged (no new validation
-- gate -- absence must not become a capability/evidence blocker, since most
-- rows and every non-pitcher-strikeout stat type will never populate it).
--
-- Backward compatible: the column is nullable with no default-breaking
-- effect on existing rows, and the RPC only adds one key to its existing
-- `PROP_EVIDENCE_READY` response object. Every other branch/response of the
-- function (not-found, identity-mismatch, acquisition-incomplete) is
-- untouched. The application-side write path
-- (pick_request_runtime._snapshot_payload) only sends this key when a
-- caller actually supplies opponent_context, so an unapplied migration does
-- not break any existing caller's upsert.
--
-- Verified via BEGIN; ...; ROLLBACK; against the live "wow-engine-validation"
-- Supabase project (iczfhsmjrrafhvcpmqhr) on 2026-09-02. NOT applied to
-- production by this change -- schema authorization is separate from
-- implementation.

alter table public.wow_prop_evidence_snapshots
  add column if not exists opponent_context jsonb;

create or replace function public.wow_prop_evidence_snapshot(p_source_snapshot_id uuid, p_event_id text, p_sport text, p_player text, p_stat_type text, p_line numeric)
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
        'probability_publishable', false,
        'can_execute', false
    );
end;
$function$;
