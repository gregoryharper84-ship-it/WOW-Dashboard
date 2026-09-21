from __future__ import annotations

import copy

import pytest

import nhl_skater_sog_ingestion as sog


def _team(team_id: int, abbrev: str) -> dict:
    return {"id": team_id, "abbrev": abbrev}


def _skater(player_id: int, name: str, position: str, shots: int, toi: str = "18:04") -> dict:
    return {
        "playerId": player_id,
        "name": {"default": name},
        "position": position,
        "sog": shots,
        "toi": toi,
    }


def _base_boxscore(**overrides) -> dict:
    payload = {
        "id": "2025020123",
        "season": "20252026",
        "gameState": "OFF",
        "startTimeUTC": "2025-11-01T00:00:00Z",
        "homeTeam": _team(10, "TOR"),
        "awayTeam": _team(6, "BOS"),
        "playerByGameStats": {
            "homeTeam": {
                "forwards": [_skater(8478402, "Connor McDavid Clone", "C", 5)],
                "defense": [_skater(8471675, "Home Dman", "D", 2)],
            },
            "awayTeam": {
                "forwards": [_skater(8477956, "Away Forward", "LW", 0)],
                "defense": [_skater(8479999, "Away Dman", "D", 3)],
            },
        },
    }
    payload.update(overrides)
    return payload


def test_normal_settled_game_produces_correct_records():
    result = sog.ingest_settled_game_skater_sog(_base_boxscore(), source_uri="https://api-web.nhle.com/test", retrieved_at="2025-11-02T00:00:00Z")
    assert result.canonical_game_id == "2025020123"
    assert not result.postponed
    assert len(result.records) == 4
    by_player = {r.player_id: r for r in result.records}
    assert by_player["8478402"].actual_value == 5
    assert by_player["8478402"].participation_status == sog.PARTICIPATION_DRESSED_PLAYED
    assert by_player["8478402"].team_id == "10"
    assert by_player["8477956"].team_id == "6"
    assert by_player["8478402"].season_label == "2025-2026"
    assert by_player["8478402"].provider_season_id == "20252026"


def test_zero_sog_dressed_skater_is_a_legitimate_zero():
    result = sog.ingest_settled_game_skater_sog(_base_boxscore(), source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    zero_record = next(r for r in result.records if r.player_id == "8477956")
    assert zero_record.participation_status == sog.PARTICIPATION_DRESSED_PLAYED
    assert zero_record.actual_value == 0
    assert zero_record.actual_value is not None


def test_scratch_dnp_is_not_converted_into_a_zero_sog_observation():
    boxscore = _base_boxscore()
    scratches = [{"teamId": 10, "playerId": 9000001}]
    result = sog.ingest_settled_game_skater_sog(boxscore, scratches_raw=scratches, source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    scratch_record = next(r for r in result.records if r.player_id == "9000001")
    assert scratch_record.participation_status == sog.PARTICIPATION_SCRATCHED_DNP
    assert scratch_record.actual_value is None  # never 0 -- distinct from a dressed zero


def test_traded_player_resolves_to_updated_team_id_across_games():
    game_a = _base_boxscore(id="G1")
    game_b = _base_boxscore(
        id="G2",
        homeTeam=_team(20, "NYR"),
        playerByGameStats={
            "homeTeam": {"forwards": [_skater(8478402, "Connor McDavid Clone", "C", 4)], "defense": []},
            "awayTeam": {"forwards": [], "defense": []},
        },
    )
    result_a = sog.ingest_settled_game_skater_sog(game_a, source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    result_b = sog.ingest_settled_game_skater_sog(game_b, source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    rec_a = next(r for r in result_a.records if r.player_id == "8478402")
    rec_b = next(r for r in result_b.records if r.player_id == "8478402")
    assert rec_a.team_id == "10"
    assert rec_b.team_id == "20"
    assert rec_a.player_id == rec_b.player_id  # same canonical identity, no new player_id created


def test_player_name_ambiguity_never_merges_distinct_player_ids():
    boxscore = _base_boxscore(
        playerByGameStats={
            "homeTeam": {"forwards": [_skater(111, "John Smith", "C", 2)], "defense": []},
            "awayTeam": {"forwards": [_skater(222, "John Smith", "LW", 1)], "defense": []},
        }
    )
    result = sog.ingest_settled_game_skater_sog(boxscore, source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    ids = {r.player_id for r in result.records}
    assert ids == {"111", "222"}
    assert len(result.records) == 2


def test_provider_id_participation_conflict_is_unresolved_not_guessed():
    boxscore = _base_boxscore()
    # Player 8478402 is dressed-with-stats AND separately reported scratched
    # for the same team/game: a genuine source data conflict.
    scratches = [{"teamId": 10, "playerId": 8478402}]
    result = sog.ingest_settled_game_skater_sog(boxscore, scratches_raw=scratches, source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    assert not any(r.player_id == "8478402" for r in result.records)
    codes = {u.reason_code for u in result.unresolved}
    assert "NHL_SOG_PARTICIPATION_CONFLICT" in codes


def test_postponed_game_produces_no_fabricated_records():
    boxscore = _base_boxscore(gameState="PPD")
    result = sog.ingest_settled_game_skater_sog(boxscore, source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    assert result.postponed is True
    assert result.records == ()
    assert result.unresolved == ()


def test_global_stat_alias_normalization():
    for alias in ("Shots on Goal", "SOG", "  sog  ", "Player Shots on Goal", "shots_on_goal"):
        assert sog.resolve_stat_alias(alias) == sog.STAT_TYPE


def test_generic_shots_alias_unrecognized_without_source_scope():
    with pytest.raises(sog.NHLSogIngestionError) as exc:
        sog.resolve_stat_alias("Shots")
    assert exc.value.code == "PROP_STAT_ALIAS_UNRECOGNIZED"


def test_source_scoped_shots_alias_only_applies_to_its_platform():
    sog.SOURCE_SCOPED_STAT_ALIASES["PRIZEPICKS"] = {"shots": sog.STAT_TYPE}
    try:
        assert sog.resolve_stat_alias("Shots", platform="PRIZEPICKS") == sog.STAT_TYPE
        with pytest.raises(sog.NHLSogIngestionError):
            sog.resolve_stat_alias("Shots", platform="UNDERDOG")
        with pytest.raises(sog.NHLSogIngestionError):
            sog.resolve_stat_alias("Shots")  # still unrecognized with no platform at all
    finally:
        sog.SOURCE_SCOPED_STAT_ALIASES.pop("PRIZEPICKS", None)


def test_canonical_source_id_reconciled_in_manifest():
    import json
    with open("historical_source_manifest_v1.json") as f:
        manifest = json.load(f)
    nhl_providers = {s["provider"] for s in manifest["sources"] if s["sport"] == "NHL"}
    assert "NHL_PUBLIC_API" not in nhl_providers
    assert sog.SOURCE_ID in nhl_providers


def test_duplicate_ingestion_is_idempotent_no_duplication():
    boxscore = _base_boxscore()
    result_1 = sog.ingest_settled_game_skater_sog(boxscore, source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    result_2 = sog.ingest_settled_game_skater_sog(boxscore, source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    merged_once = sog.merge_records((), result_1.records)
    merged_twice = sog.merge_records(merged_once, result_2.records)
    assert len(merged_twice) == len(result_1.records)
    assert set(r.canonical_key for r in merged_twice) == set(r.canonical_key for r in result_1.records)


def test_deterministic_replay_produces_bit_identical_records():
    boxscore = _base_boxscore()
    result_1 = sog.ingest_settled_game_skater_sog(boxscore, source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    result_2 = sog.ingest_settled_game_skater_sog(copy.deepcopy(boxscore), source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    assert result_1.source_payload_sha256 == result_2.source_payload_sha256
    assert tuple(r.to_dict() for r in sorted(result_1.records, key=lambda r: r.player_id)) == tuple(
        r.to_dict() for r in sorted(result_2.records, key=lambda r: r.player_id)
    )


def test_conflicting_content_under_same_canonical_key_is_rejected():
    boxscore = _base_boxscore()
    result_1 = sog.ingest_settled_game_skater_sog(boxscore, source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    tampered_boxscore = _base_boxscore()
    tampered_boxscore["playerByGameStats"]["homeTeam"]["forwards"][0]["sog"] = 999
    result_2 = sog.ingest_settled_game_skater_sog(tampered_boxscore, source_uri="u", retrieved_at="2025-11-02T01:00:00Z")
    with pytest.raises(sog.NHLSogIngestionError) as exc:
        sog.merge_records(result_1.records, result_2.records)
    assert exc.value.code == "NHL_SOG_IMMUTABLE_RECORD_CONFLICT"


def test_post_start_leakage_rejection():
    boxscore = _base_boxscore()
    result = sog.ingest_settled_game_skater_sog(boxscore, source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    with pytest.raises(sog.NHLSogIngestionError) as exc:
        sog.build_pregame_snapshot(
            "8478402",
            as_of="2025-11-01T00:00:00Z",  # exactly at puck drop, not before
            target_game_start_time="2025-11-01T00:00:00Z",
            history=result.records,
        )
    assert exc.value.code == "EVENT_ALREADY_STARTED"


def test_pregame_snapshot_excludes_facts_unavailable_before_puck_drop():
    early_game = _base_boxscore(id="EARLY", startTimeUTC="2025-10-01T00:00:00Z")
    later_game = _base_boxscore(id="LATER", startTimeUTC="2025-11-01T00:00:00Z")
    future_game = _base_boxscore(id="FUTURE", startTimeUTC="2025-12-01T00:00:00Z")
    early = sog.ingest_settled_game_skater_sog(early_game, source_uri="u", retrieved_at="2025-11-02T00:00:00Z").records
    later = sog.ingest_settled_game_skater_sog(later_game, source_uri="u", retrieved_at="2025-11-02T00:00:00Z").records
    future = sog.ingest_settled_game_skater_sog(future_game, source_uri="u", retrieved_at="2025-11-02T00:00:00Z").records
    history = early + later + future

    snapshot = sog.build_pregame_snapshot(
        "8478402",
        as_of="2025-11-15T00:00:00Z",
        target_game_start_time="2025-12-01T00:00:00Z",
        history=history,
    )
    game_ids = {r.canonical_game_id for r in snapshot.prior_records}
    assert game_ids == {"EARLY", "LATER"}
    assert "FUTURE" not in game_ids


def test_malformed_boxscore_missing_top_level_fields_fails_closed():
    incomplete = {"id": "G1", "gameState": "OFF"}  # missing season/teams/startTime/stats
    with pytest.raises(sog.NHLSogIngestionError) as exc:
        sog.ingest_settled_game_skater_sog(incomplete, source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    assert exc.value.code == "NHL_SOG_BOXSCORE_SCHEMA_INVALID"


def test_malformed_skater_entry_is_unresolved_not_fabricated():
    boxscore = _base_boxscore(
        playerByGameStats={
            "homeTeam": {"forwards": [{"name": {"default": "No Player Id"}, "position": "C", "sog": 3}], "defense": []},
            "awayTeam": {"forwards": [], "defense": []},
        }
    )
    result = sog.ingest_settled_game_skater_sog(boxscore, source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    assert result.records == ()
    assert result.unresolved[0].reason_code == "NHL_SOG_PLAYER_ID_MISSING"


def test_missing_sog_value_for_dressed_skater_is_unresolved():
    boxscore = _base_boxscore(
        playerByGameStats={
            "homeTeam": {"forwards": [{"playerId": 55, "name": {"default": "No SOG"}, "position": "C"}], "defense": []},
            "awayTeam": {"forwards": [], "defense": []},
        }
    )
    result = sog.ingest_settled_game_skater_sog(boxscore, source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    assert result.records == ()
    assert result.unresolved[0].reason_code == "NHL_SOG_VALUE_MISSING"


def test_goalie_position_excluded_from_skater_only_vertical():
    boxscore = _base_boxscore(
        playerByGameStats={
            "homeTeam": {"forwards": [{"playerId": 77, "name": {"default": "Backup Goalie"}, "position": "G", "sog": 0}], "defense": []},
            "awayTeam": {"forwards": [], "defense": []},
        }
    )
    result = sog.ingest_settled_game_skater_sog(boxscore, source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    assert result.records == ()
    assert result.unresolved[0].reason_code == "NHL_SOG_POSITION_UNSUPPORTED"


def test_season_label_and_provider_season_id_are_separate_fields():
    boxscore = _base_boxscore(season="20302031")
    result = sog.ingest_settled_game_skater_sog(boxscore, source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    rec = result.records[0]
    assert rec.provider_season_id == "20302031"
    assert rec.season_label == "2030-2031"


def test_provenance_contains_source_and_timestamps():
    result = sog.ingest_settled_game_skater_sog(_base_boxscore(), source_uri="https://api-web.nhle.com/x", retrieved_at="2025-11-02T12:00:00Z")
    rec = result.records[0]
    assert rec.source == sog.SOURCE_ID
    assert rec.source_uri == "https://api-web.nhle.com/x"
    assert rec.source_retrieved_at == "2025-11-02T12:00:00Z"
    assert rec.source_payload_sha256
    assert rec.effective_at
    assert rec.game_start_time
    assert rec.research_evidence_only is True
    assert rec.can_execute is False


def test_home_away_team_stable_id_distinct_from_abbreviation():
    result = sog.ingest_settled_game_skater_sog(_base_boxscore(), source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    rec = result.records[0]
    assert rec.home_team.team_id == "10"
    assert rec.home_team.team_abbreviation == "TOR"
    assert rec.away_team.team_id == "6"
    assert rec.away_team.team_abbreviation == "BOS"


# --- Phase 2.1 hardening: availability timestamp + team-shot reconciliation ---


def test_available_at_prefers_explicit_settlement_timestamp():
    boxscore = _base_boxscore(settledAtUTC="2025-11-01T03:15:00Z")
    result = sog.ingest_settled_game_skater_sog(boxscore, source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    rec = result.records[0]
    assert rec.available_at == "2025-11-01T03:15:00+00:00"


def test_available_at_falls_back_to_retrieved_at_when_settlement_timestamp_absent():
    boxscore = _base_boxscore()
    result = sog.ingest_settled_game_skater_sog(boxscore, source_uri="u", retrieved_at="2025-11-02T05:00:00Z")
    rec = result.records[0]
    assert rec.available_at == "2025-11-02T05:00:00+00:00"


def test_malformed_settlement_timestamp_fails_closed():
    boxscore = _base_boxscore(settledAtUTC="not-a-timestamp")
    with pytest.raises(sog.NHLSogIngestionError) as exc:
        sog.ingest_settled_game_skater_sog(boxscore, source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    assert exc.value.code == "NHL_SOG_AVAILABILITY_TIMESTAMP_INVALID"


def test_official_team_sog_total_matches_summed_skater_sog_is_trusted():
    # home team dressed: 5 + 2 = 7
    boxscore = _base_boxscore(teamSogTotals={"home": 7, "away": 3})
    result = sog.ingest_settled_game_skater_sog(boxscore, source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    home_records = [r for r in result.records if r.team_id == "10"]
    assert all(r.team_shot_reconciliation_status == "MATCHED" for r in home_records)
    assert all(r.official_team_sog_total == 7 for r in home_records)


def test_official_team_sog_total_mismatch_excludes_and_flags_conflict():
    boxscore = _base_boxscore(teamSogTotals={"home": 99, "away": 3})
    result = sog.ingest_settled_game_skater_sog(boxscore, source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    home_records = [r for r in result.records if r.team_id == "10"]
    assert all(r.team_shot_reconciliation_status == "MISMATCH" for r in home_records)
    assert all(r.official_team_sog_total is None for r in home_records)


def test_incomplete_skater_coverage_without_official_total_or_roster_assertion():
    boxscore = _base_boxscore()  # no teamSogTotals, no rosterFullyResolved
    result = sog.ingest_settled_game_skater_sog(boxscore, source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    home_records = [r for r in result.records if r.team_id == "10"]
    assert all(r.team_shot_reconciliation_status == "INCOMPLETE_SKATER_COVERAGE" for r in home_records)
    assert all(r.official_team_sog_total is None for r in home_records)


def test_roster_fully_resolved_assertion_trusts_summed_total_without_official():
    boxscore = _base_boxscore(rosterFullyResolved={"home": True, "away": False})
    result = sog.ingest_settled_game_skater_sog(boxscore, source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    home_records = [r for r in result.records if r.team_id == "10"]
    away_records = [r for r in result.records if r.team_id == "6"]
    assert all(r.team_shot_reconciliation_status == "UNOFFICIAL_FULL_ROSTER_ASSERTED" for r in home_records)
    assert all(r.official_team_sog_total == 7 for r in home_records)
    assert all(r.team_shot_reconciliation_status == "INCOMPLETE_SKATER_COVERAGE" for r in away_records)


def test_unresolved_dressed_entry_prevents_roster_fully_resolved_trust():
    boxscore = _base_boxscore(
        rosterFullyResolved={"home": True, "away": True},
        playerByGameStats={
            "homeTeam": {
                "forwards": [_skater(8478402, "P", "C", 5), {"name": {"default": "No Id"}, "position": "C", "sog": 1}],
                "defense": [],
            },
            "awayTeam": {"forwards": [], "defense": []},
        },
    )
    result = sog.ingest_settled_game_skater_sog(boxscore, source_uri="u", retrieved_at="2025-11-02T00:00:00Z")
    home_records = [r for r in result.records if r.team_id == "10"]
    assert home_records
    assert all(r.team_shot_reconciliation_status == "INCOMPLETE_SKATER_COVERAGE" for r in home_records)
