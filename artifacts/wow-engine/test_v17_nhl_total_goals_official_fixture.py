"""Genuine full-season NHL public-source fixtures: verify, never publish."""
import base64
import gzip
import json
from pathlib import Path

import pytest

from v17.nhl_total_goals_official_fixture import (
    CAN_EXECUTE, PROBABILITY_PUBLISHABLE, NHLProjectedSourceError,
    load_checked_in_season, parse_projected_official_season,
    build_real_reconstructed_regime_rows,
)
from v17.nhl_total_goals_regime_mixture_research import (
    NHLRegimeMixtureResearchError, fit_regime_mixture_research,
)

FIXTURES=Path(__file__).parent / "tests" / "fixtures"


def test_two_genuine_full_seasons_have_strict_first_party_projection_provenance():
    cohorts=[]
    for year in (2021,2022):
        source,audit=load_checked_in_season(year,fixture_directory=FIXTURES)
        assert audit["status"]=="NHL_TOTALS_OFFICIAL_HISTORICAL_PROJECTION_VERIFIED"
        assert audit["games"]==1312
        assert audit["team_mirror_instances"]==2624
        assert audit["games_per_team"]==82
        assert sum(audit["ending_regime_counts"].values())==1312
        assert audit["source_projection_not_d1_raw_sha"] is True
        assert audit["immutable_pregame_snapshots"] is False
        assert audit["probability_publishable"] is False
        assert audit["can_execute"] is False
        assert all(len(x.source_projection_sha256)==64 for x in source)
        cohorts.append((year,source))
    rows,receipt=build_real_reconstructed_regime_rows(cohorts)
    assert receipt["total_official_source_games"]==2624
    assert 2200 < receipt["eligible_prior_game_features"] < 2600
    assert len(rows)==receipt["eligible_prior_game_features"]
    assert all(r.feature_as_of<r.start_utc for r in rows)
    assert {r.terminal_regime for r in rows} == {"REG","OT","SO"}
    assert receipt["source_projection_not_d1_raw_sha"] is True
    assert receipt["governed_model_fitted"] is False
    assert receipt["calibrated_lower_bound"] is None
    assert all(not r.archived_pregame_snapshot for r in rows)
    assert not CAN_EXECUTE and not PROBABILITY_PUBLISHABLE


def test_training_data_is_authentic_but_missing_2023_calibration_is_not_a_model():
    first,_=load_checked_in_season(2021,fixture_directory=FIXTURES)
    second,_=load_checked_in_season(2022,fixture_directory=FIXTURES)
    rows,_=build_real_reconstructed_regime_rows([(2021,first),(2022,second)])
    with pytest.raises(NHLRegimeMixtureResearchError) as e:
        fit_regime_mixture_research(rows)
    assert e.value.code=="NHL_REGIME_HOLDOUT_INSUFFICIENT"


def test_missing_local_fixture_produces_typed_acquisition_hold(tmp_path):
    with pytest.raises(NHLProjectedSourceError) as e:
        load_checked_in_season(2023,fixture_directory=tmp_path)
    assert e.value.code=="NHL_TOTALS_OFFICIAL_FIXTURE_MISSING"


@pytest.mark.parametrize("mutate,code", [
    (lambda p: p["records"].pop(), "NHL_TOTALS_OFFICIAL_COVERAGE_INCOMPLETE"),
    (lambda p: p["records"][0].__setitem__("sourceMirrorTeams", ["NYR","NYR"]),
     "NHL_TOTALS_OFFICIAL_GAME_PROJECTION_INVALID"),
    (lambda p: p["records"][0]["gameOutcome"].__setitem__("lastPeriodType","BAD"),
     "NHL_TOTALS_OFFICIAL_GAME_PROJECTION_INVALID"),
    (lambda p: p["records"][0].__setitem__("gameState","FUT"),
     "NHL_TOTALS_OFFICIAL_GAME_PROJECTION_INVALID"),
    (lambda p: p.__setitem__("archived_pregame_snapshot",True),
     "NHL_TOTALS_OFFICIAL_PROVENANCE_INVALID"),
    (lambda p: p.__setitem__("source_game_projection_not_raw_payload",False),
     "NHL_TOTALS_OFFICIAL_PROVENANCE_INVALID"),
])
def test_first_party_source_corruption_is_held_without_model_fit(mutate,code):
    path=FIXTURES / "nhl_official_regimes_20212022.json.gz.b64"
    original=json.loads(gzip.decompress(base64.b64decode(path.read_text())))
    mutate(original)
    encoded=base64.b64encode(gzip.compress(json.dumps(original).encode())).decode()
    with pytest.raises(NHLProjectedSourceError) as error:
        parse_projected_official_season(encoded,year=2021)
    assert error.value.code==code


def test_invalid_archive_is_not_silently_empty_corpus():
    with pytest.raises(NHLProjectedSourceError) as error:
        parse_projected_official_season("!!!",year=2021)
    assert error.value.code=="NHL_TOTALS_OFFICIAL_ARCHIVE_INVALID"



def test_valid_looking_result_timestamp_change_breaks_independent_d1_checksum():
    path = FIXTURES / "nhl_official_regimes_20212022.json.gz.b64"
    body = json.loads(gzip.decompress(base64.b64decode(path.read_text())))
    old = body["records"][0]["startTimeUTC"]
    body["records"][0]["startTimeUTC"] = (
        old.replace("Z", ".001Z")
    )
    encoded=base64.b64encode(gzip.compress(json.dumps(body).encode())).decode()
    with pytest.raises(NHLProjectedSourceError) as e:
        parse_projected_official_season(encoded,year=2021)
    assert e.value.code == "NHL_TOTALS_OFFICIAL_SOURCE_FINGERPRINT_MISMATCH"


def test_valid_looking_regime_flip_breaks_first_party_outcome_checksum():
    path = FIXTURES / "nhl_official_regimes_20212022.json.gz.b64"
    body = json.loads(gzip.decompress(base64.b64decode(path.read_text())))
    candidate = next(
        r for r in body["records"]
        if r["gameOutcome"]["lastPeriodType"]=="REG"
        and abs(r["homeTeam"]["score"]-r["awayTeam"]["score"])==1
    )
    assert (candidate["homeTeam"]["score"]+candidate["awayTeam"]["score"])%2==1
    candidate["gameOutcome"]["lastPeriodType"] = "SO"
    encoded=base64.b64encode(gzip.compress(json.dumps(body).encode())).decode()
    with pytest.raises(NHLProjectedSourceError) as e:
        parse_projected_official_season(encoded,year=2021)
    assert e.value.code == "NHL_TOTALS_OFFICIAL_SOURCE_FINGERPRINT_MISMATCH"
