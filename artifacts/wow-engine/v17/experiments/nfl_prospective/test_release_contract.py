"""Ensure protected orchestration cannot slip into model promotion or bad mutation."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]


def test_immutable_migration_rejects_rewrites_and_postgame_inserts():
    sql=(ROOT/'migrations'/'20261117_nfl_prospective_immutable_freeze.sql').read_text().lower()
    assert "new.prediction_created_at := clock_time" in sql
    assert "new.created_at := clock_time" in sql
    assert "new.event_start_time_utc <= clock_time" in sql
    assert "nfl_prospective_frozen_forecast_mutation_denied" in sql
    assert "immutable_delete_denied" in sql
    assert "trigger wow_nfl_prospective_freeze_guard" in sql
    assert "from public, anon, authenticated, service_role" in sql
    assert "grant select, insert, update" in sql
    assert "grant all" not in sql


def test_scheduled_workflow_never_runs_mutations_on_pr():
    wf=(ROOT.parents[1]/'.github'/'workflows'/'wow-v17-nfl-prospective-shadow.yml').read_text().lower()
    assert "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'" in wf
    assert "github.ref == 'refs/heads/main'" in wf
    assert "wow_can_execute: \"false\"" in wf
    assert "run: python -m pytest" in wf
    assert "--confcutdir=v17/experiments/nfl_prospective" in wf
    assert 'pythonpath: "."' in wf
    assert "secrets.supabase_service_role_key" in wf
    assert "cancel-in-progress: false" in wf
    assert "retention-days: 90" in wf
    assert "git push" not in wf


def test_clock_hardening_migration_uses_actual_trigger_execution_clock():
    hardening = (ROOT/'migrations'/'20261117_nfl_prospective_clock_runtime_hardening.sql').read_text().lower()
    assert 'clock_timestamp()' in hardening
    assert 'statement_timestamp()' not in hardening
    assert 'create or replace function public.wow_nfl_prospective_immutable_guard()' in hardening
