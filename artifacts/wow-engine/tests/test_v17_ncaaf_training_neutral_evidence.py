"""CFBD neutral-site observations must not be fabricated during NCAAF research."""
from types import SimpleNamespace

import pytest

from ncaaf_cfbd_hydrator import SourceSnapshot
from ncaaf_training_materializer import _game_row, materialize_training_games


def _source_game(neutral_marker=False):
    row = {
        "id": 401800001,
        "completed": True,
        "homePoints": 31,
        "awayPoints": 24,
        "startDate": "2026-09-12T18:00:00+00:00",
        "homeTeam": "Louisville",
        "awayTeam": "Florida State",
        "week": 2,
        "season": 2026,
        "seasonType": "regular",
        "neutralSite": neutral_marker,
    }
    return row


def _snapshot(rows):
    return SourceSnapshot(
        provider="CFBD", endpoint="/games", season=2026, week=2,
        requested_at="2026-09-12T19:00:00+00:00",
        retrieved_at="2026-09-12T23:00:00+00:00",
        request_params={"year": 2026},
        response_rows=rows, response_row_count=len(rows),
        payload_sha256="a" * 64, acquisition_status="AVAILABLE",
        blocker_codes=[], can_execute=False,
    )


class _DB:
    def __init__(self):
        self.rows = []
        self.writes = 0

    def table(self, table):
        assert table == "wow_ncaaf_training_games"
        return self

    def upsert(self, rows, **kwargs):
        assert kwargs["on_conflict"] == "official_event_id"
        self.writes += 1
        self.rows.extend(rows)
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


@pytest.mark.parametrize("neutral", [False, True])
def test_materializer_preserves_explicit_boolean_neutral_flag(neutral):
    game = _source_game(neutral)
    db = _DB()
    report = materialize_training_games(db, [_snapshot([game])])
    assert report.candidate_rows == report.persisted_rows == 1
    assert report.skipped_rows == 0
    assert report.blocker_codes == ()
    assert db.rows[0]["neutral_site"] is neutral
    assert db.rows[0]["can_execute"] is False
    assert report.can_execute is False


@pytest.mark.parametrize("neutral", [None, "", "false", "true", 0, 1, []])
def test_missing_or_nonboolean_neutral_site_is_not_coerced_or_persisted(neutral):
    game = _source_game()
    if neutral is None:
        del game["neutralSite"]
    else:
        game["neutralSite"] = neutral
    db = _DB()
    report = materialize_training_games(db, [_snapshot([game])])
    assert report.candidate_rows == report.persisted_rows == 0
    assert report.skipped_rows == 1
    assert "NCAAF_TRAINING_NEUTRAL_SITE_EVIDENCE_MISSING" in report.blocker_codes
    assert "NCAAF_NO_SETTLED_TRAINING_GAMES_MATERIALIZED" in report.blocker_codes
    assert db.writes == 0
    assert report.can_execute is False


def test_mixed_snapshot_retains_good_rows_but_surfaces_missing_source_evidence():
    valid = _source_game(False)
    invalid = _source_game()
    invalid["id"] = 401800002
    invalid.pop("neutralSite")
    db = _DB()
    result = materialize_training_games(db, [_snapshot([valid, invalid])])
    assert result.candidate_rows == result.persisted_rows == 1
    assert result.skipped_rows == 1
    assert result.blocker_codes == ("NCAAF_TRAINING_NEUTRAL_SITE_EVIDENCE_MISSING",)
    assert db.rows[0]["official_event_id"] == "401800001"
    assert db.rows[0]["neutral_site"] is False
    assert result.can_execute is False


def test_incomplete_nonsettled_row_does_not_claim_missing_neutral_source():
    row = _source_game()
    row["completed"] = False
    row.pop("neutralSite")
    db = _DB()
    result = materialize_training_games(db, [_snapshot([row])])
    assert result.blocker_codes == ("NCAAF_NO_SETTLED_TRAINING_GAMES_MATERIALIZED",)
    assert result.skipped_rows == 1
    assert db.writes == 0
