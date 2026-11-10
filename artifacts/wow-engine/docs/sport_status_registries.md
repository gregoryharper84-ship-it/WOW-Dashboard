# WOW V17 per-sport status registries

Companion to `docs/failure_codes.md`. Derived by
`scripts/sport_failure_registry.py`.

<!-- sport-status-registry:NFL:begin -->
## NFL success and informational dispositions (November 2026)

Emitted by the modules named below. Every disposition is informational only:
none of them grants probability or publication authority, and none may be read
as a certification.

| Code | Emitting function | Emitting module | Meaning | Grants authority? |
|---|---|---|---|---|
| `NFL_SPREAD_CONTEXT_V2_EXACT_LINE_REPLAY_COMPLETE` | `run_nfl_context_v2_close_proxy_replay` | `v17/nfl_spread_context_challenger.py` | Informational disposition emitted by `run_nfl_context_v2_close_proxy_replay`; grants no authority. | None |
| `NFL_SPREAD_CONTEXT_V3_SCALE_CAL_EXACT_LINE_REPLAY_COMPLETE` | `run_nfl_context_v3_scale_calibrated_replay` | `v17/nfl_spread_scale_calibrated_challenger.py` | Informational disposition emitted by `run_nfl_context_v3_scale_calibrated_replay`; grants no authority. | None |
| `NFL_SUPPORTED` | `install` | `v17/nfl_prop_settlement_overlay.py` | Informational disposition emitted by `install`; grants no authority. | None |
| `NFL_TOTAL_POINTS_V1_HOLDOUT_REPLAY_COMPLETE` | `train_candidate` | `v17/nfl_total_points_challenger.py` | Informational disposition emitted by `train_candidate`; grants no authority. | None |

### NFL literals that are not governed codes

Model families, artifact/feature versions, table and metric names, and capture
stream names. They are listed so the coverage check is exact rather than
approximate; they are not dispositions and must not be treated as outcomes.

| Literal | Emitting module |
|---|---|
| `NFL_2024_TIME_SPLIT_WITH_2025_HOLDOUT` | `nfl_event_model_v17.py` |
| `NFL_ATD_2026_W06_HISTORICAL_EXACT10_V1` | `v17/nfl_atd_historical_grade_packet.py` |
| `NFL_ATD_OFFICIAL_SCORING_CANDIDATE_V1` | `v17/nfl_atd_official_settlement_proposals.py` |
| `NFL_CALIBRATION_OPERATION_V1` | `nfl_event_model_v17.py` |
| `NFL_CANONICAL_NUMERIC_VECTOR_SCHEMA_ORDER_V1` | `nfl_event_model_v17.py` |
| `NFL_DFS_FANTASY_SCORE` | `nfl_dfs_calibration_evidence.py` |
| `NFL_ESPN_IDENTITY_NFLVERSE_STATS_V1` | `nfl_prop_auto_hydration.py` |
| `NFL_EVENT_CONTEXT_CHALLENGER_V2` | `v17/nfl_event_context_challenger.py` |
| `NFL_EVENT_CONTEXT_FEATURES_V2` | `v17/nfl_event_context_challenger.py` |
| `NFL_EVENT_CONTEXT_LOGIT_V2` | `v17/nfl_event_context_challenger.py` |
| `NFL_EVENT_EARLY_SEASON_V1` | `nfl_event_features_p3.py` |
| `NFL_EVENT_PREGAME_PRIOR_V1` | `nfl_event_features_p2.py` |
| `NFL_EVENT_PREGAME_STATIONARY_DECAY_V1` | `v17/nfl_ml_challenger_v2.py` |
| `NFL_EVENT_PROBABILITY` | `nfl_event_model_contract.py` |
| `NFL_FITTED_MODEL_PATH_PROVEN` | `v17/nfl_team_event_specialist.py` |
| `NFL_FORWARD_CALIBRATION_HEALTH_V1` | `v17/nfl_forward_shadow.py` |
| `NFL_FORWARD_SHADOW_GRADE_V1` | `v17/nfl_forward_shadow.py` |
| `NFL_FULL_GAME_HOME_AWAY` | `v17/nfl_daily_stage_receipts.py` |
| `NFL_GSIS_GAMEBOOK` | `v17/nfl_atd_gamebook_evidence.py` |
| `NFL_ML_REMOVE_CUMULATIVE_PRIOR_GAMES_V1` | `v17/nfl_ml_prior_games_ablation.py` |
| `NFL_ML_STATIONARY_DECAY_PLATT_COMPOSITE_LB_V1` | `v17/nfl_ml_challenger_v2.py` |
| `NFL_OFFICIAL_GSIS_GAMEBOOK` | `v17/nfl_atd_official_settlement_proposals.py` |
| `NFL_OUTRIGHT_WINNER_2024_TIME_SPLIT` | `nfl_event_model_v17.py` |
| `NFL_OUTRIGHT_WIN_LOGREG_STATIONARY_DECAY_V1` | `v17/nfl_ml_challenger_v2.py` |
| `NFL_OUTRIGHT_WIN_LOGREG_V1` | `nfl_event_model_v17.py` |
| `NFL_PICKEM_POOL_V1` | `v17/nfl_pickem_pool_optimizer.py` |
| `NFL_PROP_EVIDENCE_BOOTSTRAP_SHRINKAGE_V1` | `nfl_prop_calibration_adapter.py` |
| `NFL_PROP_PRECALIBRATION_BOOTSTRAP_V1` | `nfl_prop_calibration_adapter.py` |
| `NFL_PROP_ROLLING_FITTED_V1` | `nfl_prop_model_adapter.py` |
| `NFL_SEASON_2021_2023_TRAIN_2024_CAL_2025_VALIDATION_V1` | `v17/nfl_event_context_challenger.py` |
| `NFL_SEPARATED_SEASON_PRIOR_ONLY_V1` | `v17/nfl_event_context_challenger.py` |
| `NFL_SETTLEMENT_SOURCE` | `v17/nfl_prop_settlement_overlay.py` |
| `NFL_SPREAD_CONTEXT_FEATURES_V2` | `v17/nfl_spread_context_challenger.py` |
| `NFL_SPREAD_CONTEXT_MARGIN_SCALE_CAL_V3` | `v17/nfl_spread_scale_calibrated_challenger.py` |
| `NFL_SPREAD_CONTEXT_MARGIN_V2` | `v17/nfl_spread_context_challenger.py` |
| `NFL_SPREAD_CONTEXT_RIDGE_EMPIRICAL_V2` | `v17/nfl_spread_context_challenger.py` |
| `NFL_SPREAD_CONTEXT_RIDGE_SCALE_CALIBRATED_V3` | `v17/nfl_spread_scale_calibrated_challenger.py` |
| `NFL_TOTAL_POINTS_2025_RESIDUAL_INTERVAL_V1` | `v17/nfl_total_points_tiebreaker_specialist.py` |
| `NFL_TOTAL_POINTS_PREGAME_COMPOSITE_V1` | `v17/nfl_total_points_challenger.py` |
| `NFL_TOTAL_POINTS_RIDGE_V1` | `v17/nfl_total_points_challenger.py` |
| `NFL_TOTAL_POINTS_SCORING_PRIOR_V1` | `v17/nfl_total_points_tiebreaker_specialist.py` |
| `NFL_TOTAL_POINTS_SHRUNK_SCORING_V1` | `v17/nfl_total_points_tiebreaker_specialist.py` |
| `NFL_TOTAL_POINTS_SHRUNK_SCORING_V1_2026` | `v17/nfl_total_points_tiebreaker_specialist.py` |
| `NFL_TOTAL_POINTS_TIEBREAKER_PROJECTED` | `v17/nfl_total_points_challenger.py` |
| `NFL_TOTAL_POINTS_TIEBREAKER_QUALIFIED` | `v17/nfl_total_points_tiebreaker_specialist.py` |
| `NFL_TOTAL_POINTS_TIEBREAKER_V1` | `v17/nfl_total_points_challenger.py` |

<!-- sport-status-registry:NFL:end -->

<!-- sport-status-registry:NBA:begin -->
## NBA success and informational dispositions (November 2026)

Emitted by the modules named below. Every disposition is informational only:
none of them grants probability or publication authority, and none may be read
as a certification.

| Code | Emitting function | Emitting module | Meaning | Grants authority? |
|---|---|---|---|---|
| `NBA_SCALAR_CANDIDATE_REGISTERED` | `derive_and_register_candidates` | `v17/nba_scalar_candidate_registry.py` | Informational disposition emitted by `derive_and_register_candidates`; grants no authority. | None |
| `NBA_SCALAR_CANDIDATE_REGISTRATION_COMPLETE` | `derive_and_register_candidates` | `v17/nba_scalar_candidate_registry.py` | Informational disposition emitted by `derive_and_register_candidates`; grants no authority. | None |
| `NBA_SCALAR_RESEARCH_EVIDENCE_ONLY` | `candidate_preflight` | `v17/nba_scalar_candidate_bridge.py` | Informational disposition emitted by `candidate_preflight`; grants no authority. | None |

### NBA literals that are not governed codes

Model families, artifact/feature versions, table and metric names, and capture
stream names. They are listed so the coverage check is exact rather than
approximate; they are not dispositions and must not be treated as outcomes.

| Literal | Emitting module |
|---|---|
| `NBA_CAPTURE` | `v17/nba_points_pregame_roster.py` |
| `NBA_CDN_LIVE_BOXSCORE` | `v17/nba_points_pregame_roster.py` |
| `NBA_FANTASY_SCORE_EMPIRICAL_RESIDUAL` | `v17/nba_scalar_candidate_registry.py` |
| `NBA_KICKOFF` | `v17/nba_points_pregame_roster.py` |
| `NBA_PLAYER_PROP_EMPIRICAL_RESIDUAL_JOINT_V1` | `v17/nba_scalar_fitted_candidate.py` |
| `NBA_SCALAR` | `v17/nba_scalar_fitted_candidate.py` |
| `NBA_SCALAR_FROM_JOINT_COMPONENT_RESIDUAL_V1` | `v17/nba_scalar_candidate_registry.py` |
| `NBA_STATS_PLAYERGAMELOG_AND_NBA_CDN_BOXSCORE` | `v17/nba_points_official_history.py` |

<!-- sport-status-registry:NBA:end -->

<!-- sport-status-registry:NCAAF:begin -->
## NCAAF success and informational dispositions (November 2026)

Emitted by the modules named below. Every disposition is informational only:
none of them grants probability or publication authority, and none may be read
as a certification.

| Code | Emitting function | Emitting module | Meaning | Grants authority? |
|---|---|---|---|---|
| `NCAAF_CANDIDATE_ARTIFACTS_PERSISTED` | `train_and_persist_candidate` | `ncaaf_candidate_training_runner.py` | Informational disposition emitted by `train_and_persist_candidate`; grants no authority. | None |
| `NCAAF_NCAA_ESPN_SOURCE_CROSSWALK_RESEARCH_ONLY` | `build_read_only_sql` | `v17/ncaaf_cfbd_exact_sample_sql.py` | Informational disposition emitted by `build_read_only_sql`; grants no authority. | None |
| `NCAAF_PROP_CHALLENGER_PACKAGE_BUILT` | `build_candidate_package` | `v17/ncaaf_prop_challenger.py` | Informational disposition emitted by `build_candidate_package`; grants no authority. | None |
| `NCAAF_PROP_INERT_CANDIDATES_PERSISTED` | `persist_inert_candidates` | `v17/ncaaf_prop_challenger_runner.py` | Informational disposition emitted by `persist_inert_candidates`; grants no authority. | None |
| `NCAAF_RAW_AVAILABILITY_PERSISTED` | `ingest_ncaaf_availability_report` | `ncaaf_raw_availability_runtime.py` | Informational disposition emitted by `ingest_ncaaf_availability_report`; grants no authority. | None |
| `NCAAF_RESULT_FORM_CANDIDATE_PERSISTED` | `train_and_persist` | `v17/ncaaf_result_form_candidate.py` | Informational disposition emitted by `train_and_persist`; grants no authority. | None |
| `NCAAF_SCALE_ELIGIBLE` | `module scope` | `ncaaf_trust.py` | Informational disposition emitted by `module scope`; grants no authority. | None |

### NCAAF literals that are not governed codes

Model families, artifact/feature versions, table and metric names, and capture
stream names. They are listed so the coverage check is exact rather than
approximate; they are not dispositions and must not be treated as outcomes.

| Literal | Emitting module |
|---|---|
| `NCAAF_EVIDENCE_FEATURE_COMPILER_V1` | `ncaaf_feature_compiler.py` |
| `NCAAF_FEATURES_V1` | `ncaaf_candidate_training_runner.py` |
| `NCAAF_FEATURE_TRANSFORM_V1` | `ncaaf_candidate_training_runner.py` |
| `NCAAF_LOGISTIC_V1` | `ncaaf_candidate_training_runner.py` |
| `NCAAF_PRIMARY_CANDIDATE` | `ncaaf_trust.py` |
| `NCAAF_PROP_CANONICAL_STAT_NORMALIZATION_AND_TRAINING_CORPUS` | `v17/ncaaf_prop_history_maintenance.py` |
| `NCAAF_PROP_EMPIRICAL_RESIDUAL_CAL_V1` | `v17/ncaaf_prop_challenger.py` |
| `NCAAF_PROP_EMPIRICAL_RESIDUAL_RIDGE_V1` | `v17/ncaaf_prop_challenger.py` |
| `NCAAF_PROP_FEATURES_V1` | `v17/ncaaf_prop_challenger.py` |
| `NCAAF_PROP_ROLLING_FORM_V1` | `v17/ncaaf_prop_challenger.py` |
| `NCAAF_RESULT_FORM_FORWARD_FEATURES_DIAGNOSTIC_V1` | `v17/ncaaf_current_event_result_form.py` |
| `NCAAF_RESULT_FORM_LOGIT_V1` | `v17/ncaaf_result_form_candidate.py` |
| `NCAAF_RESULT_FORM_PRIOR_V1` | `v17/ncaaf_result_form_candidate.py` |
| `NCAAF_SETTLED_PRIOR_RESULTS_RECONSTRUCTION_V1` | `v17/ncaaf_result_form_candidate.py` |
| `NCAAF_TEST_ONLY` | `ncaaf_trust.py` |
| `NCAAF_TRUSTED` | `ncaaf_trust.py` |
| `NCAAF_WATCH` | `ncaaf_trust.py` |

<!-- sport-status-registry:NCAAF:end -->

<!-- sport-status-registry:NCAAB:begin -->
## NCAAB success and informational dispositions (November 2026)

Emitted by the modules named below. Every disposition is informational only:
none of them grants probability or publication authority, and none may be read
as a certification.

| Code | Emitting function | Emitting module | Meaning | Grants authority? |
|---|---|---|---|---|
| `NCAAB_CANDIDATE_PERSISTED` | `train_and_persist` | `v17/ncaab_sportsdataverse_candidate.py` | Informational disposition emitted by `train_and_persist`; grants no authority. | None |

### NCAAB literals that are not governed codes

Model families, artifact/feature versions, table and metric names, and capture
stream names. They are listed so the coverage check is exact rather than
approximate; they are not dispositions and must not be treated as outcomes.

| Literal | Emitting module |
|---|---|
| `NCAAB_ML_FROZEN_REPLAY_RECEIPT_V2` | `v17/ncaab_moneyline_replay.py` |
| `NCAAB_PUBLIC_SOURCE_PINNED_NUMERICAL_REPLAY_V1` | `v17/ncaab_source_pinned_replay.py` |
| `NCAAB_REPLAY_MARKET_FEATURES_USED` | `v17/ncaab_moneyline_replay.py` |
| `NCAAB_RESULT_FORM_LOGIT_V1` | `v17/ncaab_sportsdataverse_candidate.py` |
| `NCAAB_TEAM_FORM_PRIOR_V1` | `v17/ncaab_sportsdataverse_candidate.py` |

<!-- sport-status-registry:NCAAB:end -->

<!-- sport-status-registry:TENNIS:begin -->
## Tennis success and informational dispositions (November 2026)

Emitted by the modules named below. Every disposition is informational only:
none of them grants probability or publication authority, and none may be read
as a certification.

| Code | Emitting function | Emitting module | Meaning | Grants authority? |
|---|---|---|---|---|
| `TENNIS_MAIN_TOUR_MATCH_WIN_CANDIDATES_PERSISTED` | `train_all` | `v17/tennis_valuebet_candidate.py` | Informational disposition emitted by `train_all`; grants no authority. | None |

### Tennis literals that are not governed codes

Model families, artifact/feature versions, table and metric names, and capture
stream names. They are listed so the coverage check is exact rather than
approximate; they are not dispositions and must not be treated as outcomes.

| Literal | Emitting module |
|---|---|
| `TENNIS_MAIN_TOUR_FORM_SURFACE_PRIOR_V1` | `v17/tennis_valuebet_candidate.py` |
| `TENNIS_MAIN_TOUR_MATCH_WIN_LOGIT_V1` | `v17/tennis_valuebet_candidate.py` |

<!-- sport-status-registry:TENNIS:end -->

<!-- sport-status-registry:SOCCER:begin -->
## Soccer success and informational dispositions (November 2026)

Emitted by the modules named below. Every disposition is informational only:
none of them grants probability or publication authority, and none may be read
as a certification.

| Code | Emitting function | Emitting module | Meaning | Grants authority? |
|---|---|---|---|---|
| `SOCCER_1X2_CANDIDATES_PERSISTED` | `train_all` | `v17/soccer_openfootball_candidate.py` | Informational disposition emitted by `train_all`; grants no authority. | None |

### Soccer literals that are not governed codes

Model families, artifact/feature versions, table and metric names, and capture
stream names. They are listed so the coverage check is exact rather than
approximate; they are not dispositions and must not be treated as outcomes.

| Literal | Emitting module |
|---|---|
| `SOCCER_1X2_MULTINOMIAL_LOGIT_V1` | `v17/soccer_openfootball_candidate.py` |
| `SOCCER_1X2_MULTINOMIAL_LOGIT_V1_` | `v17/soccer_multiclass_certification_evidence.py` |
| `SOCCER_RESULT_FORM_1X2_PRIOR_V1` | `v17/soccer_multiclass_certification_evidence.py` |

<!-- sport-status-registry:SOCCER:end -->

<!-- sport-status-registry:MLB:begin -->
## MLB success and informational dispositions (November 2026)

Emitted by the modules named below. Every disposition is informational only:
none of them grants probability or publication authority, and none may be read
as a certification.

| Code | Emitting function | Emitting module | Meaning | Grants authority? |
|---|---|---|---|---|
| `MLB_1IP_EXACT_LINE_EXPANSION_CERTIFIED` | `run_mlb_1ip_line_expansion_maintenance` | `v17/mlb_1ip_line_expansion_maintenance.py` | Informational disposition emitted by `run_mlb_1ip_line_expansion_maintenance`; grants no authority. | None |
| `MLB_1IP_LINE_EXPANSION_ALREADY_CERTIFIED` | `run_mlb_1ip_line_expansion_maintenance` | `v17/mlb_1ip_line_expansion_maintenance.py` | Informational disposition emitted by `run_mlb_1ip_line_expansion_maintenance`; grants no authority. | None |
| `MLB_RUN_LINE_FORWARD_SHADOW_COMPLETE` | `run_mlb_run_line_forward_shadow` | `v17/mlb_run_line_shadow.py` | Informational disposition emitted by `run_mlb_run_line_forward_shadow`; grants no authority. | None |
| `MLB_RUN_LINE_FORWARD_SHADOW_READY` | `run_mlb_run_line_shadow_preflight` | `v17/mlb_run_line_shadow.py` | Informational disposition emitted by `run_mlb_run_line_shadow_preflight`; grants no authority. | None |
| `MLB_TEAM_EVENT_CALLER_EVIDENCE_FALLBACK_READY` | `_caller_fallback` | `v17/mlb_team_event_hydration.py` | Informational disposition emitted by `_caller_fallback`; grants no authority. | None |
| `MLB_TEAM_EVENT_CANONICAL_ACQUISITION_ATTEMPTED` | `_capture_requested_slate` | `v17/mlb_team_event_hydration.py` | Informational disposition emitted by `_capture_requested_slate`; grants no authority. | None |
| `MLB_TEAM_EVENT_CANONICAL_EVIDENCE_READY` | `resolve_mlb_team_event_evidence` | `v17/mlb_team_event_hydration.py` | Informational disposition emitted by `resolve_mlb_team_event_evidence`; grants no authority. | None |

### MLB literals that are not governed codes

Model families, artifact/feature versions, table and metric names, and capture
stream names. They are listed so the coverage check is exact rather than
approximate; they are not dispositions and must not be treated as outcomes.

| Literal | Emitting module |
|---|---|
| `MLB_1IP_BF_CONDITIONAL_TOTAL_PITCH_PMF_V1` | `mlb_1ip_empirical_pmf.py` |
| `MLB_1IP_BF_DIRICHLET_SHRINKAGE_V1` | `mlb_1ip_bf_model.py` |
| `MLB_1IP_CONDITIONAL_TOTAL_PITCH_PMF_V1` | `mlb_1ip_empirical_pmf.py` |
| `MLB_1IP_EMPIRICAL_CAL_V1` | `mlb_1ip_artifact_pipeline.py` |
| `MLB_1IP_EMPIRICAL_SPECIALIST` | `mlb_1ip_ingress_runtime.py` |
| `MLB_1IP_EMPIRICAL_TEMPORAL_CAL_V1` | `mlb_1ip_empirical_pmf.py` |
| `MLB_1IP_EVENT_TREE_EMPIRICAL_V1` | `mlb_1ip_artifact_pipeline.py` |
| `MLB_1IP_EVENT_TREE_TRANSFORM_V1` | `mlb_1ip_artifact_pipeline.py` |
| `MLB_1IP_EXACT_LINE_EXPANSION_14_5_16_5_INDEPENDENT_EARLY_2026` | `v17/mlb_1ip_line_expansion_maintenance.py` |
| `MLB_1IP_FEATURES_V1` | `mlb_1ip_artifact_pipeline.py` |
| `MLB_1IP_PLAYER_CONDITIONED_BF_MIXTURE_V1` | `mlb_1ip_player_conditioned.py` |
| `MLB_1IP_PLAYER_CONDITIONED_TEMPORAL_CAL_V1` | `mlb_1ip_player_conditioned.py` |
| `MLB_1IP_PLAYER_SHIFTED_AGGREGATE_WILSON_V1` | `mlb_1ip_player_conditioned.py` |
| `MLB_EXTRA_INNING_FINAL_MARGIN_EMPIRICAL_V1` | `v17/mlb_extra_inning_margin_challenger.py` |
| `MLB_EXTRA_INNING_MARGIN_EVIDENCE_V1` | `v17/mlb_extra_inning_margin_challenger.py` |
| `MLB_GAME_WIN_PAIRED_RUN_FEATURES_V1` | `v17/mlb_game_winner_shadow_evaluation.py` |
| `MLB_GAME_WIN_SHADOW_FEATURES_V1` | `v17/mlb_game_winner_shadow_challenger.py` |
| `MLB_GAME_WIN_SHADOW_PLATT_V1` | `v17/mlb_game_winner_shadow_challenger.py` |
| `MLB_GAME_WIN_SHADOW_SHARPNESS_V17` | `v17/mlb_game_winner_shadow_challenger.py` |
| `MLB_GAME_WIN_SHADOW_SHARPNESS_V17_R1` | `v17/mlb_game_winner_shadow_challenger.py` |
| `MLB_LINEUP_PLATOON_SHRINK_V1` | `mlb_event_specialist_v16.py` |
| `MLB_MSD_V17_DYNAMIC_CALIBRATION_V1` | `v17/mlb_starter_sequencing_dispersion_contract.py` |
| `MLB_MSD_V17_FEATURES_V1` | `v17/mlb_starter_sequencing_dispersion_contract.py` |
| `MLB_OFFICIAL_FEED_WEATHER_V1` | `mlb_event_specialist_v16.py` |
| `MLB_STARTER_SEQUENCING_DISPERSION_V17` | `v17/mlb_starter_sequencing_dispersion_contract.py` |
| `MLB_STATS_API_1IP_PLAYBYPLAY` | `mlb_1ip_live_acquisition.py` |
| `MLB_STATS_API_CROSS_SEASON_HISTORY` | `v17/mlb_pitcher_fantasy_score_hydration.py` |
| `MLB_STATS_API_LINEUP` | `mlb_1ip_live_acquisition.py` |
| `MLB_STATS_API_OFFICIAL_1IP_V1` | `mlb_1ip_live_acquisition.py` |
| `MLB_STATS_API_OFFICIAL_PITCHER_FANTASY_SCORE_V1` | `v17/mlb_pitcher_fantasy_score_hydration.py` |
| `MLB_STATS_API_OFFICIAL_V1` | `v17/mlb_prop_evidence_control_plane.py` |
| `MLB_STATS_API_PITCHING_GAME_LOG` | `v17/mlb_pitcher_fantasy_score_hydration.py` |
| `MLB_STATS_API_PLAYER_IDENTITY` | `v17/mlb_pitcher_fantasy_score_hydration.py` |
| `MLB_STATS_API_PROBABLE_PITCHER` | `mlb_1ip_live_acquisition.py` |
| `MLB_STATS_API_SCHEDULE_PROBABLE_PITCHER` | `v17/mlb_pitcher_fantasy_score_hydration.py` |
| `MLB_V16_V2D_CONTEXT_SHARED_SIM_R1` | `mlb_event_specialist_v16.py` |
| `MLB_V16_V2D_RUN_LINE_SHADOW_V1` | `v17/mlb_run_line_shadow.py` |
| `MLB_V2D_2024_TRAIN_2022_2024_HOME_PRIOR_R1` | `v17/mlb_event_feature_contract.py` |
| `MLB_V2D_CONTEXT_V1` | `mlb_event_specialist_v16.py` |

<!-- sport-status-registry:MLB:end -->

<!-- sport-status-registry:WNBA:begin -->
## WNBA success and informational dispositions (November 2026)

Emitted by the modules named below. Every disposition is informational only:
none of them grants probability or publication authority, and none may be read
as a certification.

| Code | Emitting function | Emitting module | Meaning | Grants authority? |
|---|---|---|---|---|
| `WNBA_COMPOSITE_CANDIDATE_REGISTERED` | `derive_and_register_candidate` | `v17/wnba_composite_candidate_registry.py` | Informational disposition emitted by `derive_and_register_candidate`; grants no authority. | None |
| `WNBA_COMPOSITE_CANDIDATE_REGISTRATION_COMPLETE` | `derive_and_register_candidate` | `v17/wnba_composite_candidate_registry.py` | Informational disposition emitted by `derive_and_register_candidate`; grants no authority. | None |
| `WNBA_COMPOSITE_RESEARCH_EVIDENCE_ONLY` | `candidate_preflight` | `v17/wnba_composite_candidate_bridge.py` | Informational disposition emitted by `candidate_preflight`; grants no authority. | None |
| `WNBA_RAW_HISTORY_PERSISTED` | `hydrate_wnba_history` | `wnba_history_runtime.py` | Informational disposition emitted by `hydrate_wnba_history`; grants no authority. | None |

### WNBA literals that are not governed codes

Model families, artifact/feature versions, table and metric names, and capture
stream names. They are listed so the coverage check is exact rather than
approximate; they are not dispositions and must not be treated as outcomes.

| Literal | Emitting module |
|---|---|
| `WNBA_CDN_SCHEDULE_CURRENT` | `v17/wnba_prop_evidence_control_plane.py` |
| `WNBA_CDN_SCHEDULE_GITHUB_OIDC_BRIDGE` | `v17/wnba_official_schedule_web_fallback.py` |
| `WNBA_COMPOSITE` | `v17/wnba_composite_candidate_bridge.py` |
| `WNBA_COMPOSITE_EMPIRICAL_RESIDUAL_JOINT_V1` | `v17/wnba_composite_fitted_candidate.py` |
| `WNBA_COMPOSITE_FROM_JOINT_COMPONENT_RESIDUAL_V1` | `v17/wnba_composite_candidate_registry.py` |
| `WNBA_FANTASY_SCORE_EMPIRICAL_RESIDUAL` | `v17/wnba_composite_candidate_registry.py` |
| `WNBA_LIVEDATA_SCOREBOARD_10_RENDER_RECOVERY` | `v17/wnba_prop_evidence_control_plane.py` |
| `WNBA_LIVEDATA_SCOREBOARD_RECOVERY_USED` | `v17/wnba_prop_evidence_control_plane.py` |
| `WNBA_OFFICIAL_INJURY_REPORT` | `v17/wnba_composite_auto_hydration.py` |
| `WNBA_OFFICIAL_SCHEDULE_API` | `v17/wnba_official_schedule_web_fallback.py` |
| `WNBA_OFFICIAL_SCHEDULE_CURRENT_ROSTERS` | `v17/wnba_prop_evidence_acquisition.py` |
| `WNBA_OFFICIAL_SCHEDULE_WEB_SSR` | `v17/wnba_official_schedule_web_fallback.py` |
| `WNBA_OFFICIAL_STATS_CDN_FORWARD_V1` | `v17/wnba_prop_evidence_acquisition.py` |
| `WNBA_OFFICIAL_STATS_CDN_INJURY_COMPOSITE_V1` | `v17/wnba_composite_auto_hydration.py` |
| `WNBA_OFFICIAL_STATS_CDN_INJURY_V1` | `wnba_prop_auto_hydration.py` |
| `WNBA_PRECALIBRATION_MOVING_BLOCK_BOOTSTRAP_V1` | `wnba_prop_calibration_adapter.py` |
| `WNBA_PROP_POISSON_LOGGLM_V1` | `v17/wnba_prop_candidate_registry.py` |
| `WNBA_PROP_PRECALIBRATION_BOOTSTRAP_V1` | `wnba_prop_calibration_adapter.py` |
| `WNBA_ROTATION_PLAYER` | `v17/wnba_composite_auto_hydration.py` |
| `WNBA_SCHEDULE_URL` | `v17/wnba_prop_evidence_control_plane.py` |
| `WNBA_STATS_COMMON_TEAM_ROSTER` | `v17/wnba_composite_auto_hydration.py` |
| `WNBA_STATS_LEAGUE_GAME_LOG` | `v17/wnba_composite_auto_hydration.py` |
| `WNBA_STATS_SCOREBOARD_V3` | `v17/wnba_spread_event_identity.py` |
| `WNBA_STATS_SCOREBOARD_V3_RECOVERY_USED` | `v17/wnba_prop_evidence_control_plane.py` |
| `WNBA_STATS_SCOREBOARD_V3_RENDER_RECOVERY` | `v17/wnba_prop_evidence_control_plane.py` |
| `WNBA_STATS_TO_ESPN_ABBREVIATION` | `v17/wnba_team_identity_aliases.py` |
| `WNBA_TRAINING_INGEST_PRE_SOURCE` | `wnba_prop_training_contract.py` |
| `WNBA_TRAINING_SOURCE_PRE_RESULT` | `wnba_prop_training_contract.py` |

<!-- sport-status-registry:WNBA:end -->
