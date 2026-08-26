-- WOW Validation Prediction Logger — DB Migration
-- Idempotent (IF NOT EXISTS on all objects).
-- Run manually or let the logger auto-apply on first call.
--
-- Tables
-- ------
-- wow_validation_prediction_log  — immutable frozen predictions
-- wow_validation_outcome_log     — post-game outcomes (one per prediction)
--
-- Design notes
-- ------------
-- log_dedup_key = SHA-256[:16] of (pitcher_mlbam_id, game_date, line, direction)
--   → stable across multi-worker retries; prevents duplicate logging.
-- ON CONFLICT (log_dedup_key) DO NOTHING enforces idempotency.
-- wow_validation_outcome_log.log_dedup_key references prediction table
--   → FK ensures orphan outcomes are impossible.
-- outcome_verified = TRUE marks cross-confirmed outcomes suitable for benchmark.

CREATE TABLE IF NOT EXISTS wow_validation_prediction_log (
    log_id              BIGSERIAL   PRIMARY KEY,
    prediction_id       TEXT        NOT NULL,
    log_dedup_key       TEXT        NOT NULL UNIQUE,
    schema_version      TEXT        NOT NULL DEFAULT '1.0.0',
    frozen_at           TIMESTAMPTZ NOT NULL,
    sport               TEXT        NOT NULL DEFAULT 'MLB',
    prop_type           TEXT        NOT NULL DEFAULT '1IP_PITCHES_THROWN',
    game_date           TEXT        NOT NULL,
    pitcher_name        TEXT        NOT NULL,
    pitcher_mlbam_id    INTEGER     NOT NULL,
    opponent            TEXT,
    line                NUMERIC(8,2) NOT NULL,
    direction           TEXT        NOT NULL
                            CHECK (direction IN ('LESS', 'MORE')),
    model_probability   NUMERIC(8,6),
    model_uncertainty   NUMERIC(8,6),
    feature_snapshot_id TEXT        NOT NULL,
    model_version       TEXT        NOT NULL,
    data_provenance     JSONB,
    run_id              TEXT,
    request_id          TEXT,
    board_date          TEXT,
    start_time          TEXT,
    skip_reason         TEXT,
    logged_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wow_vpl_game_date
    ON wow_validation_prediction_log (game_date);

CREATE INDEX IF NOT EXISTS idx_wow_vpl_pitcher
    ON wow_validation_prediction_log (pitcher_mlbam_id);

CREATE TABLE IF NOT EXISTS wow_validation_outcome_log (
    outcome_log_id      BIGSERIAL   PRIMARY KEY,
    prediction_id       TEXT        NOT NULL,
    log_dedup_key       TEXT        NOT NULL UNIQUE
                            REFERENCES wow_validation_prediction_log (log_dedup_key),
    schema_version      TEXT        NOT NULL DEFAULT '1.0.0',
    outcome_timestamp   TIMESTAMPTZ NOT NULL,
    actual_pitches      INTEGER     NOT NULL CHECK (actual_pitches >= 0),
    hit                 BOOLEAN     NOT NULL,
    outcome_source      TEXT        NOT NULL,
    outcome_verified    BOOLEAN     NOT NULL DEFAULT FALSE,
    notes               TEXT,
    logged_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wow_vol_prediction_id
    ON wow_validation_outcome_log (prediction_id);
