from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb


DATABASE_URL = os.getenv("WOW_SCOUT_QUEUE_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="Postgres integration database not configured")
ROOT = Path(__file__).resolve().parent
MIGRATION = ROOT / "migrations" / "20261003_v17_scout_handoff_queue.sql"


@pytest.fixture(scope="module", autouse=True)
def _install_schema():
    if not DATABASE_URL:
        yield
        return
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("create extension if not exists pgcrypto")
            for role in ("anon", "authenticated", "service_role"):
                cur.execute(
                    f"do $$ begin create role {role}; exception when duplicate_object then null; end $$;"
                )
            cur.execute(MIGRATION.read_text(encoding="utf-8"))
    yield


@pytest.fixture(autouse=True)
def _clean():
    if not DATABASE_URL:
        yield
        return
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("truncate public.wow_scout_handoff_state_events, public.wow_scout_handoff_jobs restart identity cascade")
    yield


def _enqueue(cur, *, source="run-1", candidate="cand-1", payload=None):
    payload = payload or {
        "row_key": "row-1",
        "sport": "MLB",
        "player": "Example",
        "stat_type": "HITS",
        "line": 0.5,
        "direction": "OVER",
    }
    cur.execute(
        """
        select * from public.wow_enqueue_scout_handoff_job(
          %s,%s,%s,'WOW_PROP_LANE','HIGH','/score-pick-request',%s,%s::jsonb,null,null
        );
        """,
        (source, source, candidate, f"{source}:{candidate}", Jsonb(payload)),
    )
    return cur.fetchone()


def test_enqueue_is_idempotent_by_source_candidate_and_lane():
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            _enqueue(cur)
            _enqueue(cur)
            cur.execute("select count(*) from public.wow_scout_handoff_jobs")
            assert cur.fetchone()[0] == 1
            cur.execute("select count(*) from public.wow_scout_handoff_state_events")
            assert cur.fetchone()[0] == 4


def test_active_lease_excludes_second_worker_then_expired_lease_is_reclaimed():
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            _enqueue(cur)
            cur.execute("select * from public.wow_claim_scout_handoff_job('worker-1',60)")
            first = cur.fetchone()
            assert first["current_state"] == "SPECIALIST_PROCESSING"
            assert first["attempt_count"] == 1
            assert first["lease_owner"] == "worker-1"

            cur.execute("select * from public.wow_claim_scout_handoff_job('worker-2',60)")
            assert cur.fetchone() is None

            cur.execute(
                "update public.wow_scout_handoff_jobs set lease_expires_at=now()-interval '1 second' where job_id=%s",
                (first["job_id"],),
            )
            cur.execute("select * from public.wow_claim_scout_handoff_job('worker-2',60)")
            reclaimed = cur.fetchone()
            assert reclaimed["job_id"] == first["job_id"]
            assert reclaimed["current_state"] == "SPECIALIST_PROCESSING"
            assert reclaimed["attempt_count"] == 2
            assert reclaimed["lease_owner"] == "worker-2"

            cur.execute(
                "select code, detail from public.wow_scout_handoff_state_events where job_id=%s order by event_id desc limit 1",
                (first["job_id"],),
            )
            event = cur.fetchone()
            assert event["code"] == "SPECIALIST_LEASE_RECLAIMED"
            assert event["detail"]["previous_worker_id"] == "worker-1"
            assert event["detail"]["lease_reclaimed"] is True

            cur.execute(
                "update public.wow_scout_handoff_jobs set lease_expires_at=now()-interval '1 second' where job_id=%s",
                (first["job_id"],),
            )
            cur.execute("select * from public.wow_claim_scout_handoff_job('worker-3',60)")
            assert cur.fetchone() is None
            cur.execute(
                "select current_state,terminal,last_error_code,last_error_detail from public.wow_scout_handoff_jobs where job_id=%s",
                (first["job_id"],),
            )
            blocked = cur.fetchone()
            assert blocked["current_state"] == "HANDOFF_BLOCKED"
            assert blocked["terminal"] is True
            assert blocked["last_error_code"] == "SCOUT_HANDOFF_RETRY_EXHAUSTED"
            assert blocked["last_error_detail"]["lease_reclaim_budget"] == 2


def test_expired_non_repeat_safe_team_lease_blocks_instead_of_reexecuting():
    payload = {
        "research_run_id": "rr-nfl",
        "objective_lane": "OUTRIGHT_WIN_PROBABILITY",
        "sport": "NFL",
        "league": "NFL",
        "event_key": "NFL:evt-1",
        "event_state": "PREGAME",
        "event_date": "2026-10-03",
        "timezone": "America/Chicago",
        "price_required_for_objective": False,
        "event_start_time_utc": "2026-10-04T00:00:00Z",
        "home_team": "Home",
        "away_team": "Away",
    }
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                """
                select * from public.wow_enqueue_scout_handoff_job(
                  'run-nfl','rr-nfl','cand-nfl','LLP_TEAM_BETTING_ENGINE','HIGH',
                  '/score-team-event-request','rr-nfl:cand-nfl',%s::jsonb,null,null
                );
                """,
                (Jsonb(payload),),
            )
            cur.fetchone()
            cur.execute("select * from public.wow_claim_scout_handoff_job('worker-1',60)")
            first = cur.fetchone()
            assert first["attempt_count"] == 1
            cur.execute(
                "update public.wow_scout_handoff_jobs set lease_expires_at=now()-interval '1 second' where job_id=%s",
                (first["job_id"],),
            )
            cur.execute("select * from public.wow_claim_scout_handoff_job('worker-2',60)")
            assert cur.fetchone() is None
            cur.execute(
                "select current_state,terminal,last_error_code from public.wow_scout_handoff_jobs where job_id=%s",
                (first["job_id"],),
            )
            blocked = cur.fetchone()
            assert blocked["current_state"] == "HANDOFF_BLOCKED"
            assert blocked["terminal"] is True
            assert blocked["last_error_code"] == "SCOUT_HANDOFF_AMBIGUOUS_RETRY_PROHIBITED"


def test_database_derives_qualification_from_specialist_receipt():
    complete = {
        "result": {
            "rows": [{
                "terminal_status": "COMPLETED",
                "probability_publishable": True,
                "rank_eligible": True,
                "card_admission_eligible": True,
                "can_execute": False,
            }],
            "can_execute": False,
        },
        "can_execute": False,
    }
    missing_admission = {
        "result": {
            "rows": [{
                "terminal_status": "COMPLETED",
                "probability_publishable": True,
                "rank_eligible": True,
                "can_execute": False,
            }],
            "can_execute": False,
        },
        "can_execute": False,
    }
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            _enqueue(cur, source="run-pass", candidate="cand-pass")
            cur.execute("select * from public.wow_claim_scout_handoff_job('worker-pass',60)")
            claimed = cur.fetchone()
            assert claimed is not None
            assert claimed["current_state"] == "SPECIALIST_PROCESSING"
            assert claimed["lease_owner"] == "worker-pass"
            cur.execute(
                "select current_state,lease_owner,terminal from public.wow_scout_handoff_jobs where job_id=%s",
                (claimed["job_id"],),
            )
            persisted_claim = cur.fetchone()
            assert persisted_claim["current_state"] == "SPECIALIST_PROCESSING"
            assert persisted_claim["lease_owner"] == "worker-pass"
            assert persisted_claim["terminal"] is False
            cur.execute(
                "select * from public.wow_finish_scout_handoff_job(%s,'worker-pass',%s::jsonb)",
                (claimed["job_id"], Jsonb(complete)),
            )
            assert cur.fetchone()["current_state"] == "V17_QUALIFIED"

            _enqueue(cur, source="run-missing", candidate="cand-missing")
            cur.execute("select * from public.wow_claim_scout_handoff_job('worker-missing',60)")
            claimed = cur.fetchone()
            cur.execute(
                "select * from public.wow_finish_scout_handoff_job(%s,'worker-missing',%s::jsonb)",
                (claimed["job_id"], Jsonb(missing_admission)),
            )
            assert cur.fetchone()["current_state"] == "MODEL_EVALUATED"


def test_database_rejects_probability_authority_payload_even_if_caller_forges_pass():
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.RaiseException, match="SCOUT_PROBABILITY_AUTHORITY_VIOLATION"):
                _enqueue(cur, payload={"model_probability": 0.9, "row_key": "forged"})


def test_batch_rejects_unverified_red_team_pass():
    forged = [{
        "source_run_id": "run-1",
        "research_run_id": "run-1",
        "candidate_id": "cand-1",
        "target_lane": "WOW_PROP_LANE",
        "research_priority": "HIGH",
        "target_route": "/score-pick-request",
        "request_id": "run-1:cand-1",
        "request_payload": {"row_key": "row-1"},
        "red_team_status": "RED_TEAM_PASSED",
        "red_team_rule_version": "FORGED_RULE",
    }]
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.RaiseException, match="SCOUT_HANDOFF_RED_TEAM_RULE_UNVERIFIED"):
                cur.execute(
                    "select public.wow_enqueue_scout_handoff_batch(%s::jsonb)",
                    (Jsonb(forged),),
                )
