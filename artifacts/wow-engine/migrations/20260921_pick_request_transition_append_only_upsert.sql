-- Preserve append-only V17 transition receipts while permitting the runtime's
-- deterministic INSERT .. ON CONFLICT DO UPDATE upsert contract.
--
-- PostgreSQL requires UPDATE privilege for ON CONFLICT DO UPDATE even when the
-- incoming deterministic receipt is identical to the stored row. The runtime
-- intentionally uses deterministic transition_id values so retries can be
-- idempotent. This trigger permits only no-op updates; any material mutation of
-- an existing transition receipt remains fail-closed.

create or replace function public.wow_pick_request_transition_append_only_guard()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if new is distinct from old then
    raise exception using
      errcode = '55000',
      message = 'WOW_PICK_REQUEST_TRANSITION_APPEND_ONLY';
  end if;
  return old;
end;
$$;

revoke all on function public.wow_pick_request_transition_append_only_guard() from public, anon, authenticated;

drop trigger if exists wow_pick_request_transition_append_only_guard
  on public.wow_pick_request_row_transitions;

create trigger wow_pick_request_transition_append_only_guard
before update on public.wow_pick_request_row_transitions
for each row
execute function public.wow_pick_request_transition_append_only_guard();

grant update on table public.wow_pick_request_row_transitions to service_role;

comment on function public.wow_pick_request_transition_append_only_guard() is
  'V17 append-only guard: permits only identical no-op UPDATEs required by deterministic transition upserts.';
