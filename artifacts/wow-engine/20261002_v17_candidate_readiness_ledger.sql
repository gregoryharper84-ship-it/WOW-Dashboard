-- V17 Candidate Readiness durable ledger (Class B infrastructure only).
-- No sporting probability, calibration, ranking, execution, or terminal-reducer authority lives here.

CREATE TABLE IF NOT EXISTS wow_candidate_readiness (
    candidate_id TEXT PRIMARY KEY,
    source_feed TEXT NOT NULL,
    sport TEXT NOT NULL,
    raw_payload_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN (
        'ACQUIRED',
        'RECONCILED',
        'HYDRATED',
        'SPECIALIST_ASSIGNED',
        'EVALUATED',
        'QUALIFIED_FOR_REDUCER',
        'REJECTED_MALFORMED_OFFER',
        'REJECTED_UNRESOLVED_IDENTITY',
        'REJECTED_FEATURE_UNAVAILABLE',
        'REJECTED_NO_SPECIALIST',
        'REJECTED_DOMAIN_EXCEEDED',
        'REJECTED_GOVERNANCE_GATE'
    )),
    canonical_event_id TEXT,
    canonical_player_id TEXT,
    canonical_market_id TEXT,
    feature_snapshot_id TEXT,
    specialist_id TEXT,
    rejection_reason TEXT,
    can_execute BOOLEAN NOT NULL DEFAULT FALSE CHECK (can_execute = FALSE),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        (state LIKE 'REJECTED_%' AND rejection_reason IS NOT NULL)
        OR
        (state NOT LIKE 'REJECTED_%' AND rejection_reason IS NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_wow_candidate_readiness_source_payload
ON wow_candidate_readiness (source_feed, sport, raw_payload_hash);

CREATE INDEX IF NOT EXISTS idx_wow_candidate_readiness_state
ON wow_candidate_readiness (sport, state, updated_at DESC);

CREATE TABLE IF NOT EXISTS wow_candidate_readiness_transitions (
    transition_id BIGSERIAL PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES wow_candidate_readiness(candidate_id) ON DELETE RESTRICT,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    reason TEXT,
    transition_sha256 TEXT NOT NULL UNIQUE,
    can_execute BOOLEAN NOT NULL DEFAULT FALSE CHECK (can_execute = FALSE),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wow_candidate_transition_candidate
ON wow_candidate_readiness_transitions (candidate_id, transition_id);

-- Append-only audit protection. UPDATE/DELETE must be rejected for transition receipts.
CREATE OR REPLACE FUNCTION wow_reject_candidate_transition_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'wow_candidate_readiness_transitions is append-only';
END;
$$;

DROP TRIGGER IF EXISTS trg_wow_candidate_transition_no_update
ON wow_candidate_readiness_transitions;

CREATE TRIGGER trg_wow_candidate_transition_no_update
BEFORE UPDATE OR DELETE ON wow_candidate_readiness_transitions
FOR EACH ROW EXECUTE FUNCTION wow_reject_candidate_transition_mutation();

-- This function is intentionally orchestration-only. It does not score a candidate.
-- Application code validates legal transitions before using this persistence primitive.
CREATE OR REPLACE FUNCTION wow_record_candidate_transition(
    p_candidate_id TEXT,
    p_expected_from_state TEXT,
    p_to_state TEXT,
    p_reason TEXT,
    p_transition_sha256 TEXT,
    p_canonical_event_id TEXT DEFAULT NULL,
    p_canonical_player_id TEXT DEFAULT NULL,
    p_canonical_market_id TEXT DEFAULT NULL,
    p_feature_snapshot_id TEXT DEFAULT NULL,
    p_specialist_id TEXT DEFAULT NULL
)
RETURNS VOID
LANGUAGE plpgsql
AS $$
DECLARE
    v_current_state TEXT;
    v_transition_allowed BOOLEAN := FALSE;
BEGIN
    SELECT state
      INTO v_current_state
      FROM wow_candidate_readiness
     WHERE candidate_id = p_candidate_id
     FOR UPDATE;

    IF v_current_state IS NULL THEN
        RAISE EXCEPTION 'candidate not found: %', p_candidate_id;
    END IF;

    IF v_current_state <> p_expected_from_state THEN
        RAISE EXCEPTION 'candidate state changed concurrently: expected %, found %',
            p_expected_from_state, v_current_state;
    END IF;

    IF v_current_state IN (
        'QUALIFIED_FOR_REDUCER',
        'REJECTED_MALFORMED_OFFER',
        'REJECTED_UNRESOLVED_IDENTITY',
        'REJECTED_FEATURE_UNAVAILABLE',
        'REJECTED_NO_SPECIALIST',
        'REJECTED_DOMAIN_EXCEEDED',
        'REJECTED_GOVERNANCE_GATE'
    ) THEN
        RAISE EXCEPTION 'terminal candidate state cannot transition: %', v_current_state;
    END IF;

    v_transition_allowed := CASE
        WHEN v_current_state = 'ACQUIRED'
             AND p_to_state IN ('RECONCILED', 'REJECTED_MALFORMED_OFFER', 'REJECTED_UNRESOLVED_IDENTITY')
            THEN TRUE
        WHEN v_current_state = 'RECONCILED'
             AND p_to_state IN ('HYDRATED', 'REJECTED_FEATURE_UNAVAILABLE')
            THEN TRUE
        WHEN v_current_state = 'HYDRATED'
             AND p_to_state IN ('SPECIALIST_ASSIGNED', 'REJECTED_NO_SPECIALIST')
            THEN TRUE
        WHEN v_current_state = 'SPECIALIST_ASSIGNED'
             AND p_to_state IN ('EVALUATED', 'REJECTED_DOMAIN_EXCEEDED', 'REJECTED_GOVERNANCE_GATE')
            THEN TRUE
        WHEN v_current_state = 'EVALUATED'
             AND p_to_state IN ('QUALIFIED_FOR_REDUCER', 'REJECTED_GOVERNANCE_GATE')
            THEN TRUE
        ELSE FALSE
    END;

    IF NOT v_transition_allowed THEN
        RAISE EXCEPTION 'illegal candidate transition: % -> %', v_current_state, p_to_state;
    END IF;

    INSERT INTO wow_candidate_readiness_transitions (
        candidate_id,
        from_state,
        to_state,
        reason,
        transition_sha256,
        can_execute
    ) VALUES (
        p_candidate_id,
        p_expected_from_state,
        p_to_state,
        p_reason,
        p_transition_sha256,
        FALSE
    )
    ON CONFLICT (transition_sha256) DO NOTHING;

    UPDATE wow_candidate_readiness
       SET state = p_to_state,
           canonical_event_id = COALESCE(p_canonical_event_id, canonical_event_id),
           canonical_player_id = COALESCE(p_canonical_player_id, canonical_player_id),
           canonical_market_id = COALESCE(p_canonical_market_id, canonical_market_id),
           feature_snapshot_id = COALESCE(p_feature_snapshot_id, feature_snapshot_id),
           specialist_id = COALESCE(p_specialist_id, specialist_id),
           rejection_reason = p_reason,
           can_execute = FALSE,
           updated_at = NOW()
     WHERE candidate_id = p_candidate_id;
END;
$$;
