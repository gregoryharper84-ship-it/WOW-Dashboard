-- WOW V17 MLB context-only weather final-refresh repair.
--
-- Defect: the shared environmental writer hashed retrieval metadata and used a
-- different payload shape from the MLB scoring-evidence writer. Final refresh
-- compared writer-specific payload hashes as though every evidence kind were a
-- certified numeric model input, so unchanged official weather could force
-- WEATHER_STATUS_MATERIAL_CHANGE_AFTER_MODEL / RERUN_REQUIRED.
--
-- Contract:
--   * identical official weather receives a stable semantic hash independent of
--     retrieval clock / writer metadata;
--   * weather hash differences are non-material to the fitted probability only
--     when BOTH score-time and refresh-time evidence explicitly declare the
--     weather context-only and neither applies a numeric probability adjustment;
--   * missing/stale weather still fails closed;
--   * missing score-time weather evidence still requires a rerun;
--   * every non-weather evidence mismatch remains material;
--   * future certified/numeric weather remains material because it will not
--     satisfy the context-only guard;
--   * no fitted model, coefficient, calibration, lower bound, threshold, route,
--     terminal-authority, publication-authority, or execution behavior changes.
-- can_execute remains false.

-- Patch the current hardened shared-environment provider in place so this
-- migration composes with earlier live-acceptance hardening rather than
-- reinstalling an older function body.
do $do$
declare
  d text;
  prior text;
begin
  select pg_get_functiondef(
    'public.wow_v17_hydrate_shared_environmental_evidence(uuid,uuid)'::regprocedure
  ) into d;

  prior := d;
  d := replace(
    d,
    $q$    'retrieved_at',v_now,
    'model_input_semantics','CONTEXT_ONLY_UNLESS_CERTIFIED_FEATURE_PRESENT',$q$,
    $q$    'semantic_hash_version','WOW_V17_SHARED_ENVIRONMENTAL_SEMANTIC_HASH_V1',
    'retrieved_at',v_now,
    'model_input_semantics','CONTEXT_ONLY_UNLESS_CERTIFIED_FEATURE_PRESENT',$q$
  );
  if d = prior then
    raise exception 'V17_SHARED_ENVIRONMENT_SEMANTIC_HASH_VERSION_PATCH_NOT_APPLIED';
  end if;

  prior := d;
  d := replace(
    d,
    $q$v_hash := md5(v_payload::text);$q$,
    $q$v_hash := encode(
    extensions.digest(
      convert_to(
        jsonb_build_object(
          'schema_version','WOW_V17_SHARED_ENVIRONMENTAL_SEMANTIC_HASH_V1',
          'event_prediction_id',p_event_prediction_id,
          'official_event_id',r.official_event_id,
          'venue',r.venue,
          'sport',r.sport,
          'temperature_f',nullif(v_weather->>'temp',''),
          'wind',nullif(v_weather->>'wind',''),
          'condition',nullif(v_weather->>'condition',''),
          'official_weather',v_weather,
          'model_input_semantics','CONTEXT_ONLY_UNLESS_CERTIFIED_FEATURE_PRESENT',
          'probability_adjustment_applied',false
        )::text,
        'UTF8'
      ),
      'sha256'
    ),
    'hex'
  );$q$
  );
  if d = prior then
    raise exception 'V17_SHARED_ENVIRONMENT_SEMANTIC_HASH_PATCH_NOT_APPLIED';
  end if;

  execute d;
end;
$do$;

-- Patch final refresh in place. Freshness remains independent of materiality:
-- a stale/missing WEATHER_STATUS still fails the refresh. Only a hash mismatch
-- between two explicitly context-only weather observations is exempted from
-- model invalidation.
do $do$
declare
  d text;
  prior text;
begin
  select pg_get_functiondef(
    'public.wow_v17_probability_only_final_refresh(uuid,uuid,timestamp with time zone)'::regprocedure
  ) into d;

  prior := d;
  d := replace(
    d,
    $q$select evidence_status,source_grade,evidence_timestamp,freshness_ttl_seconds,payload_hash,retrieved_at into e from public.wow_event_evidence$q$,
    $q$select evidence_status,source_grade,evidence_timestamp,freshness_ttl_seconds,payload_hash,retrieved_at,evidence_payload into e from public.wow_event_evidence$q$
  );
  if d = prior then
    raise exception 'V17_FINAL_REFRESH_CURRENT_EVIDENCE_PAYLOAD_PATCH_NOT_APPLIED';
  end if;

  prior := d;
  d := replace(
    d,
    $q$select payload_hash into sc from public.wow_event_scoring_evidence where event_prediction_id=p_event_prediction_id and scoring_snapshot_id=r.scoring_snapshot_id and evidence_kind=k limit 1;$q$,
    $q$select scored.payload_hash,source_evidence.evidence_payload
   into sc
   from public.wow_event_scoring_evidence scored
   left join public.wow_event_evidence source_evidence
     on source_evidence.evidence_id=scored.evidence_id
   where scored.event_prediction_id=p_event_prediction_id
     and scored.scoring_snapshot_id=r.scoring_snapshot_id
     and scored.evidence_kind=k
   limit 1;$q$
  );
  if d = prior then
    raise exception 'V17_FINAL_REFRESH_SCORING_EVIDENCE_PAYLOAD_PATCH_NOT_APPLIED';
  end if;

  prior := d;
  d := replace(
    d,
    $q$if not found then reasons:=array_append(reasons,k||'_SCORING_HASH_MISSING');material_change:=true; elsif e.payload_hash is distinct from sc.payload_hash then reasons:=array_append(reasons,k||'_MATERIAL_CHANGE_AFTER_MODEL');material_change:=true; end if;$q$,
    $q$if not found then
    reasons:=array_append(reasons,k||'_SCORING_HASH_MISSING');
    material_change:=true;
   elsif e.payload_hash is distinct from sc.payload_hash then
    if not (
      k='WEATHER_STATUS'
      and coalesce(
        e.evidence_payload->>'model_input_semantics',
        e.evidence_payload->>'model_role',
        ''
      ) like 'CONTEXT_ONLY%'
      and coalesce(
        sc.evidence_payload->>'model_input_semantics',
        sc.evidence_payload->>'model_role',
        ''
      ) like 'CONTEXT_ONLY%'
      and coalesce((e.evidence_payload->>'probability_adjustment_applied')::boolean,false)=false
      and coalesce((sc.evidence_payload->>'probability_adjustment_applied')::boolean,false)=false
    ) then
      reasons:=array_append(reasons,k||'_MATERIAL_CHANGE_AFTER_MODEL');
      material_change:=true;
    end if;
   end if;$q$
  );
  if d = prior then
    raise exception 'V17_FINAL_REFRESH_CONTEXT_ONLY_WEATHER_MATERIALITY_PATCH_NOT_APPLIED';
  end if;

  execute d;
end;
$do$;
