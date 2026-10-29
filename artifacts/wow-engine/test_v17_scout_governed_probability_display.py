from __future__ import annotations

from datetime import datetime, timezone

from v17.scout_board_materializer import _display_row
from v17.scout_governed_probability_lookup import lookup_governed_probability


class FakeCursor:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.row


def _candidate(**overrides):
    row = {
        "candidate_id": "scout-nfl-1",
        "sport_key": "americanfootball_nfl",
        "market_type": "TEAM_EVENT",
        "selection": None,
        "event_id": "espn-401872931",
        "canonical_event_id": None,
        "commence_time": datetime(2026, 9, 15, 0, 15, tzinfo=timezone.utc),
        "home_team": "Kansas City Chiefs",
        "away_team": "Denver Broncos",
        "controlling_specialist": "LLP_TEAM_BETTING_ENGINE",
        "research_status": "RESEARCH_INTEREST_LOW",
        "research_priority_score": 65.0,
        "thesis": "Fresh multi-book research evidence",
        "edge_classes": ["MARKET_DISAGREEMENT"],
        "contradictory_evidence": [],
        "red_team_flags": [],
        "can_execute": False,
    }
    row.update(overrides)
    return row


def test_nfl_publishable_specialist_probability_is_resolved_without_scout_probability_authority():
    cur = FakeCursor((
        "Denver Broncos",
        0.5796689600932923,
        0.523750876984715,
        True,
        "NFL_TEAM_EVENT_V1",
        datetime(2026, 9, 14, 20, 40, tzinfo=timezone.utc),
    ))
    result = lookup_governed_probability(cur, _candidate())
    assert result["governed_selection"] == "Denver Broncos"
    assert result["governed_probability"] == 0.5796689600932923
    assert result["calibrated_lower_bound"] == 0.523750876984715
    assert result["probability_publishable"] is True
    assert result["probability_source"] == "CONTROLLING_SPECIALIST"
    assert result["can_execute"] is False
    assert cur.calls[0][1][0] == "NFL:espn-401872931"


def test_nfl_lookup_requires_champion_and_latest_actual_daily_terminal():
    cur = FakeCursor(None)
    result = lookup_governed_probability(cur, _candidate())
    sql, params = cur.calls[0]
    for contract in (
        "artifact.artifact_id=p.model_artifact_id",
        "artifact.active=true",
        "artifact.promoted=true",
        "artifact.lifecycle_state='CHAMPION'",
        "artifact.can_execute=false",
        "p.model_probability_publishable=true",
        "join lateral (",
        "public.wow_v17_daily_run_row_detail",
        "daily.identity->>'sport'='NFL'",
        "daily.identity->>'official_event_id'=p.official_event_id",
        "order by daily.captured_at desc,daily.row_index desc,daily.run_id desc",
        "limit 1",
        "latest.row_status='COMPLETED'",
        "latest.can_execute=false",
        "latest.detail->'result'->>'global_terminal_authority'='V17_TERMINAL_REDUCER'",
        "latest.detail->'result'->>'event_prediction_id'=p.event_prediction_id::text",
        "latest.detail->'result'->>'selected_participant'=p.selected_participant",
        "latest.detail->'result'->>'terminal_label'='FINAL_APPROVED'",
        "latest.detail->'result'->>'terminal_ceiling'='FINAL_APPROVED'",
        "latest.detail->'result'->>'probability_publishable'='true'",
        "latest.detail->'result'->>'rank_eligible'='true'",
        "latest.detail->>'probability_publishable'='true'",
        "latest.captured_at >= p.created_at",
        "latest.captured_at >= p.event_start_time_utc - interval '7 days'",
        "latest.captured_at < p.event_start_time_utc",
    ):
        assert contract in sql
    assert "public.wow_agent_run_candidates" not in sql
    assert "artifact.probability_publishable=true" not in sql
    assert params[0] == "NFL:espn-401872931"
    assert result["governed_probability"] is None
    assert result["probability_publishable"] is False
    assert result["specialist_status"] == "TERMINAL_PROOF_NOT_VERIFIED"


def test_later_terminal_hold_must_supersede_any_earlier_approval():
    # Detect a query mutation that filters to APPROVED inside the lateral
    # lookup and could revive an earlier pass after a newer terminal HOLD.
    cur = FakeCursor(None)
    lookup_governed_probability(cur, _candidate())
    sql = cur.calls[0][0]
    before_latest = sql.split(") as latest on true", 1)[0]
    after_latest = sql.split(") as latest on true", 1)[1]
    assert "daily.row_status='COMPLETED'" not in before_latest
    assert "daily.detail->'result'->>'terminal_label'='FINAL_APPROVED'" not in before_latest
    assert "latest.row_status='COMPLETED'" in after_latest
    assert "latest.detail->'result'->>'terminal_label'='FINAL_APPROVED'" in after_latest


def test_latest_terminal_requires_exact_prediction_and_fresh_pregame_receipt():
    cur = FakeCursor(None)
    lookup_governed_probability(cur, _candidate())
    sql = cur.calls[0][0]
    assert "latest.detail->'result'->>'event_prediction_id'=p.event_prediction_id::text" in sql
    assert "latest.detail->'result'->>'selected_participant'=p.selected_participant" in sql
    assert "latest.captured_at >= p.created_at" in sql
    assert "latest.captured_at < p.event_start_time_utc" in sql
    assert "latest.detail->'result'->>'global_terminal_authority'='V17_TERMINAL_REDUCER'" in sql


def test_absent_terminal_proof_never_displays_a_row_publishable_prediction():
    # The real Postgres query returns no row if champion lifecycle OR exact
    # terminal/reducer evidence is missing. A fake cursor must not pretend that
    # a model row is itself independent FINAL_APPROVED proof.
    cur = FakeCursor(None)
    display = _display_row(cur, _candidate())
    assert display["probability"] is None
    assert display["governed_probability"] is None
    assert display["probability_display"] == "—"
    assert display["probability_publishable"] is False
    assert display["specialist_status"] == "TERMINAL_PROOF_NOT_VERIFIED"
    assert display["can_execute"] is False


def test_unavailable_artifact_ledger_fails_closed_and_never_derives_probability():
    class QueryFailureCursor:
        def execute(self, sql, params):
            raise RuntimeError("artifact ledger unavailable")

    result = lookup_governed_probability(QueryFailureCursor(), _candidate())
    assert result["governed_probability"] is None
    assert result["probability_publishable"] is False
    assert result["specialist_status"] == "SPECIALIST_LEDGER_UNAVAILABLE"


def test_board_row_keeps_legacy_scout_probability_null_and_formats_governed_score_beside_pick():
    cur = FakeCursor((
        "Denver Broncos",
        0.5796689600932923,
        0.523750876984715,
        True,
        "NFL_TEAM_EVENT_V1",
        datetime(2026, 9, 14, 20, 40, tzinfo=timezone.utc),
    ))
    result = _display_row(cur, _candidate())
    assert result["pick"] == "Denver Broncos"
    assert result["probability"] is None
    assert result["governed_probability_pct"] == 57.97
    assert result["calibrated_lower_bound_pct"] == 52.38
    assert result["probability_display"] == "57.97%"
    assert result["lower_bound_display"] == "52.38%"
    assert result["probability_source"] == "CONTROLLING_SPECIALIST"
    assert result["can_execute"] is False


def test_missing_publishable_specialist_result_displays_pending_not_market_probability():
    cur = FakeCursor(None)
    result = _display_row(cur, _candidate())
    assert result["probability"] is None
    assert result["governed_probability"] is None
    assert result["calibrated_lower_bound"] is None
    assert result["probability_display"] == "—"
    assert result["specialist_status"] == "TERMINAL_PROOF_NOT_VERIFIED"
    assert result["probability_publishable"] is False


def test_unsupported_sport_fails_closed_as_model_unavailable():
    cur = FakeCursor(None)
    result = lookup_governed_probability(cur, _candidate(sport_key="basketball_nba"))
    assert result["governed_probability"] is None
    assert result["specialist_status"] == "MODEL_UNAVAILABLE"
    assert result["probability_publishable"] is False
    assert cur.calls == []


def test_malformed_scout_governance_never_displays_probability():
    cur = FakeCursor(("Denver Broncos", 0.9, 0.8, True, "bad", datetime.now(timezone.utc)))
    result = lookup_governed_probability(cur, _candidate(can_execute=True))
    assert result["governed_probability"] is None
    assert result["specialist_status"] == "SCOUT_GOVERNANCE_INVALID"
    assert cur.calls == []
