import os
import sys

REQUIRED_ENV_VARS = [
    "DATABASE_URL",
    "SCORING_API_KEY",
    "ODDS_API_KEY",
]

missing = [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]

if missing:
    for var in missing:
        print(f"FATAL: missing env var: {var}", file=sys.stderr)
    sys.exit(1)

print("pre_start: all required env vars present, starting gunicorn", flush=True)

# ---------------------------------------------------------------------------
# Skill file validation (Section 8 — startup integrity check)
# ---------------------------------------------------------------------------
try:
    _here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, _here)
    from gate_engine.wow_runtime_manifest import validate_skill_files
    _skill_check = validate_skill_files(os.path.join(_here, "skills"))
    if _skill_check["missing"]:
        print(
            f"pre_start: WARN: required skill files missing: {_skill_check['missing']} — "
            "engine will start DEGRADED (skill validation will fail in /wow/engine/health)",
            flush=True,
        )
    else:
        print(
            f"pre_start: skill file validation OK "
            f"({len(_skill_check['present'])} files present)",
            flush=True,
        )
except Exception as _e:
    print(f"pre_start: skill file validation skipped ({_e})", flush=True)

# ---------------------------------------------------------------------------
# Ledger table creation (Task-49 — pitcher, WNBA, cross-ticket)
# ---------------------------------------------------------------------------
_LEDGER_DDL = [
    # PATCH-016: MLB directional pitcher ledger
    """
    CREATE TABLE IF NOT EXISTS mlb_directional_pitcher_ledger (
        id                              BIGSERIAL PRIMARY KEY,
        pitcher                         TEXT,
        event_id                        TEXT,
        event_date                      DATE,
        market_type                     TEXT,
        directional_lane                TEXT,
        line                            NUMERIC,
        offer_type                      TEXT,
        starter_confirmation            TEXT,
        lineup_confirmation             TEXT,
        health_regime                   TEXT,
        predicted_innings               NUMERIC,
        predicted_batters_faced         NUMERIC,
        predicted_pitch_count           NUMERIC,
        predicted_strikeouts            NUMERIC,
        failure_path_score              NUMERIC,
        short_outing_support_share      NUMERIC,
        conditional_probability_given_normal_workload NUMERIC,
        unconditional_probability       NUMERIC,
        calibrated_lower_bound          NUMERIC,
        actual_innings                  NUMERIC,
        actual_batters_faced            NUMERIC,
        actual_pitch_count              NUMERIC,
        actual_strikeouts               NUMERIC,
        settled_result                  TEXT,
        observed_failure_category       TEXT,
        process_pass_or_fail            TEXT,
        duplicate_group_id              TEXT,
        row_id                          TEXT,
        logged_at                       TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS mlb_dl_pitcher_idx     ON mlb_directional_pitcher_ledger(pitcher)",
    "CREATE INDEX IF NOT EXISTS mlb_dl_lane_idx        ON mlb_directional_pitcher_ledger(directional_lane)",
    "CREATE INDEX IF NOT EXISTS mlb_dl_event_date_idx  ON mlb_directional_pitcher_ledger(event_date DESC)",
    "CREATE INDEX IF NOT EXISTS mlb_dl_duplicate_idx   ON mlb_directional_pitcher_ledger(duplicate_group_id)",

    # PATCH-017: WNBA composite forward-test ledger
    """
    CREATE TABLE IF NOT EXISTS wnba_composite_forward_test_ledger (
        id                      BIGSERIAL PRIMARY KEY,
        player_name             TEXT NOT NULL,
        event_date              DATE NOT NULL,
        event_id                TEXT,
        stat_family             TEXT,
        exact_line              NUMERIC,
        direction               TEXT,
        offer_type              TEXT,
        role_status             TEXT,
        primary_teammate_status TEXT,
        multi_path_class        TEXT,
        calibrated_lower_bound  NUMERIC,
        raw_probability         NUMERIC,
        forward_test_status     TEXT,
        is_dnp_or_void          BOOLEAN DEFAULT FALSE,
        is_promo                BOOLEAN DEFAULT FALSE,
        duplicate_group_id      TEXT,
        settled_result          TEXT,
        model_hit               BOOLEAN,
        row_id                  TEXT,
        logged_at               TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(player_name, event_date, stat_family, exact_line, direction)
    )
    """,
    "CREATE INDEX IF NOT EXISTS wnba_ft_player_idx   ON wnba_composite_forward_test_ledger(player_name)",
    "CREATE INDEX IF NOT EXISTS wnba_ft_event_idx    ON wnba_composite_forward_test_ledger(event_date DESC)",
    "CREATE INDEX IF NOT EXISTS wnba_ft_dup_idx      ON wnba_composite_forward_test_ledger(duplicate_group_id)",

    # PATCH-014: Cross-ticket exposure audit log
    """
    CREATE TABLE IF NOT EXISTS cross_ticket_exposure_log (
        id                              BIGSERIAL PRIMARY KEY,
        session_id                      TEXT,
        slate_date                      DATE,
        total_rows                      INT,
        unique_underlying_theses        INT,
        exact_duplicate_groups          INT,
        alternate_threshold_groups      INT,
        shared_latent_groups            INT,
        pitcher_thesis_groups           INT,
        portfolio_fragility_class       TEXT,
        critical_thesis                 TEXT,
        share_of_rows_at_risk           NUMERIC,
        rows_rejected                   INT,
        actions_json                    JSONB,
        logged_at                       TIMESTAMPTZ DEFAULT NOW()
    )
    """,
]

try:
    import psycopg2 as _psycopg2  # type: ignore
    _db_url = os.environ.get("DATABASE_URL", "")
    if _db_url:
        _conn = _psycopg2.connect(_db_url, connect_timeout=10)
        _cur  = _conn.cursor()
        for _stmt in _LEDGER_DDL:
            _cur.execute(_stmt)
        _conn.commit()
        _cur.close()
        _conn.close()
        print("pre_start: ledger tables created/verified OK "
              "(mlb_directional_pitcher_ledger, wnba_composite_forward_test_ledger, "
              "cross_ticket_exposure_log)", flush=True)
    else:
        print("pre_start: WARN: DATABASE_URL not set — skipping ledger table creation", flush=True)
except Exception as _e:
    print(f"pre_start: WARN: ledger table creation failed ({_e})", flush=True)
