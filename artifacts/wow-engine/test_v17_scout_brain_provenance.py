from __future__ import annotations

from v17 import scout_brain_persistence as persistence


def _row():
    return {
        "official_event_id": "evt-1",
        "sport_key": "baseball_mlb",
        "commence_time": "2026-09-14T20:00:00Z",
        "home_team": "Texas Rangers",
        "away_team": "Houston Astros",
        "route": "LLP_TEAM_BETTING_ENGINE",
        "controlling_specialist_route": "LLP_TEAM_BETTING_ENGINE",
        "scout_team_id": "MLB_SCOUT_TEAM",
        "research_status": "RESEARCH_INTEREST_LOW",
        "data_completeness": 100.0,
        "source_freshness_score": 96.0,
        "market_evidence_status": "AVAILABLE",
        "market_evidence": {
            "bookmaker": "ESPN",
            "source_provider": "ESPN_SCOREBOARD_RESEARCH_FALLBACK",
            "source_class": "SECONDARY_MEDIA",
            "market_key": "h2h",
            "outcome_name": "Texas Rangers",
            "price": -120,
            "market_last_update": "2026-09-14T19:58:00Z",
            "prediction_authority": False,
            "can_execute": False,
        },
        "can_execute": False,
    }


def test_source_snapshot_identity_is_deterministic_and_non_candidate_scoped():
    row = _row()
    evidence = row["market_evidence"]
    sid1, payload1, hash1 = persistence.stable_source_snapshot_id(row, evidence)
    sid2, payload2, hash2 = persistence.stable_source_snapshot_id(row, dict(evidence))
    assert sid1 == sid2
    assert payload1 == payload2
    assert hash1 == hash2
    changed = dict(evidence)
    changed["price"] = -125
    sid3, _, _ = persistence.stable_source_snapshot_id(row, changed)
    assert sid3 != sid1


def test_priority_score_counts_list_evidence_rows():
    row = _row()
    row["market_evidence"] = [dict(row["market_evidence"]), {**row["market_evidence"], "bookmaker": "BetMGM"}]
    assert persistence.priority_score(row) == 39.0


class FakeCursor:
    def __init__(self):
        self.statements = []
        self._fetchone = (True,)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.statements.append((" ".join(sql.split()), params))
        if "returning (xmax = 0)" in sql:
            self._fetchone = (True,)

    def fetchone(self):
        return self._fetchone


class FakeConnection:
    def __init__(self):
        self.cur = FakeCursor()
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed = True


def test_persist_handoff_writes_observation_snapshot_and_link(monkeypatch):
    conn = FakeConnection()
    monkeypatch.setattr(persistence, "database_url", lambda: "postgresql://unused")
    monkeypatch.setattr(persistence.psycopg, "connect", lambda url: conn)
    handoff = {
        "status": "DISCOVERY_COMPLETE",
        "run_id": "run-1",
        "research_run_id": "research-1",
        "model_handoff": {"team_event_candidates": [_row()], "prop_candidates": []},
        "can_execute": False,
    }
    result = persistence.persist_handoff(handoff)
    sql = "\n".join(statement for statement, _ in conn.cur.statements)
    assert "insert into wow_scout.observations" in sql
    assert "insert into wow_scout.source_snapshots" in sql
    assert "insert into wow_scout.candidate_source_links" in sql
    assert result["observation_count"] == 1
    assert result["source_snapshot_count"] == 1
    assert result["candidate_source_link_count"] == 1
    assert result["can_execute"] is False
    assert conn.committed is True
