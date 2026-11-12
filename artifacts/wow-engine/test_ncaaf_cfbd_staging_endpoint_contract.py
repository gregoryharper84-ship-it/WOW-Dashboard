"""Permanent regression for CFBD /games/players DB staging admission.

Exact mismatch caused the Oct 28 NCAAF worker to acquire player stats
and then fail PostgREST with a SQL CHECK violation.
"""
from pathlib import Path
from ncaaf_cfbd_client import _ALLOWED_ENDPOINTS

STAGING = Path(__file__).with_name("ncaaf_cfbd_staging.sql")


def test_cfbd_player_stats_route_matches_staging_sql_contract():
    sql = STAGING.read_text(encoding="utf-8")
    assert "/games/players" in _ALLOWED_ENDPOINTS
    # Both fresh creation and in-place upgrades must admit the documented
    # player-game route. A CREATE TABLE IF NOT EXISTS alone is insufficient.
    assert sql.count("'/games/players'") == 2
    create, upgrade = sql.split(
        "drop constraint if exists wow_ncaaf_source_endpoint;", 1
    )
    assert "'/games/players'" in create
    assert "add constraint wow_ncaaf_source_endpoint" in upgrade
    assert "'/games/players'" in upgrade


def test_staging_retains_research_only_security():
    sql = STAGING.read_text(encoding="utf-8").lower()
    assert "enable row level security" in sql
    assert "revoke all on table public.wow_ncaaf_source_snapshots from anon, authenticated" in sql
    assert "constraint wow_ncaaf_source_never_execute check (can_execute = false)" in sql
    assert "grant all on table public.wow_ncaaf_source_snapshots to service_role" in sql
    for route in ("/games", "/games/players", "/ratings/core", "/ratings/sp",
                  "/ratings/srs", "/ratings/elo", "/ratings/fpi"):
        assert "'" + route + "'" in sql
