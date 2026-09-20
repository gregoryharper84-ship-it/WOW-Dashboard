-- WOW V17 Core Intelligence access hardening.
-- Apply after 20260920_v17_core_intelligence_shadow_lab.sql.
-- Defense in depth: public-schema RLS, service-role SELECT/INSERT only,
-- and explicit TRUNCATE guards on every append-only intelligence table.

do $$
declare
  tbl text;
  truncate_trigger_name text;
begin
  foreach tbl in array array[
    'wow_intelligence_observations',
    'wow_intelligence_hypotheses',
    'wow_intelligence_challenger_evaluations',
    'wow_intelligence_market_observations',
    'wow_intelligence_signal_observations',
    'wow_intelligence_signal_scorecards',
    'wow_intelligence_market_scorecards',
    'wow_intelligence_specialist_scorecards',
    'wow_intelligence_challenger_proposals',
    'wow_intelligence_promotion_reviews',
    'wow_intelligence_shadow_rows'
  ] loop
    execute format('alter table public.%I enable row level security', tbl);

    -- Supabase projects can carry legacy/default service_role grants. Replace
    -- them explicitly so the learning runtime can append and read evidence but
    -- cannot UPDATE, DELETE, TRUNCATE, alter triggers, or acquire REFERENCES.
    execute format('revoke all on table public.%I from service_role', tbl);
    execute format('grant select, insert on table public.%I to service_role', tbl);
    execute format('revoke all on table public.%I from public, anon, authenticated', tbl);

    truncate_trigger_name := 'trg_' || tbl || '_immutable_truncate';
    execute format('drop trigger if exists %I on public.%I', truncate_trigger_name, tbl);
    execute format(
      'create trigger %I before truncate on public.%I for each statement execute function public.wow_core_intelligence_block_mutation()',
      truncate_trigger_name,
      tbl
    );
  end loop;
end;
$$;
