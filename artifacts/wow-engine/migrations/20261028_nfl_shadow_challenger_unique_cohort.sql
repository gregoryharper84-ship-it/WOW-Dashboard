-- Research-only integrity safeguard for preregistered NFL shadow evaluations.
-- Apply only through the existing approved migration path after independent DB review.
-- The advisory ledger remains immutable, non-publishing and can_execute=false.
-- Fail rather than delete, modify or silently de-duplicate an existing receipt.
do $$
begin
    if exists (
        select 1
        from public.wow_intelligence_challenger_evaluations
        group by challenger_id, cohort_key
        having count(*) > 1
    ) then
        raise exception 'NFL_SHADOW_CHALLENGER_DUPLICATE_COHORT_PREEXISTS';
    end if;
end;
$$;

create unique index if not exists wow_intelligence_challenger_cohort_unique
    on public.wow_intelligence_challenger_evaluations (challenger_id, cohort_key);

-- No runtime promotion authority, RLS change or exception to append-only triggers.
