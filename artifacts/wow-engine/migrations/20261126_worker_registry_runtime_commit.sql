-- Pins the resident wow-agent-worker process's Render source commit in the
-- existing worker registry, so web/worker deploy parity is observable through
-- the authenticated /wow/deploy-identity readback without a Render API key.
--
-- Nullable and backwards compatible: a worker that has not started under this
-- release (or a non-Render runtime) leaves these null, and the readback fails
-- closed with parity UNVERIFIED instead of inventing a SHA. The existing
-- registry upsert (agent_runtime_schema.sql) does not touch these columns, so
-- re-applying the schema never erases an observed commit.
--
-- can_execute=false; dry-run only.

alter table public.wow_agent_worker_registry
  add column if not exists runtime_commit text,
  add column if not exists runtime_commit_recorded_at timestamptz;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'wow_agent_worker_registry_runtime_commit_sha'
      and conrelid = 'public.wow_agent_worker_registry'::regclass
  ) then
    alter table public.wow_agent_worker_registry
      add constraint wow_agent_worker_registry_runtime_commit_sha
      check (runtime_commit is null or runtime_commit ~ '^[a-f0-9]{40}$');
  end if;
end $$;
