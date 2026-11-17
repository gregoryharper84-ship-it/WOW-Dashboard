"""The source-sample SQL must remain read-only, bounded and non-publishable."""
import copy
from hashlib import sha256
import json
import pytest

from v17.ncaaf_sdv_espn_crosswalk import NCAA_COLUMNS, ESPN_COLUMNS, reconcile
from v17.ncaaf_cfbd_exact_sample_sql import (
    CAN_EXECUTE, ExactSampleHold, build_read_only_sql,
)


def ncaa(category):
    row = dict.fromkeys(NCAA_COLUMNS)
    row.update(season=2024, contest_id="700", espn_game_id="401677189",
               team_id="123", name="Known Player", category=category,
               pass_yards="180", completions="15", pass_attempts="23",
               yds_rush="-4", rush_attempts="12",
               rec="7", receiving_yards="88")
    return row


def espn(category):
    row = dict.fromkeys(ESPN_COLUMNS)
    row.update(season=2024, game_id=401677189, team_id=87,
               athlete_id=4683423, athlete_name="Known Player", category=category,
               passingYards="180", **{"completions/passingAttempts": "15/23"},
               rushingYards="-4", rushingAttempts="12",
               receptions="7", receivingYards="88")
    return row


def report():
    return reconcile(
        [ncaa("passing"), ncaa("rushing"), ncaa("receiving")],
        [espn("passing"), espn("rushing"), espn("receiving")],
        season=2024, ncaa_sha256="a"*64, espn_sha256="b"*64,
    )


def resign(receipt):
    s = json.dumps(receipt["cfbd_exact_match_sample"],
                   sort_keys=True, separators=(",", ":")).encode()
    receipt["cfbd_exact_match_sample_sha256"] = sha256(s).hexdigest()
    receipt["cfbd_exact_match_sample_size"] = len(receipt["cfbd_exact_match_sample"])


def test_seven_exact_sample_stat_families_and_non_authority():
    r = report()
    samples = r["cfbd_exact_match_sample"]
    assert len(samples) == 7
    assert len({(x["event_id"], x["athlete_id"], x["stat_type"]) for x in samples}) == 7
    assert {x["stat_type"] for x in samples} == {
        "PASSING_YARDS", "COMPLETIONS", "PASS_ATTEMPTS", "RUSHING_YARDS",
        "RUSH_ATTEMPTS", "RECEPTIONS", "RECEIVING_YARDS"}
    assert r["cfbd_exact_match_sample_verified"] is False
    assert r["model_training_authorized_by_crosswalk"] is False
    assert r["can_execute"] is False
    assert CAN_EXECUTE is False
    assert all(x["athlete_id"] == "4683423" for x in samples)
    assert all(x["espn_team_id"] == "87" for x in samples)
    assert all("name" not in x for x in samples)


def test_sql_is_read_only_private_schema_strict_match():
    q = build_read_only_sql(report())
    assert "LEFT JOIN wow_prop_history.ncaaf_player_game_outcomes" in q
    for f in ("o.official_event_id = s.event_id",
              "o.official_player_id = s.athlete_id",
              "o.stat_type = s.stat_type", "cfbd_value<>candidate_value"):
        assert f in q
    for illegal in ("INSERT INTO", "UPDATE ", "DELETE FROM", "CREATE ", "DROP ", "TRUNCATE "):
        assert illegal not in q.upper()
    assert "MODEL_TRAINING_ELIGIBLE" in q.upper()
    assert "CFBD_STAT_CONTRADICTION" in q
    assert "CFBD_DUPLICATE_SOURCE_KEY" in q
    assert "CFBD_EXACT_KEY_NOT_FOUND" in q
    assert "MATCH_TEAM_UNVERIFIED" in q
    assert "(event_id,athlete_id)" in q


def test_repeated_sample_yields_same_hash_sql():
    a, b = report(), report()
    assert a["cfbd_exact_match_sample_sha256"] == b["cfbd_exact_match_sample_sha256"]
    assert build_read_only_sql(a) == build_read_only_sql(b)


@pytest.mark.parametrize("kind,value,code", [
    ("event_id", "401'; drop table", "EVENT_ID_INVALID"),
    ("athlete_id", "not-numeric", "ATHLETE_ID_INVALID"),
    ("stat_type", "POINTS", "STAT_INVALID"),
    ("actual_value", 3.14, "STAT_INVALID"),
])
def test_non_numeric_key_or_invalid_stat_fails_closed(kind, value, code):
    r = report()
    r["cfbd_exact_match_sample"][0][kind] = value
    resign(r)
    with pytest.raises(ExactSampleHold, match=code):
        build_read_only_sql(r)


def test_tampered_sample_hash_rejected():
    r = report()
    r["cfbd_exact_match_sample"][0]["actual_value"] = 34
    with pytest.raises(ExactSampleHold, match="NCAAF_CFBD_SAMPLE_HASH_MISMATCH"):
        build_read_only_sql(r)


def test_duplicate_exact_key_fails_closed():
    r = report()
    r["cfbd_exact_match_sample"].append(copy.deepcopy(r["cfbd_exact_match_sample"][0]))
    resign(r)
    with pytest.raises(ExactSampleHold, match="NCAAF_CFBD_SAMPLE_DUPLICATE_KEY"):
        build_read_only_sql(r)


def test_untrusted_report_cannot_self_authorize_model():
    r = report()
    r["model_training_authorized_by_crosswalk"] = True
    with pytest.raises(ExactSampleHold, match="NCAAF_CFBD_RECEIPT_SCOPE_INVALID"):
        build_read_only_sql(r)


def test_allows_negative_rushing_yards_but_not_negative_counts():
    r = report()
    assert "RUSHING_YARDS" in build_read_only_sql(r)
    count = next(x for x in r["cfbd_exact_match_sample"] if x["stat_type"]=="RECEPTIONS")
    count["actual_value"] = -3
    resign(r)
    with pytest.raises(ExactSampleHold, match="NCAAF_CFBD_COUNT_NEGATIVE"):
        build_read_only_sql(r)


def test_wrong_season_rejected():
    with pytest.raises(ExactSampleHold, match="NCAAF_CFBD_RECEIPT_SCOPE_INVALID"):
        build_read_only_sql(report(), season=2025)


def test_sample_is_capped_and_cannot_expand_bulk_export():
    r = report()
    r["cfbd_exact_match_sample"] = [
        {**r["cfbd_exact_match_sample"][0], "athlete_id": str(i+1)}
        for i in range(29)]
    resign(r)
    with pytest.raises(ExactSampleHold, match="NCAAF_CFBD_SAMPLE_SIZE_INVALID"):
        build_read_only_sql(r)
