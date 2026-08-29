-- WOW v16 Clean Core — NCAAF forward trust review metrics
--
-- This view derives standardized research performance from the immutable
-- NCAAF prediction/outcome ledger. It does not represent executed wagering.
-- ROI is hypothetical unit-risk ROI at the recorded pregame American ML price.
-- Fees/friction are not inferred; rows lacking a usable entry price do not enter
-- the unit-ROI denominator.

create or replace view wow_ncaaf_trust_review_metrics as
with settled as (
    select
        p.ncaaf_prediction_id,
        p.market,
        p.market_role,
        p.selection_price_american,
        p.failure_tags,
        o.won,
        o.void,
        o.clv_grade,
        case
            when o.won is null then null
            when o.void then 0.0
            when p.selection_price_american is null or p.selection_price_american = 0 then null
            when o.won and p.selection_price_american < 0
                then 100.0 / abs(p.selection_price_american)::numeric
            when o.won and p.selection_price_american > 0
                then p.selection_price_american::numeric / 100.0
            when not o.won then -1.0
            else null
        end as unit_risk_profit
    from wow_ncaaf_predictions p
    join wow_ncaaf_outcomes o
      on o.ncaaf_prediction_id = p.ncaaf_prediction_id
    where o.won is not null
),
aggregate as (
    select
        count(*)::integer as settled_candidates,
        count(*) filter (where market = 'MONEYLINE')::integer
            as ncaaf_moneyline_bucket_candidates,
        count(*) filter (
            where clv_grade in ('BEAT_CLOSE','CLOSED_SAME','LOST_TO_CLOSE')
        )::integer as clv_evaluable_n,
        count(*) filter (where clv_grade = 'BEAT_CLOSE')::integer as clv_positive_n,
        count(*) filter (where unit_risk_profit is not null)::integer as roi_evaluable_n,
        avg(unit_risk_profit) filter (where unit_risk_profit is not null)
            as hypothetical_unit_risk_roi,
        coalesce(
            count(*) filter (where cardinality(failure_tags) > 0),
            0
        )::integer as rows_with_failure_tags
    from settled
)
select
    settled_candidates,
    ncaaf_moneyline_bucket_candidates,
    clv_evaluable_n,
    clv_positive_n,
    case
        when clv_evaluable_n = 0 then null
        else clv_positive_n::numeric / clv_evaluable_n::numeric
    end as clv_positive_rate,
    roi_evaluable_n,
    hypothetical_unit_risk_roi,
    rows_with_failure_tags,
    false as can_execute
from aggregate;

comment on view wow_ncaaf_trust_review_metrics is
  'Forward NCAAF calibration/trust metrics. ROI is hypothetical unit-risk research ROI, not executed-account ROI. can_execute=false.';
