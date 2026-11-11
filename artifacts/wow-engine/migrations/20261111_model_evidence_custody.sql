-- Missing historical cutoffs remain NULL and fail the bundle contract.
alter table if exists public.wow_mlb_event_fitted_model_artifacts
    add column if not exists training_cutoff_at timestamptz;
alter table if exists public.wow_nfl_event_fitted_model_artifacts
    add column if not exists training_cutoff_at timestamptz;

-- Return the grade and the commit time from the same tuple snapshot. A payload
-- timestamp or transaction start cannot prove visibility at an as-of cutoff.
-- Missing/expired commit tracking deliberately yields no usable evidence.
create or replace function public.mlb_grade_committed_receipt(p_grade_id uuid)
returns jsonb language plpgsql stable security invoker
set search_path = pg_catalog, public as $$
declare result jsonb;
begin
    if current_setting('track_commit_timestamp') <> 'on' then
        return null;
    end if;
    select jsonb_build_object('grade', to_jsonb(g),
                             'committed_at', pg_xact_commit_timestamp(g.xmin))
      into result from public.wow_mlb_forward_shadow_grades g
     where g.grade_id = p_grade_id;
    return result;
end $$;
revoke all on function public.mlb_grade_committed_receipt(uuid) from public;
grant execute on function public.mlb_grade_committed_receipt(uuid) to service_role;
