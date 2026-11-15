"""Regression: evidence auditing may not turn SHADOW models into certified winners."""
from pathlib import Path
import re

SQL = Path(__file__).resolve().parents[1] / 'scripts' / 'v17_certification_promotion_preflight.sql'


def test_real_registry_names_and_canonical_certification_boundary():
    sql = SQL.read_text()
    assert 'public.wow_team_event_specialist_certifications' in sql
    for table in (
        'wow_basketball_team_event_model_artifacts',
        'wow_basketball_team_event_calibrators',
        'wow_ncaaf_fitted_model_artifacts',
        'wow_prop_fitted_model_artifacts',
        'wow_nfl_forward_calibration_health',
    ):
        assert f'public.{table}' in sql
    for guessed in ('v17_model_registry', 'v17_slate_candidates', 'v17_governance_promotion_receipts'):
        assert guessed not in sql


def test_audit_has_no_mutating_statement_or_production_grants():
    sql = SQL.read_text()
    stripped = re.sub(r'--[^\n]*', '', sql)
    assert re.search(r'\bWITH\b', stripped, flags=re.I)
    for keyword in ('INSERT','UPDATE','DELETE','MERGE','ALTER','DROP','CREATE','GRANT',
                    'REVOKE','TRUNCATE','CALL','EXECUTE','PERFORM','SECURITY DEFINER'):
        assert not re.search(r'\b' + keyword.replace(' ',r'\s+') + r'\b', stripped, flags=re.I), keyword


def test_shadow_evidence_cannot_claim_terminal_acceptance():
    sql = SQL.read_text()
    assert "'CERTIFICATION_REPLAY_REQUIRED'" in sql
    assert "'TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED'" in sql
    assert "'DO_NOT_CERTIFY_YET'" in sql
    assert "'PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND'" in sql
    assert 'FALSE AS can_execute' in sql
    assert 'FALSE AS preflight_authorizes_publication' in sql
    assert "'RECEIPT_PRESENT_REQUIRES_ROUTE_REPLAY'" in sql


def test_no_invented_metric_thresholds_or_blanket_promotion():
    sql=SQL.read_text()
    for token in ('0.2350','0.0500','9065','500','WILSON_CALIBRATED_LOWER_BOUND',
                  "'CERTIFIED_ACTIVE'", "'SHADOW'::text"):
        assert token not in sql

def test_nfl_missing_incomplete_or_nonpassing_health_is_fail_closed():
    sql = SQL.read_text()
    # These guards prevent missing NFL health from being misreported as route-ready.
    for condition in (
        'nh.graded_n IS NULL',
        'nh.minimum_forward_required IS NULL',
        "nh.certification_recommendation IS DISTINCT FROM 'FORWARD_EVIDENCE_PASS'",
        'nh.graded_n < nh.minimum_forward_required',
    ):
        assert condition in sql
    assert "THEN 'DO_NOT_CERTIFY_YET'" in sql


def test_prop_registry_uses_canonical_certified_lifecycle_not_publish_flag():
    sql = SQL.read_text()
    # The prop registry intentionally CHECKs probability_publishable = false;
    # this inventory must match the certified artifact RPC's lifecycle filter.
    assert "lifecycle_state IN ('PROSPECTIVE_CERTIFIED','CHAMPION')" in sql
    assert 'WHERE active AND promoted AND probability_publishable' not in sql
    assert "'PROP_REGISTRY_FLAGS_PRESENT_REQUIRES_CERTIFIED_ROUTE_REPLAY'" in sql
    assert 'FALSE AS preflight_authorizes_publication' in sql


def test_prop_inventory_is_per_stat_family_and_requires_calibrator_and_line_range():
    sql = SQL.read_text()
    assert 'GROUP BY upper(btrim(sport)), upper(btrim(stat_type))' in sql
    assert 'FROM requested_prop_families rpf' in sql
    assert 'FULL OUTER JOIN prop_model_counts pm' in sql
    assert 'AND pm.stat_type = upper(btrim(rpf.stat_type))' in sql
    assert "NULLIF(btrim(calibrator_version),'') IS NOT NULL" in sql
    assert 'supported_line_min IS NOT NULL' in sql
    assert 'FALSE AS preflight_authorizes_publication' in sql
    assert 'FALSE AS can_execute' in sql


PREFLIGHT_PROP_SPORTS = ('MLB', 'NBA', 'NFL', 'WNBA')
MANIFEST = Path(__file__).resolve().parents[1] / 'v17' / 'prop_capability_manifest.py'


def _declared_prop_families():
    """The governed expected set: DECLARED_PROP_LANES for the requested sports."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location('prop_capability_manifest_under_test', MANIFEST)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses introspect their own module
    spec.loader.exec_module(module)
    return {
        (sport, stat_type)
        for (sport, stat_type) in module.DECLARED_PROP_LANES
        if sport in PREFLIGHT_PROP_SPORTS
    }


def _preflight_prop_families(sql):
    block = re.search(r'requested_prop_families\(sport, stat_type\) AS \((.*?)\n\),', sql, re.S)
    assert block is not None, 'the preflight must enumerate its expected prop families'
    return set(re.findall(r"\('([A-Z]+)', '([A-Z0-9_]+)'\)", block.group(1)))


def test_expected_prop_families_are_the_governed_declared_set():
    enumerated = _preflight_prop_families(SQL.read_text())
    declared = _declared_prop_families()
    assert enumerated == declared, sorted(enumerated ^ declared)
    # Dropping a declared family here would silently skip its not-found finding.
    assert {sport for sport, _stat in enumerated} == set(PREFLIGHT_PROP_SPORTS)
    assert len(enumerated) >= 30, len(enumerated)


def test_declared_family_without_a_registry_row_still_reports_not_found():
    sql = SQL.read_text()
    # The declared set drives the inventory, so a family the registry has never
    # seen keeps its row and reports NOT_FOUND; registry families the manifest
    # does not declare stay visible instead of vanishing from the audit.
    assert 'FULL OUTER JOIN prop_model_counts pm' in sql
    assert 'COALESCE(pm.active_promoted_artifacts,0) AS active_promoted_artifacts' in sql
    assert 'WHERE COALESCE(rpf.sport, pm.sport) IN (SELECT sport FROM requested_props)' in sql
    assert "THEN 'PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND'" in sql


# ---------------------------------------------------------------------------
# Executed-query regression: runs the real SQL against an ephemeral local
# PostgreSQL cluster with minimal stand-in tables. No production/validation
# credentials; nothing outside a temp directory is touched.
# ---------------------------------------------------------------------------
import glob
import os
import shutil
import socket
import subprocess
import tempfile

import pytest


def _pg_bin():
    found = sorted(glob.glob('/usr/lib/postgresql/*/bin/initdb'))
    if found:
        return os.path.dirname(found[-1])
    initdb = shutil.which('initdb')
    return os.path.dirname(initdb) if initdb else None


_STUB_DDL = """
create schema if not exists public;
create table public.wow_team_event_specialist_certifications(
  sport text, league_scope text, certification_id text, certification_status text,
  independent_verification_status text, probability_publishable boolean,
  artifact_id text, artifact_sha256 text, calibration_artifact_id text,
  calibration_sha256 text, created_at timestamptz default now());
create table public.wow_basketball_team_event_model_artifacts(sport text, active boolean, promoted boolean);
create table public.wow_basketball_team_event_calibrators(sport text, active boolean, promoted boolean);
create table public.wow_basketball_team_event_certification(sport text, capability_status text, reason_code text);
create table public.wow_ncaaf_fitted_model_artifacts(active boolean, promoted boolean);
create table public.wow_ncaaf_calibrator_artifacts(active boolean, probability_publishable boolean);
create table public.wow_nfl_forward_calibration_health(
  health_id bigint, graded_n int, minimum_forward_required int, status text,
  certification_recommendation text, generated_at timestamptz);
create table public.wow_prop_fitted_model_artifacts(
  sport text, stat_type text, active boolean, promoted boolean, lifecycle_state text,
  calibrator_version text, supported_line_min numeric, supported_line_max numeric);
insert into public.wow_prop_fitted_model_artifacts values
  -- same sport, one certified family (mixed case + padding) and one uncertified family
  ('nfl ', 'Passing_Yards', true, true, 'CHAMPION', 'cal-v1', 10, 400),
  ('NFL',  'RUSHING_YARDS', true, true, 'CHAMPION', '   ',      10, 200),
  ('NFL',  'RECEIVING_YARDS', true, true, 'CHAMPION', 'cal-v1', null, null),
  ('NFL',  'RECEPTIONS', true, true, 'CANDIDATE', 'cal-v1', 1, 10),
  ('MLB',  'HITS', true, true, 'PROSPECTIVE_CERTIFIED', 'cal-v1', 0.5, 3.5);
"""


@pytest.fixture(scope='module')
def preflight_rows():
    bindir = _pg_bin()
    if bindir is None:
        pytest.skip('PostgreSQL server binaries not installed; executed-query regression needs them')
    root = os.geteuid() == 0
    base = tempfile.mkdtemp(prefix='wow_preflight_pg_')
    run_as = []
    if root:
        os.chmod(base, 0o755)
        shutil.chown(base, user='postgres')
        run_as = ['runuser', '-u', 'postgres', '--']
    data = os.path.join(base, 'data')
    sock = base
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    env = dict(os.environ, LC_ALL='C')
    try:
        subprocess.run(run_as + [f'{bindir}/initdb', '-D', data, '-A', 'trust', '-U', 'wowtest'],
                       check=True, capture_output=True, env=env)
        # Detach the server's stdio from our pipes (a captured pipe would keep pg_ctl waiting).
        subprocess.run(run_as + [f'{bindir}/pg_ctl', '-D', data, '-w', '-t', '60',
                                 '-l', os.path.join(base, 'pg.log'), '-o',
                                 f"-p {port} -c listen_addresses='' -k {sock}", 'start'],
                       check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, env=env, timeout=90)
        psql = run_as + [shutil.which('psql') or 'psql', '-h', sock, '-p', str(port), '-U', 'wowtest',
                         '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-X', '-q']
        subprocess.run(psql, input=_STUB_DDL, text=True, check=True, capture_output=True, env=env)
        out = subprocess.run(psql + ['-A', '-F', '|', '-t', '-f', str(SQL)],
                             text=True, check=True, capture_output=True, env=env).stdout
    finally:
        subprocess.run(run_as + [f'{bindir}/pg_ctl', '-D', data, '-m', 'immediate', 'stop'],
                       capture_output=True, env=env)
        shutil.rmtree(base, ignore_errors=True)
    rows = [line.split('|') for line in out.splitlines() if line.strip()]
    return rows


def _prop(rows):
    return {(r[0], r[2]): r[11] for r in rows if r[1] == 'WOW_PROP_LANE'}


def test_executed_query_separates_certified_and_uncertified_families_in_same_sport(preflight_rows):
    prop = _prop(preflight_rows)
    flags = 'PROP_REGISTRY_FLAGS_PRESENT_REQUIRES_CERTIFIED_ROUTE_REPLAY'
    missing = 'PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND'
    # 'nfl ' / 'Passing_Yards' normalizes into the NFL request and is certified.
    assert prop[('NFL', 'PASSING_YARDS')] == flags
    # Whitespace-only calibrator, null line range and CANDIDATE lifecycle never certify.
    assert prop[('NFL', 'RUSHING_YARDS')] == missing
    assert prop[('NFL', 'RECEIVING_YARDS')] == missing
    assert prop[('NFL', 'RECEPTIONS')] == missing
    assert prop[('MLB', 'HITS')] == flags


def test_executed_query_inventories_declared_families_without_registry_rows(preflight_rows):
    prop = _prop(preflight_rows)
    missing = 'PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND'
    # Declared families with no registry row still get a row and a finding.
    assert prop[('NBA', 'PRA')] == missing
    assert prop[('WNBA', 'POINTS')] == missing
    assert prop[('NFL', 'FANTASY_SCORE')] == missing
    assert prop[('MLB', 'PITCHER_STRIKEOUTS')] == missing
    # Every requested sport is inventoried, not just the ones with artifacts.
    assert {sport for sport, _stat in prop} == set(PREFLIGHT_PROP_SPORTS)
    # A certified registry family the manifest does not declare stays visible.
    assert prop[('MLB', 'HITS')] == 'PROP_REGISTRY_FLAGS_PRESENT_REQUIRES_CERTIFIED_ROUTE_REPLAY'
    # Every row, team and prop, keeps the non-authorizing flags.
    assert preflight_rows
    for r in preflight_rows:
        assert r[-2] == 'f' and r[-1] == 'f', r
