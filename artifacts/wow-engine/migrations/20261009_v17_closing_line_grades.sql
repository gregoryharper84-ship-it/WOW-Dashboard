-- WOW V17: closing-line grades (measurement only).
--
-- One row per settled pregame prediction that was matched to a captured
-- CLOSE reference (rundown_market_history). Compares the model's probability
-- on the graded side against the no-vig consensus close. This table is never
-- read by scoring, market consensus, market role, rank eligibility, or the
-- terminal reducer. prediction_authority and can_execute are permanently false.

create table if not exists public.wow_closing_line_grades (
  grade_id                     uuid primary key default gen_random_uuid(),
  created_at                   timestamptz not null default now(),
  prediction_source            text not null,
  prediction_id                text not null,
  sport                        text not null,
  official_event_id            text,
  provider_event_id            text not null,
  selected_participant         text not null,
  event_start_utc              timestamptz not null,
  model_probability            numeric not null check (model_probability > 0 and model_probability < 1),
  close_probability            numeric not null check (close_probability > 0 and close_probability < 1),
  close_books                  text[] not null,
  close_book_count             integer not null check (close_book_count >= 1),
  close_quote_at               timestamptz,
  outcome                      smallint not null check (outcome in (0, 1)),
  model_brier                  numeric not null,
  close_brier                  numeric not null,
  brier_advantage_vs_close     numeric not null,
  model_log_loss               numeric not null,
  close_log_loss               numeric not null,
  log_loss_advantage_vs_close  numeric not null,
  model_minus_close            numeric not null,
  link_status                  text not null check (link_status = 'LINKED'),
  grading_version              text not null,
  close_reference_semantics    text not null,
  prediction_authority         boolean not null default false check (prediction_authority = false),
  can_execute                  boolean not null default false check (can_execute = false),
  constraint uq_wow_closing_line_grade_prediction unique (prediction_source, prediction_id)
);

create index if not exists wow_closing_line_grades_sport_start_idx
  on public.wow_closing_line_grades (sport, event_start_utc desc);

alter table public.wow_closing_line_grades enable row level security;

create or replace function public.wow_closing_line_grades_append_only_guard()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  raise exception using
    errcode = '55000',
    message = 'WOW_CLOSING_LINE_GRADES_APPEND_ONLY';
end;
$$;

revoke all on function public.wow_closing_line_grades_append_only_guard()
  from public, anon, authenticated;

drop trigger if exists wow_closing_line_grades_no_update_delete on public.wow_closing_line_grades;
create trigger wow_closing_line_grades_no_update_delete
before update or delete on public.wow_closing_line_grades
for each row execute function public.wow_closing_line_grades_append_only_guard();

drop trigger if exists wow_closing_line_grades_no_truncate on public.wow_closing_line_grades;
create trigger wow_closing_line_grades_no_truncate
before truncate on public.wow_closing_line_grades
for each statement execute function public.wow_closing_line_grades_append_only_guard();

revoke all privileges on table public.wow_closing_line_grades
  from public, anon, authenticated, service_role;
grant select, insert on table public.wow_closing_line_grades to service_role;

comment on table public.wow_closing_line_grades is
  'Append-only model-vs-captured-close grades. Measurement only: never a probability, consensus, rank, QA, release or execution input. Close is the last captured pre-start reference, not a provider-official close.';

-- Per-sport scorecard. Positive brier_advantage_vs_close = model beat the close.
create or replace view public.wow_closing_line_scorecard
with (security_invoker = true) as
select
  sport,
  count(*)                                         as graded_n,
  avg(model_brier)                                 as mean_model_brier,
  avg(close_brier)                                 as mean_close_brier,
  avg(brier_advantage_vs_close)                    as mean_brier_advantage_vs_close,
  stddev_samp(brier_advantage_vs_close)
    / nullif(sqrt(count(*)), 0)                    as brier_advantage_std_error,
  avg(log_loss_advantage_vs_close)                 as mean_log_loss_advantage_vs_close,
  avg((brier_advantage_vs_close > 0)::int)         as beat_close_rate,
  avg(abs(model_minus_close))                      as mean_abs_model_minus_close,
  min(event_start_utc)                             as first_event_utc,
  max(event_start_utc)                             as last_event_utc,
  false                                            as prediction_authority,
  false                                            as can_execute
from public.wow_closing_line_grades
group by sport;

revoke all privileges on public.wow_closing_line_scorecard from public, anon, authenticated;
grant select on public.wow_closing_line_scorecard to service_role;
