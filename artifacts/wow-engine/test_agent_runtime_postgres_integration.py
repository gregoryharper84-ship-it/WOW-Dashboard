from __future__ import annotations
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os, time

import psycopg
import pytest
from fastapi.testclient import TestClient

pytestmark=pytest.mark.skipif(os.getenv("WOW_AGENT_RUNTIME_INTEGRATION")!="1",reason="agent runtime integration only")


def _apply_schema():
    dsn=os.environ["SUPABASE_DB_URL"]
    sql=Path("agent_runtime_v1/migration.sql").read_text()
    with psycopg.connect(dsn,autocommit=True) as conn, conn.cursor() as cur:
        for role in ("anon","authenticated"):
            cur.execute(f"do $$ begin if not exists(select 1 from pg_roles where rolname='{role}') then create role {role}; end if; end $$;")
        cur.execute(sql)


def _candidate():
    now=datetime.now(timezone.utc); start=now+timedelta(hours=2)
    return {
        "sport":"MLB","league":"MLB","official_event_id":"ci-event-1","participant":"CI Home",
        "opponent":"CI Away","market_family":"OUTRIGHT_WINNER","period":"FULL_GAME","side":"HOME",
        "event_start_utc":start.isoformat(),
        "event_request":{
            "research_run_id":"ci-agent-runtime","requested_slate_date":start.date().isoformat(),"requested_timezone":"America/Chicago","scan_stage":"PREGAME",
            "event_key":"MLB:ci-event-1","official_event_id":"ci-event-1","event_start_time_utc":start.isoformat(),"sport":"MLB","league":"MLB",
            "market_family":"OUTRIGHT_WINNER","settlement_basis":"FULL_GAME_INCLUDING_EXTRA_INNINGS","home_team":"CI Home","away_team":"CI Away","venue":"CI Park",
            "home_starting_pitcher":"CI Pitcher H","away_starting_pitcher":"CI Pitcher A","home_starter_status":"PROBABLE","away_starter_status":"PROBABLE",
            "home_lineup_status":"PROJECTED","away_lineup_status":"PROJECTED","source_snapshot_id":"00000000-0000-0000-0000-000000000001",
        },
        "evidence":{
            "candidate_identity":{"official_event_id":"ci-event-1"},"official_event":{"event_start_utc":start.isoformat()},
            "exact_market_identity":{"market_family":"OUTRIGHT_WINNER","period":"FULL_GAME"},"game_log":[1.0,0.0,1.0],
            "box_score_log":[{"event_id":"a"},{"event_id":"b"},{"event_id":"c"}],"role_status":"CONFIRMED","role_timestamp":now.isoformat(),
            "source_attempts":[{"source":"CI_FIXTURE","status":"PASS"}],"source_conflicts":[],
        },
    }


def test_durable_api_to_worker_to_terminal_reconciliation(monkeypatch):
    _apply_schema()
    monkeypatch.setenv("WOW_ACTION_API_KEY","ci-agent-key")
    from agent_runtime_entrypoint import app
    client=TestClient(app)
    ready=client.get("/health/ready")
    assert ready.status_code==200,ready.text
    assert ready.json()=={"ok":True,"database":True,"queue":True,"registry":True,"can_execute":False}

    body={"as_of":datetime.now(timezone.utc).isoformat(),"user_timezone":"America/Chicago","discovery_enabled":False,"candidate_inputs":[_candidate()],"can_execute":False}
    headers={"Authorization":"Bearer ci-agent-key","Idempotency-Key":"ci-runtime-e2e"}
    first=client.post("/wow/runs",json=body,headers=headers)
    assert first.status_code==202,first.text
    run_id=first.json()["run_id"]
    duplicate=client.post("/wow/runs",json=body,headers=headers)
    assert duplicate.status_code==202
    assert duplicate.json()["run_id"]==run_id

    terminal=None
    for _ in range(100):
        response=client.get(f"/wow/runs/{run_id}/manifest",headers={"Authorization":"Bearer ci-agent-key"})
        assert response.status_code==200,response.text
        manifest=response.json()
        if manifest["terminal"]:
            terminal=manifest; break
        time.sleep(.2)
    assert terminal is not None,"run did not terminalize"
    assert terminal["status"]=="COMPLETED_WITH_BLOCKERS"
    assert terminal["reconciliation"]=={"rows_in":1,"rows_completed":0,"rows_held":1,"rows_rejected":0,"balanced":True}
    assert terminal["rows"][0]["terminal_label"]=="MODEL_UNAVAILABLE"
    assert terminal["rows"][0]["can_execute"] is False

    with psycopg.connect(os.environ["SUPABASE_DB_URL"]) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from wow.runs where run_id=%s",(run_id,)); assert cur.fetchone()[0]==1
        cur.execute("select worker_id,status from wow.agent_jobs where run_id=%s order by queued_at",(run_id,)); jobs=cur.fetchall()
        assert [row[0] for row in jobs]==["wow.parallel-discovery-router","wow.slate-integrity-expert","wow.evidence-hydration","wow.controlling-model"]
        assert all(row[1] in ("SUCCEEDED","BLOCKED") for row in jobs)
        cur.execute("select count(*) from wow.agent_outputs where run_id=%s",(run_id,)); assert cur.fetchone()[0]==4
        cur.execute("select count(*) from wow.terminal_decisions where run_id=%s",(run_id,)); assert cur.fetchone()[0]==1
