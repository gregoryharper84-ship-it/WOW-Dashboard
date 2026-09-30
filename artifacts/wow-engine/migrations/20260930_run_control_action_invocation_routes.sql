-- #1091: admit canonical V17 run-control Action routes to invocation telemetry.
--
-- Observability-only change:
-- * no sporting probability/model/calibration/terminal behavior changes;
-- * route values remain a closed allowlist;
-- * dynamic request IDs are never persisted in the route column;
-- * can_execute remains permanently false.

alter table public.wow_action_invocation_receipts
    drop constraint if exists wow_action_invocation_route_ck;

alter table public.wow_action_invocation_receipts
    add constraint wow_action_invocation_route_ck
    check (
        route in (
            '/score-prop',
            '/score-pick-request',
            '/score-team-event-request',
            '/score-team-event',
            '/v17/pick-request-runs/resumable',
            '/v17/pick-request-runs/{request_id}',
            '/v17/pick-request-runs/{request_id}/close'
        )
    );

comment on constraint wow_action_invocation_route_ck
    on public.wow_action_invocation_receipts is
    'Closed allowlist of canonical WOW Action telemetry routes, including normalized V17 durable run-control templates; can_execute=false.';
