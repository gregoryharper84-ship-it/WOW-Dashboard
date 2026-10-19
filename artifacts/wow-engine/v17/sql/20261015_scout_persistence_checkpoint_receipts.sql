-- P0 #1775: Private, transactional exact-once Scout persistence receipts.
-- Apply only under governed DB release; never expose wow_scout to Data API.
-- Works alongside original research tables; no existing partial state is mutated.
CREATE TABLE IF NOT EXISTS wow_scout.persist_upload_sessions (
    research_run_id text PRIMARY KEY
        REFERENCES wow_scout.research_runs(research_run_id) ON DELETE RESTRICT,
    expected_candidates integer NOT NULL CHECK (expected_candidates >= 0),
    expected_evidence integer NOT NULL CHECK (expected_evidence >= 0),
    header_sha256 text NOT NULL CHECK (header_sha256 ~ '^[a-f0-9]{64}$'),
    next_chunk_index integer NOT NULL DEFAULT 0 CHECK (next_chunk_index >= 0),
    uploaded_candidates integer NOT NULL DEFAULT 0 CHECK (uploaded_candidates >= 0),
    uploaded_evidence integer NOT NULL DEFAULT 0 CHECK (uploaded_evidence >= 0),
    upload_state text NOT NULL DEFAULT 'PERSISTING'
        CHECK (upload_state IN ('PERSISTING','COMPLETE')),
    can_execute boolean NOT NULL DEFAULT false CHECK (can_execute = false),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (uploaded_candidates <= expected_candidates),
    CHECK (uploaded_evidence <= expected_evidence)
);
CREATE TABLE IF NOT EXISTS wow_scout.persist_chunk_receipts (
    research_run_id text NOT NULL REFERENCES wow_scout.persist_upload_sessions(research_run_id)
        ON DELETE RESTRICT,
    chunk_index integer NOT NULL CHECK (chunk_index >= 0),
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
    entries integer NOT NULL CHECK (entries > 0),
    finalized_candidates integer NOT NULL CHECK (finalized_candidates >= 0),
    evidence_rows integer NOT NULL CHECK (evidence_rows >= 0),
    can_execute boolean NOT NULL DEFAULT false CHECK (can_execute = false),
    committed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (research_run_id,chunk_index)
);
REVOKE ALL ON TABLE wow_scout.persist_upload_sessions FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE wow_scout.persist_chunk_receipts FROM PUBLIC, anon, authenticated;
COMMENT ON TABLE wow_scout.persist_chunk_receipts IS
    'P0 #1775 atomic APPEND ledger. Replays require identical content hash; no missing/ambiguous 502 can count as delivered.';
