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


def test_nfl_lookup_enforces_fitted_artifact_publication_and_exact_identity_join():
    cur = FakeCursor(None)
    result = lookup_governed_probability(cur, _candidate())
    sql, params = cur.calls[0]
    assert "join public.wow_nfl_event_fitted_model_artifacts as artifact" in sql
    assert "artifact.artifact_id=p.model_artifact_id" in sql
    assert "artifact.probability_publishable=true" in sql
    assert "artifact.can_execute=false" in sql
    assert "p.model_probability_publishable=true" in sql
    assert "p.event_key=%s" in sql
    assert params[0] == "NFL:espn-401872931"
    assert result["governed_probability"] is None
    assert result["probability_publishable"] is False


def test_unpublished_fitted_artifact_row_is_never_displayed():
    # A database JOIN excludes an otherwise row-publishable prediction when
    # the referenced fitted artifact is unpublished. No probability substitution.
    class ArtifactAwareCursor(FakeCursor):
        def fetchone(self):
            sql, _ = self.calls[-1]
            if ("artifact.probability_publishable=true" in sql
                    and self.artifact_publishable is False):
                return None
            return self.row

    cur = ArtifactAwareCursor((
        "Denver Broncos", 0.5796689600932923, 0.523750876984715,
        True, "NFL_TEAM_EVENT_V1", datetime(2026, 9, 14, 20, 40, tzinfo=timezone.utc)
    ))
    cur.artifact_publishable = False
    display = _display_row(cur, _candidate())
    assert display["probability"] is None
    assert display["governed_probability"] is None
    assert display["probability_display"] == "—"
    assert display["probability_publishable"] is False
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
    assert result["specialist_status"] == "PENDING_SPECIALIST"
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
