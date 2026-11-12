-- V17 MLB joint research: SELECT-only lineage and label evidence inventory.
-- Issue #2204 / PR #2639. NOT a model, source reconstruction, adjudicator,
-- publication gate, migration, deploy step or certification receipt.
-- Each score row is counted once. A current event lineup pointer is NOT
-- assumed to identify the snapshot that the score originally consumed.
-- A boxscore run count alone is not proof of final settlement.
-- A confirmed lineup archived after model time remains an as-of HOLD even
-- if PostgreSQL's historical transaction-stable now() explains the ordering.
-- Historical hitter season/platoon split captures are not established; this
-- query intentionally NEVER asserts fitted-source replay readiness.
WITH scored AS (
  SELECT
      s.score_snapshot_id,
      s.shadow_event_id,
      s.model_timestamp,
      e.official_event_id,
      e.official_date,
      e.event_start_time,
      e.home_team_id,
      e.away_team_id
  FROM public.wow_mlb_forward_score_snapshots s
  JOIN public.wow_mlb_forward_shadow_events e
    ON e.shadow_event_id = s.shadow_event_id
  WHERE s.score_status = 'SHADOW_SCORED_PREGAME'
    AND s.can_execute IS FALSE
    AND s.probability_publishable IS FALSE
    AND s.model_timestamp < e.event_start_time
),
official_schedule_final AS (
  SELECT
      g.value ->> 'gamePk' AS official_event_id,
      g.value #>> '{teams,home,score}' AS home_runs,
      g.value #>> '{teams,away,score}' AS away_runs,
      src.captured_at
  FROM public.wow_mlb_forward_shadow_source_snapshots src
  CROSS JOIN LATERAL jsonb_array_elements(
      COALESCE(src.raw_body::jsonb -> 'dates', '[]'::jsonb)
  ) d
  CROSS JOIN LATERAL jsonb_array_elements(
      COALESCE(d.value -> 'games', '[]'::jsonb)
  ) g
  WHERE src.source_label = 'MLB_STATS_API_OFFICIAL_SCHEDULE'
    AND src.http_status = 200
    AND src.can_execute IS FALSE
    AND g.value #>> '{status,abstractGameState}' = 'Final'
),
evidence AS (
  SELECT
      s.*,
      EXISTS (
          SELECT 1
          FROM public.wow_mlb_forward_lineup_snapshots l
          WHERE l.shadow_event_id = s.shadow_event_id
            AND l.official_event_id = s.official_event_id
            AND l.home_team_id = s.home_team_id
            AND l.away_team_id = s.away_team_id
            AND l.lineup_status = 'CONFIRMED'
            AND l.strict_pregame_provenance IS TRUE
            AND l.official_pitch_events_at_capture = 0
            AND l.official_completed_plays_at_capture = 0
            AND l.http_status = 200
            AND l.can_execute IS FALSE
            AND l.captured_at <= s.model_timestamp
            AND l.captured_at < s.event_start_time
      ) AS confirmed_lineup_captured_asof,
      EXISTS (
          SELECT 1 FROM public.wow_mlb_forward_feature_snapshots f
          WHERE f.shadow_event_id = s.shadow_event_id
            AND f.side = 'HOME'
            AND f.scoring_team_id = s.home_team_id
            AND f.opponent_team_id = s.away_team_id
            AND f.hydration_status = 'PASS'
            AND f.can_execute IS FALSE
            AND f.created_at <= s.model_timestamp
      ) AND EXISTS (
          SELECT 1 FROM public.wow_mlb_forward_feature_snapshots f
          WHERE f.shadow_event_id = s.shadow_event_id
            AND f.side = 'AWAY'
            AND f.scoring_team_id = s.away_team_id
            AND f.opponent_team_id = s.home_team_id
            AND f.hydration_status = 'PASS'
            AND f.can_execute IS FALSE
            AND f.created_at <= s.model_timestamp
      ) AS both_features_captured_asof,
      EXISTS (
          SELECT 1 FROM public.wow_mlb_forward_boxscore_snapshots b
          WHERE b.game_pk::text = s.official_event_id
            AND b.official_date = s.official_date
            AND b.captured_at > s.event_start_time
            AND b.http_status = 200
            AND b.can_execute IS FALSE
            AND b.raw_body::jsonb #>> '{teams,home,teamStats,batting,runs}' ~ '^[0-9]+$'
            AND b.raw_body::jsonb #>> '{teams,away,teamStats,batting,runs}' ~ '^[0-9]+$'
      ) AS boxscore_score_candidate,
      EXISTS (
          SELECT 1 FROM official_schedule_final fin
          JOIN public.wow_mlb_forward_boxscore_snapshots b
            ON b.game_pk::text = fin.official_event_id
           AND b.captured_at > s.event_start_time
           AND b.http_status = 200
           AND b.can_execute IS FALSE
          WHERE fin.official_event_id = s.official_event_id
            AND fin.captured_at > s.event_start_time
            AND fin.home_runs ~ '^[0-9]+$'
            AND fin.away_runs ~ '^[0-9]+$'
            AND fin.home_runs = b.raw_body::jsonb #>> '{teams,home,teamStats,batting,runs}'
            AND fin.away_runs = b.raw_body::jsonb #>> '{teams,away,teamStats,batting,runs}'
      ) AS official_final_and_boxscore_concordant
  FROM scored s
)
SELECT
    CASE WHEN model_timestamp < '2026-09-21 00:00:00+00'::timestamptz
         THEN 'PRE_TIMESTAMP_FIX'
         WHEN model_timestamp < '2026-09-28 00:00:00+00'::timestamptz
         THEN 'MIGRATION_WEEK'
         ELSE 'POST_TIMESTAMP_FIX'
    END AS research_cohort,
    count(*) AS score_snapshots,
    count(DISTINCT official_event_id) AS distinct_official_games,
    count(*) FILTER (WHERE confirmed_lineup_captured_asof) AS lineup_asof_candidates,
    count(*) FILTER (WHERE both_features_captured_asof) AS feature_asof_candidates,
    count(*) FILTER (
        WHERE confirmed_lineup_captured_asof AND both_features_captured_asof
    ) AS aligned_pregame_source_candidates,
    count(*) FILTER (WHERE boxscore_score_candidate) AS archived_boxscore_candidates,
    count(*) FILTER (
        WHERE official_final_and_boxscore_concordant
    ) AS archived_official_final_concordant,
    FALSE AS replay_fitted_source_proven,
    FALSE AS probability_publishable,
    FALSE AS can_execute,
    'V17_TERMINAL_REDUCER'::text AS terminal_authority
FROM evidence
GROUP BY research_cohort
ORDER BY research_cohort;
