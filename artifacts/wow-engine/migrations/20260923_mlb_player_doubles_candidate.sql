-- V17 research-only MLB PLAYER_DOUBLES challenger builder.
--
-- This function fits a narrow binary model for the exact 0.5 doubles line from
-- immutable Retrosplits hitter-game rows. It uses no sportsbook inputs. It can
-- create/activate only a CANDIDATE research artifact; it cannot promote, publish,
-- rank, certify, or execute.

create or replace function public.wow_v17_build_mlb_player_doubles_candidate(
  p_training_code_sha text,
  p_activate boolean default false
)
returns jsonb
language plpgsql
security invoker
set search_path = public, extensions, pg_temp
as $$
declare
  v_identity_body text;
  v_identity_sha text;
  v_date_n integer;
  v_train_date_n integer;
  v_cal_date_n integer;
  v_train_end date;
  v_cal_end date;
  v_source_rows integer;
  v_train_rows integer;
  v_cal_rows integer;
  v_test_rows integer;
  v_league_prior double precision;
  v_player_probabilities jsonb;
  v_calibration_bins jsonb;
  v_source_hash text;
  v_payload jsonb;
  v_artifact_checksum text;
  v_model_version text;
  v_artifact_id uuid;
  v_raw_brier double precision;
  v_calibrated_brier double precision;
  v_baseline_brier double precision;
  v_raw_log_loss double precision;
  v_calibrated_log_loss double precision;
  v_baseline_log_loss double precision;
  v_test_hit_rate double precision;
  v_mean_calibrated_probability double precision;
begin
  if coalesce(p_training_code_sha, '') !~ '^[0-9a-f]{40}$' then
    raise exception 'MLB_DOUBLES_TRAINING_CODE_SHA_INVALID';
  end if;

  select raw_body, raw_sha256
    into v_identity_body, v_identity_sha
  from public.wow_mlb_research_raw_cache
  where source_label = 'RETROSHEET_PLAYER_IDENTITY_20260916'
    and http_status = 200
    and research_only = true
    and can_execute = false
    and raw_body is not null
  order by created_at desc
  limit 1;

  if v_identity_body is null or v_identity_sha !~ '^[0-9a-f]{64}$' then
    raise exception 'MLB_DOUBLES_IDENTITY_SNAPSHOT_UNAVAILABLE';
  end if;

  create temp table tmp_mlb_doubles_identity(
    person_key text primary key,
    player_name text not null
  ) on commit drop;

  insert into tmp_mlb_doubles_identity(person_key, player_name)
  select trim(split_part(line, E'\t', 1)), trim(split_part(line, E'\t', 2))
  from regexp_split_to_table(v_identity_body, E'\n') line
  where line ~ E'^[a-z0-9-]{8}\t'
    and trim(split_part(line, E'\t', 2)) <> ''
  on conflict (person_key) do nothing;

  if (select count(*) from tmp_mlb_doubles_identity) < 10000 then
    raise exception 'MLB_DOUBLES_IDENTITY_SNAPSHOT_INSUFFICIENT';
  end if;

  create temp table tmp_mlb_doubles_source on commit drop as
  select
    r.game_date,
    r.game_key as event_id,
    r.person_key,
    i.player_name,
    case when coalesce(r.b_2b, 0) > 0 then 1 else 0 end::integer as outcome
  from public.wow_mlb_retrosplits_rows r
  join tmp_mlb_doubles_identity i using(person_key)
  where r.season_phase = 'R'
    and coalesce(r.b_g, 0) > 0
    and coalesce(r.b_pa, 0) > 0;

  select count(*) into v_source_rows from tmp_mlb_doubles_source;
  if v_source_rows < 50000 then
    raise exception 'MLB_DOUBLES_SOURCE_HISTORY_INSUFFICIENT:%', v_source_rows;
  end if;

  create temp table tmp_mlb_doubles_dates on commit drop as
  select game_date,
         row_number() over(order by game_date)::integer as date_idx
  from (select distinct game_date from tmp_mlb_doubles_source) d;

  select count(*) into v_date_n from tmp_mlb_doubles_dates;
  if v_date_n < 100 then
    raise exception 'MLB_DOUBLES_DATE_HISTORY_INSUFFICIENT:%', v_date_n;
  end if;
  v_train_date_n := floor(v_date_n * 0.70)::integer;
  v_cal_date_n := floor(v_date_n * 0.15)::integer;

  select max(game_date) into v_train_end
  from tmp_mlb_doubles_dates where date_idx <= v_train_date_n;
  select max(game_date) into v_cal_end
  from tmp_mlb_doubles_dates where date_idx <= v_train_date_n + v_cal_date_n;

  create temp table tmp_mlb_doubles_train on commit drop as
  select s.*
  from tmp_mlb_doubles_source s
  where s.game_date <= v_train_end;

  select count(*), avg(outcome::double precision)
    into v_train_rows, v_league_prior
  from tmp_mlb_doubles_train;
  if v_train_rows < 30000 or v_league_prior is null or v_league_prior <= 0 or v_league_prior >= 1 then
    raise exception 'MLB_DOUBLES_TRAINING_PARTITION_INVALID';
  end if;

  create temp table tmp_mlb_doubles_player_fit on commit drop as
  select person_key,
         min(player_name) as player_name,
         count(*)::integer as n,
         sum(outcome)::integer as doubles_games,
         ((sum(outcome)::double precision + 20.0 * v_league_prior) / (count(*) + 20.0))::double precision as raw_probability
  from tmp_mlb_doubles_train
  group by person_key;

  select jsonb_object_agg(player_name, raw_probability order by player_name)
    into v_player_probabilities
  from tmp_mlb_doubles_player_fit;
  if v_player_probabilities is null or jsonb_object_length(v_player_probabilities) < 500 then
    raise exception 'MLB_DOUBLES_PLAYER_FIT_INSUFFICIENT';
  end if;

  create temp table tmp_mlb_doubles_scored on commit drop as
  select
    s.*,
    coalesce(p.raw_probability, v_league_prior)::double precision as raw_probability
  from tmp_mlb_doubles_source s
  left join tmp_mlb_doubles_player_fit p using(person_key)
  where s.game_date > v_train_end;

  create temp table tmp_mlb_doubles_cal on commit drop as
  select * from tmp_mlb_doubles_scored where game_date <= v_cal_end;
  create temp table tmp_mlb_doubles_test on commit drop as
  select * from tmp_mlb_doubles_scored where game_date > v_cal_end;

  select count(*) into v_cal_rows from tmp_mlb_doubles_cal;
  select count(*) into v_test_rows from tmp_mlb_doubles_test;
  if v_cal_rows < 5000 or v_test_rows < 5000 then
    raise exception 'MLB_DOUBLES_HOLDOUT_PARTITION_INSUFFICIENT:%:%', v_cal_rows, v_test_rows;
  end if;

  create temp table tmp_mlb_doubles_cal_binned on commit drop as
  select ntile(10) over(order by raw_probability, event_id, person_key)::integer as bin,
         raw_probability,
         outcome
  from tmp_mlb_doubles_cal;

  create temp table tmp_mlb_doubles_bins on commit drop as
  select
    bin,
    min(raw_probability)::double precision as raw_min,
    max(raw_probability)::double precision as raw_max,
    avg(raw_probability)::double precision as raw_mean,
    count(*)::integer as n,
    sum(outcome)::integer as hits,
    avg(outcome::double precision)::double precision as observed_rate,
    ((sum(outcome)::double precision + 1.0) / (count(*) + 2.0))::double precision as calibrated_probability
  from tmp_mlb_doubles_cal_binned
  group by bin
  order by bin;

  select jsonb_agg(jsonb_build_object(
      'bin', bin,
      'raw_min', raw_min,
      'raw_max', raw_max,
      'raw_mean', raw_mean,
      'n', n,
      'hits', hits,
      'observed_rate', observed_rate,
      'calibrated_probability', calibrated_probability
    ) order by bin)
    into v_calibration_bins
  from tmp_mlb_doubles_bins;

  create temp table tmp_mlb_doubles_test_mapped on commit drop as
  select
    t.*,
    (
      select b.calibrated_probability
      from tmp_mlb_doubles_bins b
      order by abs(b.raw_mean - t.raw_probability), b.bin
      limit 1
    )::double precision as calibrated_probability
  from tmp_mlb_doubles_test t;

  select
    avg((raw_probability - outcome) * (raw_probability - outcome)),
    avg((calibrated_probability - outcome) * (calibrated_probability - outcome)),
    avg((v_league_prior - outcome) * (v_league_prior - outcome)),
    -avg(outcome * ln(greatest(raw_probability, 1e-12)) + (1-outcome) * ln(greatest(1-raw_probability, 1e-12))),
    -avg(outcome * ln(greatest(calibrated_probability, 1e-12)) + (1-outcome) * ln(greatest(1-calibrated_probability, 1e-12))),
    -avg(outcome * ln(greatest(v_league_prior, 1e-12)) + (1-outcome) * ln(greatest(1-v_league_prior, 1e-12))),
    avg(outcome::double precision),
    avg(calibrated_probability)
  into
    v_raw_brier,
    v_calibrated_brier,
    v_baseline_brier,
    v_raw_log_loss,
    v_calibrated_log_loss,
    v_baseline_log_loss,
    v_test_hit_rate,
    v_mean_calibrated_probability
  from tmp_mlb_doubles_test_mapped;

  select encode(digest(convert_to(string_agg(
      md5(concat_ws('|', game_date::text, event_id, person_key, outcome::text)),
      '' order by game_date, event_id, person_key
    ), 'UTF8'), 'sha256'), 'hex')
    into v_source_hash
  from tmp_mlb_doubles_source;

  if v_source_hash !~ '^[0-9a-f]{64}$' then
    raise exception 'MLB_DOUBLES_SOURCE_HASH_INVALID';
  end if;

  v_model_version := 'MLB_PLAYER_DOUBLES_EMPIRICAL_BAYES_V1_' || substr(v_source_hash,1,12) || '_' || substr(p_training_code_sha,1,8);
  v_payload := jsonb_build_object(
    'artifact_schema_version', 'MLB_PLAYER_DOUBLES_CANDIDATE_V1',
    'sport', 'MLB',
    'stat_type', 'PLAYER_DOUBLES',
    'model_family', 'MLB_PLAYER_DOUBLES_EMPIRICAL_BAYES_V1',
    'modeled_event', 'PLAYER_RECORDS_AT_LEAST_ONE_DOUBLE',
    'supported_line', 0.5,
    'model_type', 'PLAYER_BETA_BINOMIAL_SHRINKAGE_PLUS_HISTORICAL_CALIBRATION',
    'prior_strength', 20,
    'league_prior', v_league_prior,
    'player_probabilities', v_player_probabilities,
    'calibration_bins', v_calibration_bins,
    'training_cutoff', v_train_end,
    'calibration_cutoff', v_cal_end,
    'split_policy', 'CHRONOLOGICAL_DATE_70_15_15',
    'unseen_player_policy', 'FITTED_LEAGUE_PRIOR',
    'market_features_used', false,
    'identity_source_sha256', v_identity_sha,
    'training_dataset_hash', v_source_hash,
    'forward_calibration_required', true,
    'probability_publishable', false,
    'rank_eligible', false,
    'can_execute', false
  );
  v_artifact_checksum := encode(digest(convert_to(v_payload::text, 'UTF8'), 'sha256'), 'hex');

  if p_activate then
    update public.wow_prop_fitted_model_artifacts
       set candidate_research_active = false
     where sport = 'MLB'
       and stat_type = 'PLAYER_DOUBLES'
       and model_family = 'MLB_PLAYER_DOUBLES_EMPIRICAL_BAYES_V1'
       and candidate_research_active = true
       and promoted = false
       and active = false
       and probability_publishable = false
       and can_execute = false;
  end if;

  insert into public.wow_prop_fitted_model_artifacts(
    provider_identity, model_family, model_artifact_version, calibrator_version,
    sport, stat_type, feature_schema_version, feature_transform_version,
    specialist_version, certification_id, lifecycle_state,
    training_dataset_hash, training_code_sha, artifact_checksum, artifact_format,
    artifact_payload, supported_line_min, supported_line_max, training_rows,
    validation_metrics, promoted, active, probability_publishable, can_execute,
    candidate_research_active
  ) values (
    'WOW_PROP_FITTED_MODEL_V1',
    'MLB_PLAYER_DOUBLES_EMPIRICAL_BAYES_V1',
    v_model_version,
    'MLB_PLAYER_DOUBLES_HISTORICAL_CAL_V1',
    'MLB',
    'PLAYER_DOUBLES',
    'PROP_FEATURES_V1',
    'MLB_PLAYER_DOUBLES_ANY_EVENT_V1',
    'wow.mlb-player-doubles-expert@1.0.0',
    'RESEARCH_ONLY_MLB_PLAYER_DOUBLES_V1',
    'CANDIDATE',
    v_source_hash,
    p_training_code_sha,
    v_artifact_checksum,
    'MLB_PLAYER_DOUBLES_EMPIRICAL_BAYES_V1',
    v_payload,
    0.5,
    0.5,
    v_train_rows,
    jsonb_build_object(
      'source_rows', v_source_rows,
      'train_rows', v_train_rows,
      'calibration_rows', v_cal_rows,
      'test_rows', v_test_rows,
      'raw_brier', v_raw_brier,
      'calibrated_brier', v_calibrated_brier,
      'baseline_brier', v_baseline_brier,
      'raw_log_loss', v_raw_log_loss,
      'calibrated_log_loss', v_calibrated_log_loss,
      'baseline_log_loss', v_baseline_log_loss,
      'test_hit_rate', v_test_hit_rate,
      'mean_calibrated_probability', v_mean_calibrated_probability,
      'research_screen_pass', (
        v_calibrated_brier < v_baseline_brier
        and v_calibrated_log_loss < v_baseline_log_loss
      ),
      'forward_calibration_required', true,
      'probability_publishable', false,
      'can_execute', false
    ),
    false,
    false,
    false,
    false,
    p_activate
  ) returning artifact_id into v_artifact_id;

  return jsonb_build_object(
    'status', 'MLB_PLAYER_DOUBLES_CANDIDATE_BUILT',
    'artifact_id', v_artifact_id,
    'model_artifact_version', v_model_version,
    'artifact_checksum', v_artifact_checksum,
    'training_dataset_hash', v_source_hash,
    'source_rows', v_source_rows,
    'train_rows', v_train_rows,
    'calibration_rows', v_cal_rows,
    'test_rows', v_test_rows,
    'raw_brier', v_raw_brier,
    'calibrated_brier', v_calibrated_brier,
    'baseline_brier', v_baseline_brier,
    'raw_log_loss', v_raw_log_loss,
    'calibrated_log_loss', v_calibrated_log_loss,
    'baseline_log_loss', v_baseline_log_loss,
    'research_screen_pass', (
      v_calibrated_brier < v_baseline_brier
      and v_calibrated_log_loss < v_baseline_log_loss
    ),
    'candidate_research_active', p_activate,
    'probability_publishable', false,
    'can_execute', false
  );
end;
$$;

comment on function public.wow_v17_build_mlb_player_doubles_candidate(text, boolean) is
  'Builds a research-only MLB PLAYER_DOUBLES 0.5 fitted challenger from immutable Retrosplits data. Never certifies/promotes/publishes/executes.';
