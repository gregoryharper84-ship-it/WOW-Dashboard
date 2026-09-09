-- Keep retrospective position economics deterministic at currency precision.
-- This changes storage precision only; it never changes sporting probability.

alter table public.wow_postmortem_runs
    alter column total_entry type numeric(14,2) using round(total_entry,2),
    alter column total_return type numeric(14,2) using round(total_return,2),
    alter column net_profit type numeric(14,2) using round(net_profit,2);

alter table public.wow_postmortem_positions
    alter column entry_cost type numeric(14,2) using round(entry_cost,2),
    alter column gross_return type numeric(14,2) using round(gross_return,2),
    alter column net_profit type numeric(14,2) using round(net_profit,2);
