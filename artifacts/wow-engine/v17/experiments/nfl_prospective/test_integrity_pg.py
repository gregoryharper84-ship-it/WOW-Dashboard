"""Disposable PostgreSQL integration test for the immutable NFL research ledger.

Run only with WOW_TEST_POSTGRES_URL pointing to an empty local CI database.
Never point this at production. CI creates dedicated roles/schema then migrates.
"""
from datetime import datetime, timedelta, timezone
from math import log
from pathlib import Path
import json, os
import pytest


@pytest.mark.skipif(not os.getenv("WOW_TEST_POSTGRES_URL"), reason="requires disposable PostgreSQL CI service")
def test_pg_immutable_contract():
    assert os.getenv("WOW_TEST_POSTGRES_URL"), "require disposable test database"
    import psycopg
    dburi=os.environ["WOW_TEST_POSTGRES_URL"]
    root=Path(__file__).resolve().parents[3]
    base=(root/'migrations'/'20261005_nfl_ml_challenger_forward_shadow.sql').read_text()
    hardening=(root/'migrations'/'20261117_nfl_prospective_immutable_freeze.sql').read_text()
    actual_clock=(root/'migrations'/'20261117_nfl_prospective_clock_runtime_hardening.sql').read_text()
    at=datetime.now(timezone.utc)
    with psycopg.connect(dburi,autocommit=True) as conn:
        for role in ('anon','authenticated','service_role'):
            conn.execute(f'CREATE ROLE {role}')
        conn.execute(base)
        def payload(event_id, start, *, old=False):
            return ("candidate-v1",event_id,start,at-timedelta(days=3) if old else at-timedelta(days=2),
                    'HOU','IND','HOU',0.7,0.50,0.51,0.50,'local','schema','candidate','platt','2024-12-31',2025,
                    'a'*64,json.dumps({'status':'PREGAME_SHADOW_UNCERTIFIED','source_snapshot_sha256':'a'*64,
                         'source_snapshot_fetched_at':(at-timedelta(hours=1)).isoformat(),
                         'model_artifact_sha256':'b'*64}))
        insert='''INSERT INTO public.wow_nfl_ml_challenger_forward_shadow
            (challenger_id,official_event_id,event_start_time_utc,prediction_created_at,home_team,away_team,
             selected_participant,calibrated_probability,local_wilson_lower,bootstrap_q10_lower,composite_lower_bound,
             lower_bound_method,feature_schema_version,model_family,calibration_method,training_cutoff,
             calibration_season,source_feature_hash,research_metadata)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING shadow_id'''
        # Freeze an already-existing historical row to exercise the first grade.
        past_id=conn.execute(insert,payload('2026_11_TEST_OLD',at-timedelta(days=1),old=True)).fetchone()[0]
        conn.execute(hardening)
        conn.execute(actual_clock)
        future=payload('2026_12_TEST_NEW',at+timedelta(days=1))
        future_id=conn.execute(insert,future).fetchone()[0]
        rec=conn.execute('''SELECT created_at,prediction_created_at FROM public.wow_nfl_ml_challenger_forward_shadow
                             WHERE shadow_id=%s''',(future_id,)).fetchone()
        assert (at-timedelta(minutes=1)) < rec[0] < at+timedelta(minutes=1)
        assert rec[0]==rec[1], "server timestamp, not the client-supplied clock"
        def denied(query,params):
            try:
                conn.execute(query,params)
            except psycopg.Error:
                return
            raise AssertionError('DB immutability violation was incorrectly accepted')
        denied(insert,payload('2026_12_TEST_POST',at-timedelta(minutes=10)))
        # An insertion statement may begin before kickoff but be delayed by
        # a lock/slow query until after kickoff. Sample real trigger clock.
        delayed = payload('2026_12_TEST_DELAYED',datetime.now(timezone.utc)+timedelta(milliseconds=450))
        delayed_insert = insert.replace('VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                                        'SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s FROM (SELECT pg_sleep(1.2)) wait')
        assert 'pg_sleep' in delayed_insert and delayed_insert != insert
        denied(delayed_insert, delayed)
        denied('''UPDATE public.wow_nfl_ml_challenger_forward_shadow SET calibrated_probability=.8
                  WHERE shadow_id=%s''',(future_id,))
        denied('DELETE FROM public.wow_nfl_ml_challenger_forward_shadow WHERE shadow_id=%s',(future_id,))
        denied('''UPDATE public.wow_nfl_ml_challenger_forward_shadow
                  SET lifecycle_state='GRADED',outcome=1,hit=true,graded_at=now(),brier=.09,log_loss=%s,
                      grade_source_manifest=%s WHERE shadow_id=%s''',
               (-log(.7),json.dumps({'status':'TWO_PROVIDER_RECONCILED_NOT_LEAGUE_CERTIFIED',
                                     'sources':['ESPN','NFLVERSE'],'espn_raw_sha256':'f'*64,
                                     'nflverse_content_sha256':'a'*64}),future_id))
        manifest=json.dumps({'status':'TWO_PROVIDER_RECONCILED_NOT_LEAGUE_CERTIFIED',
                             'sources':['ESPN','NFLVERSE'],'espn_raw_sha256':'f'*64,
                             'nflverse_content_sha256':'a'*64})
        conn.execute('''UPDATE public.wow_nfl_ml_challenger_forward_shadow
                    SET lifecycle_state='GRADED',outcome=1,hit=true,graded_at=now(),brier=%s,log_loss=%s,
                    grade_source_manifest=%s WHERE shadow_id=%s''',(.09,-log(.7),manifest,past_id))
        status=conn.execute('''SELECT lifecycle_state,outcome FROM public.wow_nfl_ml_challenger_forward_shadow
                               WHERE shadow_id=%s''',(past_id,)).fetchone()
        assert status==('GRADED',1)
        denied('''UPDATE public.wow_nfl_ml_challenger_forward_shadow SET brier=.02 WHERE shadow_id=%s''',(past_id,))
        for role in ('anon','authenticated'):
            allowed=conn.execute('''SELECT has_table_privilege(%s,'public.wow_nfl_ml_challenger_forward_shadow','INSERT')''',
                                 (role,)).fetchone()[0]
            assert not allowed
        assert conn.execute('''SELECT has_table_privilege('service_role','public.wow_nfl_ml_challenger_forward_shadow','INSERT')''').fetchone()[0]


if __name__=='__main__':
    test_pg_immutable_contract()
    print('POSTGRES_IMMUTABLE_PROSPECTIVE_CONTRACT_PASS')
