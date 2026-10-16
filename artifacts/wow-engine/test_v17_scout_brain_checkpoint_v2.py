"""Fault injection for Scout persistence P0 #1775.

Mocks authenticated network; it does NOT claim to test live Supabase transactions.
The production rollout must also run DB integration/Edge tests and QA.
"""
import hashlib
import json
from pathlib import Path
from collections import Counter

import pytest
from v17 import scout_brain_edge_sync as old
from v17.scout_brain_edge_sync_v2 import sync_v2


def sample(tmp_path, *, candidates=2):
    rows = [{
        "sport_key": "football_nfl",
        "route": "LLP_TEAM_BETTING_ENGINE",
        "official_event_id": f"event-{i}",
        "can_execute": False,
        "market_evidence": [{"market_key": "h2h", "bookmaker": "x", "price": -110, "row": j}
                            for j in range(3)]
    } for i in range(candidates)]
    path = tmp_path / "input.json"
    path.write_text(json.dumps({
        "can_execute": False, "run_id": "r1", "research_run_id": "r1",
        "status": "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS",
        "model_handoff": {"team_event_candidates": rows, "prop_candidates": []},
    }))
    return path, tmp_path / "receipt.json"


class FakeCheckpoint:
    def __init__(self, *, fail_once=False, invalid_chunk=False, unavailable=False, missing_schema=False):
        self.calls = []
        self.parts = {}
        self.fail_once = fail_once
        self.failed = False
        self.invalid_chunk = invalid_chunk
        self.unavailable = unavailable
        self.missing_schema = missing_schema
        self.committed = Counter()
        self.finalized = False

    def __call__(self, url, payload):
        phase = payload.get("persist_phase")
        self.calls.append(phase)
        if phase == "CAPABILITIES":
            if self.unavailable:
                raise RuntimeError("PERSIST_PHASE_INVALID")
            return {"ok": True, "protocol_version": 2, "schema_version": None if self.missing_schema else 2, "can_execute": False}
        assert payload["protocol_version"] == 2
        base = {"ok": True, "protocol_version": 2, "persist_phase": phase, "can_execute": False}
        if phase == "BEGIN":
            return {**base, "next_chunk_index": len(self.parts)}
        if phase == "APPEND":
            index = payload["chunk_index"]
            contents = json.dumps(payload["candidates"], separators=(",", ":")).encode()
            sha = hashlib.sha256(contents).hexdigest()
            if index in self.parts and self.parts[index] != sha:
                raise RuntimeError("SCOUT_PERSIST_CHUNK_DIGEST_MISMATCH")
            if index > len(self.parts):
                raise RuntimeError("SCOUT_PERSIST_CHUNK_OUT_OF_ORDER")
            if index not in self.parts:
                self.parts[index] = sha
                self.committed[index] += 1
            if self.fail_once and not self.failed:
                self.failed = True
                raise RuntimeError("SCOUT_EDGE_PERSIST_HTTP_502")
            entries = payload["candidates"]
            nfinal = sum(1 for entry in entries if entry["evidence_slice"]["final"])
            nev = sum(len(old.evidence_list(entry)) for entry in entries)
            return {**base, "chunk_index": index,
                    "batch_entry_count": len(entries) + int(self.invalid_chunk),
                    "batch_finalized_candidate_count": nfinal,
                    "batch_evidence_row_count": nev,
                    "duplicate": self.committed[index] == 1 and self.failed}
        if phase == "FINALIZE":
            self.finalized = True
            return {**base, "candidate_count": payload["expected_candidates"],
                    "observation_count": payload["expected_evidence"]}
        raise AssertionError(phase)


def test_lost_response_after_database_commit_is_safely_replayed(tmp_path, monkeypatch):
    p, receipt = sample(tmp_path)
    fake = FakeCheckpoint(fail_once=True)
    monkeypatch.setattr(old, "_post", fake)
    monkeypatch.setattr("v17.scout_brain_edge_sync_v2.time.sleep", lambda _: None)
    monkeypatch.setenv("WOW_SCOUT_PERSIST_PROTOCOL", "2")
    result = old.sync(str(p), str(receipt))
    assert fake.calls[0] == "CAPABILITIES"
    assert fake.calls[1] == "BEGIN"
    assert fake.calls.count("APPEND") >= 2
    assert set(fake.committed.values()) == {1}, "transport replay must not duplicate a DB commit"
    assert result["uploaded_candidate_rows"] == 2
    assert result["uploaded_evidence_rows"] == 6
    assert result["can_execute"] is False
    assert fake.finalized and receipt.exists()


def test_unavailable_server_never_begins_or_uploads(tmp_path, monkeypatch):
    p, receipt = sample(tmp_path)
    fake = FakeCheckpoint(unavailable=True)
    monkeypatch.setattr(old, "_post", fake)
    with pytest.raises(RuntimeError, match="PERSIST_PHASE_INVALID"):
        sync_v2(str(p), str(receipt))
    assert fake.calls == ["CAPABILITIES"]
    assert not receipt.exists()


def test_missing_database_schema_preflight_never_mutates(tmp_path, monkeypatch):
    p, receipt = sample(tmp_path)
    fake = FakeCheckpoint(missing_schema=True)
    monkeypatch.setattr(old, "_post", fake)
    with pytest.raises(RuntimeError, match="SCOUT_PERSIST_SCHEMA_ATTESTATION_MISSING"):
        sync_v2(str(p), str(receipt))
    assert fake.calls == ["CAPABILITIES"]
    assert not receipt.exists()


def test_mismatched_chunk_ack_fails_before_finalize(tmp_path, monkeypatch):
    p, receipt = sample(tmp_path)
    fake = FakeCheckpoint(invalid_chunk=True)
    monkeypatch.setattr(old, "_post", fake)
    with pytest.raises(RuntimeError, match="SCOUT_PERSIST_CHUNK_ACK_RECONCILIATION_FAILED"):
        sync_v2(str(p), str(receipt))
    assert not fake.finalized
    assert not receipt.exists()


def test_chunk_digest_conflict_is_not_retried(tmp_path, monkeypatch):
    p, receipt = sample(tmp_path)
    fake = FakeCheckpoint()
    monkeypatch.setattr(old, "_post", fake)
    sync_v2(str(p), str(receipt))
    prior = fake.calls.count("APPEND")
    altered = json.loads(p.read_text())
    altered["model_handoff"]["team_event_candidates"][0]["market_evidence"][0]["price"] = 999
    p.write_text(json.dumps(altered))
    with pytest.raises(RuntimeError, match="SCOUT_PERSIST_CHUNK_DIGEST_MISMATCH"):
        sync_v2(str(p), str(tmp_path / "conflict.json"))
    assert fake.calls.count("APPEND") == prior + 1
    assert not (tmp_path / "conflict.json").exists()


def test_governance_invalid_never_mints_or_uploads(tmp_path, monkeypatch):
    p, receipt = sample(tmp_path)
    altered = json.loads(p.read_text())
    altered["can_execute"] = True
    p.write_text(json.dumps(altered))
    fake = FakeCheckpoint()
    monkeypatch.setattr(old, "_post", fake)
    with pytest.raises(RuntimeError, match="SCOUT_HANDOFF_GOVERNANCE_INVALID"):
        sync_v2(str(p), str(receipt))
    assert fake.calls == []


def test_protocol_source_enforces_transactional_receipts_and_private_schema():
    root = Path(__file__).resolve().parent
    edge = (root / "v17/supabase/functions/wow-v17-scout-brain-persist/index.ts").read_text()
    helper = (root / "v17/supabase/functions/wow-v17-scout-brain-persist/checkpoint.ts").read_text()
    sql = (root / "v17/sql/20261015_scout_persistence_checkpoint_receipts.sql").read_text()
    assert 'phase === "CAPABILITIES"' in edge
    assert "checkpointedPersistence(sql,phase,runId,body" in edge
    assert "pg_advisory_xact_lock" in helper
    assert "headerHash" in helper and "header_sha256" in helper
    assert "for update" in helper
    assert "SCOUT_PERSIST_CHUNK_DIGEST_MISMATCH" in helper
    assert "SCOUT_PERSIST_LEGACY_PARTIAL_REQUIRES_RECONCILIATION" in helper
    assert "PRIMARY KEY (research_run_id,chunk_index)" in sql
    assert "REVOKE ALL" in sql


def test_private_ledger_sql_well_formed():
    """Guard against accidental JS string interpolation/duplicate SQL tails.

    This checks structure; release also requires an independent PostgreSQL
    DDL transaction in an isolated environment before migration approval.
    """
    root = Path(__file__).resolve().parent
    script = (root / "v17/sql/20261015_scout_persistence_checkpoint_receipts.sql").read_text()
    assert script.count("CREATE TABLE IF NOT EXISTS wow_scout.persist_upload_sessions") == 1
    assert script.count("CREATE TABLE IF NOT EXISTS wow_scout.persist_chunk_receipts") == 1
    assert script.count("REVOKE ALL ON TABLE") == 2
    assert script.count("COMMENT ON TABLE") == 1
    assert "header_sha256 text NOT NULL CHECK (header_sha256 ~ '^[a-f0-9]{64}$')," in script
    assert "content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$')," in script
    assert "\n),\n" not in script
    assert script.rstrip().endswith("delivered.';")

    # Exclude single quoted strings and SQL line comments; catch unterminated
    # literals, unmatched parens and accidentally appended stray fragments.
    clean = []
    in_string = False
    for line in script.splitlines():
        if not in_string and line.lstrip().startswith("--"):
            continue
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "'":
                if in_string and i + 1 < len(line) and line[i + 1] == "'":
                    i += 2
                    continue
                in_string = not in_string
            elif not in_string:
                if line[i:i+2] == "--":
                    break
                clean.append(ch)
            i += 1
    assert not in_string, "unterminated SQL literal"
    depth = 0
    for char in clean:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            assert depth >= 0, "unexpected closing parenthesis"
    assert depth == 0, "unclosed SQL parenthesis"
    assert "".join(clean).count(";") == 5, "unexpected SQL statement duplication"



def test_private_checkpoint_schema_executes_in_disposable_ci_postgres():
    """Exercise actual PostgreSQL DDL, FK, digest, unique and privacy guards.

    CI service only; this entire transaction always rolls back. It neither
    connects to production Supabase nor calls the live Edge function.
    """
    import os

    if os.environ.get("GITHUB_WORKFLOW") != "wow-engine-verify":
        pytest.skip("Disposable PostgreSQL is supplied by wow-engine-verify")
    import psycopg

    root = Path(__file__).resolve().parent
    script = (root / "v17/sql/20261015_scout_persistence_checkpoint_receipts.sql").read_text()
    dsn = "postgresql://wow_agent_ci:wow_agent_ci@localhost:5432/wow_agent_ci"
    with psycopg.connect(dsn, connect_timeout=5, autocommit=False) as connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute("CREATE SCHEMA IF NOT EXISTS wow_scout")
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS wow_scout.research_runs "
                    "(research_run_id text PRIMARY KEY)"
                )
                cursor.execute(
                    "DO $roles$ BEGIN "
                    "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') "
                    "THEN CREATE ROLE anon NOLOGIN; END IF; "
                    "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') "
                    "THEN CREATE ROLE authenticated NOLOGIN; END IF; "
                    "END $roles$;"
                )
                cursor.execute(script, prepare=False)
                cursor.execute(
                    "INSERT INTO wow_scout.research_runs (research_run_id) "
                    "VALUES ('ci-checkpoint-run')"
                )
                cursor.execute(
                    "INSERT INTO wow_scout.persist_upload_sessions "
                    "(research_run_id,expected_candidates,expected_evidence,header_sha256) "
                    "VALUES ('ci-checkpoint-run',1,2,%s)",
                    ("a" * 64,),
                )
                cursor.execute(
                    "INSERT INTO wow_scout.persist_chunk_receipts "
                    "(research_run_id,chunk_index,content_sha256,entries,"
                    "finalized_candidates,evidence_rows) "
                    "VALUES ('ci-checkpoint-run',0,%s,1,1,2)",
                    ("b" * 64,),
                )
                cursor.execute(
                    "SELECT COUNT(*) FROM wow_scout.persist_chunk_receipts "
                    "WHERE research_run_id='ci-checkpoint-run'"
                )
                assert cursor.fetchone() == (1,)
                cursor.execute(
                    "SELECT has_table_privilege('anon',"
                    "'wow_scout.persist_chunk_receipts','SELECT'), "
                    "has_table_privilege('authenticated',"
                    "'wow_scout.persist_upload_sessions','UPDATE')"
                )
                assert cursor.fetchone() == (False, False)
                cursor.execute("SAVEPOINT conflicting_chunk")
                try:
                    cursor.execute(
                        "INSERT INTO wow_scout.persist_chunk_receipts "
                        "(research_run_id,chunk_index,content_sha256,entries,"
                        "finalized_candidates,evidence_rows) "
                        "VALUES ('ci-checkpoint-run',0,%s,1,1,2)",
                        ("c" * 64,),
                    )
                except psycopg.errors.UniqueViolation:
                    cursor.execute("ROLLBACK TO SAVEPOINT conflicting_chunk")
                else:
                    raise AssertionError("Duplicate sequence bypassed PRIMARY KEY")
                cursor.execute("SAVEPOINT malformed_digest")
                try:
                    cursor.execute(
                        "INSERT INTO wow_scout.persist_chunk_receipts "
                        "(research_run_id,chunk_index,content_sha256,entries,"
                        "finalized_candidates,evidence_rows) "
                        "VALUES ('ci-checkpoint-run',1,'INVALID',1,0,0)"
                    )
                except psycopg.errors.CheckViolation:
                    cursor.execute("ROLLBACK TO SAVEPOINT malformed_digest")
                else:
                    raise AssertionError("Invalid content hash bypassed CHECK")
                cursor.execute("SAVEPOINT forbidden_execution")
                try:
                    cursor.execute(
                        "UPDATE wow_scout.persist_upload_sessions "
                        "SET can_execute=true "
                        "WHERE research_run_id='ci-checkpoint-run'"
                    )
                except psycopg.errors.CheckViolation:
                    cursor.execute("ROLLBACK TO SAVEPOINT forbidden_execution")
                else:
                    raise AssertionError("Execution flag bypassed CHECK")
        finally:
            connection.rollback()
