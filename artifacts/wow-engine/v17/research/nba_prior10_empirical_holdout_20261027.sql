-- WOW V17 research-only NBA paired-stat diagnostic.
-- Source: SHA-pinned 2026 SportsDataverse ESPN-derived NBA player boxscores.
-- This SQL is READ-ONLY. NEVER use as a governed prop prediction or market-value basis.
-- can_execute=false; V17_TERMINAL_REDUCER remains sole global terminal authority.
--
-- Features: each NBA regular-season player's 10 preceding played games,
-- strictly ordered by event timestamp then official game id.
-- A research "hit" is PTS, REB or AST strictly exceeding its own trailing
-- ten-game mean (NOT a sportsbook offer or exact PrizePicks threshold).
--
-- For each of four declared chronological cutoffs, freeze player-level
-- raw marginal/joint frequencies from all eligible prior flag observations.
-- Require 20 prior *flag* rows, apply Jeffreys (0.5,0.5) smoothing to
-- marginals, construct the naive independence comparator pA*pB, and compare
-- a predeclared shrinkage candidate: (joint_n + 15*pA*pB)/(n + 15).
-- 15 is a RESEARCH hyperparameter; not a calibrated/fitted coefficient.
-- Holdout outcomes do NOT refit the fixed train-only frequency parameters.
-- Trailing-ten thresholds may update with strictly earlier holdout boxscores.
--
-- Day-cluster SE accounts for same-date co-movement but NOT all dependence
-- (e.g. repeated player games). Sensitivity cutoffs have overlapping
-- test sets and do NOT constitute independent validation.
-- There are no official exact-line prop offers, frozen pregame model
-- receipts, market prices, wager receipts, or ROI estimates in this query.
--
-- If source id/hash/source width mismatch, filtered source rows vanish.
-- Treat no returned comparisons as DATA_UNOBTAINABLE, never as a PASS.

WITH cutoffs AS (
  SELECT cutoff::date FROM (
    VALUES ('2026-02-01'), ('2026-02-15'),
           ('2026-03-01'), ('2026-03-15')
  ) c(cutoff)
), parsed AS (
  SELECT public.wow_v17_csv_fields(t.line) AS a
  FROM public.wow_fantasy_score_source_cache c
  CROSS JOIN LATERAL
    regexp_split_to_table(c.raw_body, E'\\n') WITH ORDINALITY t(line,n)
  WHERE c.sport = 'NBA'
    AND c.source_id = 'SPORTSDATAVERSE_ESPN_NBA_PLAYER_BOX_2026_7A4868DA'
    AND c.raw_sha256 = '7a4868da67ae7f6ebfba23389e04220be2e3f69d2e56a3942c02aa56f1becc89'
    AND c.research_only IS TRUE AND c.can_execute IS FALSE
    AND t.n > 1 AND t.line <> ''
), games AS (
  SELECT a[1] AS game_id, a[6] AS player_id,
    a[4]::date AS game_date, a[5]::timestamptz AS game_start,
    a[28]::numeric AS pts, a[21]::numeric AS reb, a[22]::numeric AS ast
  FROM parsed
  WHERE array_length(a,1) = 57 AND a[3] = '2'
    AND a[6] <> '' AND a[7] <> ''
    AND a[12] ~ '^[0-9]+(\\.[0-9]+)?$'
    AND a[12]::numeric > 0
    AND a[28] ~ '^[0-9]+$'
    AND a[21] ~ '^[0-9]+$'
    AND a[22] ~ '^[0-9]+$'
), rolling AS (
  SELECT *,
    count(*) OVER (
      PARTITION BY player_id ORDER BY game_start,game_id
      ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
    ) AS prior_n,
    avg(pts) OVER (
      PARTITION BY player_id ORDER BY game_start,game_id
      ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
    ) AS prior_pts_mean,
    avg(reb) OVER (
      PARTITION BY player_id ORDER BY game_start,game_id
      ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
    ) AS prior_reb_mean,
    avg(ast) OVER (
      PARTITION BY player_id ORDER BY game_start,game_id
      ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
    ) AS prior_ast_mean
  FROM games
), flags AS (
  SELECT player_id, game_id, game_date,
    (pts > prior_pts_mean)::int AS p,
    (reb > prior_reb_mean)::int AS r,
    (ast > prior_ast_mean)::int AS a
  FROM rolling WHERE prior_n = 10
), training AS (
  SELECT c.cutoff, f.player_id,
    count(*)::numeric AS n,
    sum(p)::numeric AS n_p, sum(r)::numeric AS n_r,
    sum(a)::numeric AS n_a, sum(p*r)::numeric AS n_pr,
    sum(p*a)::numeric AS n_pa, sum(r*a)::numeric AS n_ra
  FROM flags f JOIN cutoffs c ON f.game_date < c.cutoff
  GROUP BY c.cutoff,f.player_id
), holdout AS (
  SELECT t.cutoff,f.player_id,f.game_date,f.p,f.r,f.a,
    t.n,t.n_pr,t.n_pa,t.n_ra,
    (t.n_p+0.5)/(t.n+1) AS p_p,
    (t.n_r+0.5)/(t.n+1) AS p_r,
    (t.n_a+0.5)/(t.n+1) AS p_a
  FROM flags f JOIN training t ON f.player_id=t.player_id
    AND f.game_date>=t.cutoff
  WHERE t.n >= 20
), pairs AS (
  SELECT h.cutoff,h.player_id,h.game_date,
    v.pair, v.outcome, v.p_independent,
    (v.n_joint+15*v.p_independent)/(h.n+15) AS p_joint_research
  FROM holdout h CROSS JOIN LATERAL (
    VALUES
      ('POINTS_REBOUNDS', h.p*h.r, h.p_p*h.p_r, h.n_pr),
      ('POINTS_ASSISTS', h.p*h.a, h.p_p*h.p_a, h.n_pa),
      ('REBOUNDS_ASSISTS', h.r*h.a, h.p_r*h.p_a, h.n_ra)
  ) v(pair,outcome,p_independent,n_joint)
), scores AS (
  SELECT cutoff,player_id,game_date,pair,outcome,p_independent,p_joint_research,
    power(p_independent-outcome,2)-power(p_joint_research-outcome,2) AS brier_gain,
    ( -outcome*ln(greatest(1e-9,least(1-1e-9,p_independent)))
      -(1-outcome)*ln(greatest(1e-9,least(1-1e-9,1-p_independent)))) -
    ( -outcome*ln(greatest(1e-9,least(1-1e-9,p_joint_research)))
      -(1-outcome)*ln(greatest(1e-9,least(1-1e-9,1-p_joint_research)))) AS log_loss_gain
  FROM pairs
), totals AS (
  SELECT cutoff,pair,count(*) AS n,
    count(DISTINCT player_id) AS players,
    count(DISTINCT game_date) AS days,
    avg(outcome) AS observed_joint_frequency,
    avg(p_independent) AS independence_forecast,
    avg(p_joint_research) AS joint_research_forecast,
    avg(brier_gain) AS brier_gain,
    avg(log_loss_gain) AS log_loss_gain
  FROM scores GROUP BY cutoff,pair
), daily AS (
  SELECT cutoff,pair,game_date,count(*) AS n,
    sum(brier_gain) AS sum_brier_gain,
    sum(log_loss_gain) AS sum_log_loss_gain
  FROM scores GROUP BY cutoff,pair,game_date
), day_cluster_se AS (
  SELECT t.cutoff,t.pair,
    sqrt((t.days::numeric/(t.days-1)) *
      sum(power(d.sum_brier_gain-t.brier_gain*d.n,2)))/t.n AS brier_se,
    sqrt((t.days::numeric/(t.days-1)) *
      sum(power(d.sum_log_loss_gain-t.log_loss_gain*d.n,2)))/t.n AS log_loss_se
  FROM totals t JOIN daily d USING(cutoff,pair)
  WHERE t.days>1
  GROUP BY t.cutoff,t.pair,t.days,t.n,t.brier_gain,t.log_loss_gain
)
SELECT t.cutoff,t.pair,t.n,t.players,t.days,
  round(t.observed_joint_frequency,5) AS observed_joint_frequency,
  round(t.independence_forecast,5) AS independence_forecast,
  round(t.joint_research_forecast,5) AS joint_research_forecast,
  round(t.brier_gain,6) AS brier_gain_positive_is_better,
  round(s.brier_se,6) AS exploratory_day_cluster_brier_se,
  round(t.log_loss_gain,6) AS log_loss_gain_positive_is_better,
  round(s.log_loss_se,6) AS exploratory_day_cluster_logloss_se,
  'RESEARCH_HOLD_NOT_CERTIFIED'::text AS status,
  false AS model_promoted,
  false AS probability_publishable,
  false AS can_execute
FROM totals t JOIN day_cluster_se s USING(cutoff,pair)
ORDER BY t.cutoff,t.pair;
