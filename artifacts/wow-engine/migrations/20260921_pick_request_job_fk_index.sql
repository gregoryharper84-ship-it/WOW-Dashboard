-- Cover the durable job queue run_id foreign key for cascade/delete and join performance.
create index if not exists wow_pick_request_jobs_run_id_idx
    on public.wow_pick_request_jobs(run_id);
