-- Pin search_path on the immutable-ledger mutation rejector.
-- Behavior is unchanged; this only removes mutable search-path risk.

create or replace function public.wow_reject_immutable_mutation()
returns trigger
language plpgsql
set search_path=''
as $function$
begin
  raise exception 'immutable ledger row cannot be updated or deleted';
end;
$function$;
