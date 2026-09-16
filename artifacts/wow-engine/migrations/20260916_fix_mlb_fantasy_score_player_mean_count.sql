-- V17 compatibility repair for the real-data MLB Fantasy Score candidate builder.
--
-- PostgreSQL does not provide jsonb_object_length(jsonb). The builder installed by
-- 20260916_mlb_fantasy_score_candidate_builder.sql used that symbol only for a
-- validation-metric count, causing the otherwise-valid fit to fail at registry
-- insertion time. Patch the already-defined function body deterministically from
-- the catalog so fresh and existing environments converge without weakening any
-- candidate authority boundary.

do $$
declare
  v_oid regprocedure := 'public.wow_v17_build_mlb_fantasy_score_candidates(text,boolean)'::regprocedure;
  v_def text;
  v_old constant text := 'jsonb_object_length(v_player_means)';
  v_new constant text := '(select count(*) from jsonb_object_keys(v_player_means))';
begin
  v_def := pg_get_functiondef(v_oid);
  if position(v_old in v_def) = 0 then
    if position(v_new in v_def) > 0 then
      return;
    end if;
    raise exception 'MLB_FANTASY_SCORE_PLAYER_MEAN_COUNT_PATCH_TARGET_NOT_FOUND';
  end if;

  v_def := replace(v_def, v_old, v_new);
  execute v_def;

  if position(v_old in pg_get_functiondef(v_oid)) > 0 then
    raise exception 'MLB_FANTASY_SCORE_PLAYER_MEAN_COUNT_PATCH_NOT_APPLIED';
  end if;
end;
$$;

-- Reassert the authority surface after CREATE OR REPLACE.
revoke all on function public.wow_v17_build_mlb_fantasy_score_candidates(text,boolean) from public;
revoke all on function public.wow_v17_build_mlb_fantasy_score_candidates(text,boolean) from anon, authenticated;
grant execute on function public.wow_v17_build_mlb_fantasy_score_candidates(text,boolean) to service_role;

comment on function public.wow_v17_build_mlb_fantasy_score_candidates(text,boolean) is
  'Evidence-only V17 MLB hitter/pitcher Fantasy Score candidate builder. Never grants publication, ranking, or execution authority.';
