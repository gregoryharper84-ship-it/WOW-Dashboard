from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "20261007_v17_mlb_prelineup_identity_evidence.sql"


def _sql() -> str:
    return MIGRATION.read_text()


def test_prelineup_identity_repair_is_canonical_identity_only():
    sql = _sql()
    assert "wow_v17_hydrate_mlb_prelineup_identity_evidence" in sql
    assert "MLB_STATS_API_CANONICAL_LEDGER" in sql
    assert "se.source_game_json->>'gamePk' is distinct from r.official_event_id" in sql
    assert "se.home_team is distinct from r.home_team" in sql
    assert "se.away_team is distinct from r.away_team" in sql
    assert "se.event_start_time is distinct from r.event_start_time" in sql
    assert "'OFFICIAL_EVENT_ID'" in sql
    assert "'OFFICIAL','RETRIEVED'" in sql
    assert "'lineup_evidence_claimed', false" in sql
    assert "'probability_authority', false" in sql
    assert "'can_execute', false" in sql


def test_prelineup_identity_repair_does_not_claim_other_governance_evidence():
    sql = _sql()
    helper = sql.split("create or replace function public.wow_v17_hydrate_mlb_prelineup_identity_evidence", 1)[1]
    helper = helper.split("do $patch$", 1)[0]
    for evidence_kind in (
        "HOME_LINEUP",
        "AWAY_LINEUP",
        "BULLPEN_STATUS",
        "WEATHER_STATUS",
        "INJURY_STATUS",
    ):
        assert f"'{evidence_kind}'" not in helper


def test_probability_only_bridge_runs_identity_handoff_before_full_hydration():
    sql = _sql()
    early = "perform public.wow_v17_hydrate_mlb_prelineup_identity_evidence(event_id,p_score_snapshot_id);"
    full = "perform public.wow_v17_hydrate_mlb_event_governance_evidence(event_id,p_score_snapshot_id,''{}''::jsonb,p_decision_intent);"
    replacement_line = next(
        line for line in sql.splitlines() if line.strip().startswith("replacement text :=")
    )
    assert early in sql
    assert full in sql
    assert replacement_line.index("wow_v17_hydrate_mlb_prelineup_identity_evidence") < replacement_line.index(
        "wow_v17_hydrate_mlb_event_governance_evidence"
    )


def test_repair_preserves_existing_global_terminal_reducer():
    sql = _sql()
    assert "wow_reduce_event_terminal_label" not in sql
    assert "terminal_label=" not in sql.lower()
    assert "rank_eligible=true" not in sql.lower()
    assert "probability_publishable=true" not in sql.lower()
