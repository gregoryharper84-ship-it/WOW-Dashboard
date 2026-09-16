-- V17 Fantasy Score ingestion repair.
-- 1) Preserve full quoted-comma semantics while avoiding the regex path for
--    rows that contain no material comma inside a quoted field.
-- 2) Normalize the known nflverse image transform comma before fast-path
--    detection so regular weekly rows do not pay the full regex cost.
-- 3) Repair the hash-pinned 2026 ESPN NBA/WNBA player-box width from the stale
--    50-column guard to the verified 57-column release schema.

create or replace function public.wow_v17_csv_fields(p_line text)
returns text[]
language plpgsql
immutable
strict
as $$
declare
  v_fast text[];
  v_work text;
begin
  -- nflverse headshot URLs use the quoted transform token f_auto,q_auto.
  -- Replace only that exact known comma before quoted-comma detection, then
  -- restore it after splitting. Rare player names such as "Last, Jr." still
  -- take the original full parser below.
  v_work := replace(p_line, 'f_auto,q_auto', 'f_auto__WOW_CSV_COMMA__q_auto');

  if v_work !~ '"[^"]*,[^"]*"' then
    select array_agg(
      replace(
        replace(trim(both '"' from x), '""', '"'),
        'f_auto__WOW_CSV_COMMA__q_auto',
        'f_auto,q_auto'
      )
      order by ord
    ) into v_fast
    from unnest(string_to_array(v_work, ',')) with ordinality u(x, ord);
    return v_fast;
  end if;

  -- Preserve the original full CSV parser whenever another quoted field
  -- actually contains a comma.
  return (
    select array_agg(replace(coalesce(m[1],m[2]), '""', '"') order by ord)
    from regexp_matches(p_line, '(?:^|,)(?:"((?:[^"]|"")*)"|([^,]*))', 'g')
      with ordinality r(m,ord)
  );
end;
$$;

revoke all on function public.wow_v17_csv_fields(text) from public, anon, authenticated;
grant execute on function public.wow_v17_csv_fields(text) to service_role;
comment on function public.wow_v17_csv_fields(text) is
  'V17 Fantasy Score CSV parser with nflverse URL normalization, direct-split fast path, and quoted-comma regex fallback.';

-- The source files are hash pinned, so width is part of their frozen schema.
-- Patch only the exact stale guard in the already-installed builder. Fresh
-- databases apply the original builder first and this repair immediately after.
do $$
declare
  v_def text;
  v_patched text;
begin
  select pg_get_functiondef(
    'public.wow_v17_build_nba_wnba_nfl_fantasy_candidates(text,boolean)'::regprocedure
  ) into v_def;

  if position('array_length(a,1)=50' in v_def) = 0 then
    if position('array_length(a,1)=57' in v_def) > 0 then
      return;
    end if;
    raise exception 'FANTASY_SCORE_BASKETBALL_SCHEMA_GUARD_NOT_FOUND';
  end if;

  v_patched := replace(v_def, 'array_length(a,1)=50', 'array_length(a,1)=57');
  if position('array_length(a,1)=50' in v_patched) > 0 then
    raise exception 'FANTASY_SCORE_BASKETBALL_SCHEMA_GUARD_PATCH_INCOMPLETE';
  end if;
  execute v_patched;
end;
$$;

comment on function public.wow_v17_build_nba_wnba_nfl_fantasy_candidates(text,boolean) is
  'Evidence-only V17 NBA/WNBA/NFL Fantasy Score candidate builder; 2026 basketball source width verified at 57 fields. Never grants publication, ranking, or execution authority.';