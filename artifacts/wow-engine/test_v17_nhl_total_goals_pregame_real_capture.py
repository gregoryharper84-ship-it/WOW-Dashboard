"""Frozen genuine NHL Oct 26 2026 first-party pregame source research canary.

Source was read on Oct 26 at 22:28:19Z, *before* all three Oct 26
night games. This is an actual first-party historical snapshot, not a
fabricated retrospective FUT state. It still has NO certified goalie
starting evidence, no licensed snapshot attestation or model probability.
"""
import base64
from datetime import datetime
import gzip
from hashlib import sha256
import json
from pathlib import Path

from v17.nhl_total_goals_pregame_hydration import hydrate_nhl_total_goals_pregame


def snapshot():
    file=Path(__file__).parent / "tests" / "fixtures" / "nhl_20262027_pregame_capture_20261026.json.gz.b64"
    body=json.loads(gzip.decompress(base64.b64decode(file.read_text())))
    return body


def projected_digest(game):
    canonical={k:v for k,v in game.items() if k!="mirrors"}
    return sha256(json.dumps(canonical,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def to_source(game, observed_by_team):
    final_observed = max(observed_by_team[team] for team in game["mirrors"])
    return {
        "source_game_state":game["gameState"],
        "source_final_observed_at_utc":final_observed,
        "sport":"NHL","source_provider":"NHL_PUBLIC_WEB_API",
        "historical_reconstruction":True,"can_execute":False,
        "official_event_id":str(game["id"]),
        "season":"20262027","event_start_time":game["startTimeUTC"],
        "home_team":game["homeTeam"]["abbrev"],
        "away_team":game["awayTeam"]["abbrev"],
        "home_score":game["homeTeam"]["score"],
        "away_score":game["awayTeam"]["score"],
        "source_payload_sha256":projected_digest(game),
    }


def test_actual_oct26_pregame_league_snapshot_has_complete_mirror_census():
    body=snapshot()
    assert body["source"]=="NHL_PUBLIC_WEB_API"
    assert body["season"]==20262027
    assert body["source_requests"]==32
    assert body["pre_start_only"] is True
    assert body["retrospective_source_now"] is False
    assert len(body["firstparty_observed_receipts"])==32
    as_of=datetime.fromisoformat(body["retrieved_as_of_utc"].replace("Z","+00:00"))
    assert as_of.isoformat().startswith("2026-10-26T22:28:19")
    assert len(body["completed_games"])==184
    assert sum(len(g["mirrors"]) for g in body["completed_games"])==368
    for g in body["completed_games"]:
        assert g["gameState"] in ("FINAL","OFF")
        assert set(g["mirrors"])=={g["homeTeam"]["abbrev"],g["awayTeam"]["abbrev"]}
        assert g["homeTeam"]["score"]!=g["awayTeam"]["score"]
        assert datetime.fromisoformat(g["startTimeUTC"].replace("Z","+00:00"))<as_of
    for receipt in body["firstparty_observed_receipts"]:
        assert datetime.fromisoformat(receipt["observed_at_utc"].replace("Z","+00:00")) <= as_of


def test_three_genuine_future_games_have_source_pinned_prior_only_features():
    body=snapshot()
    as_of=body["retrieved_as_of_utc"]
    events={g["id"]:g for g in body["future_targets"]}
    assert {2026020185,2026020186,2026020187}.issubset(events)
    observed_by_team={
        r["team"]:r["observed_at_utc"]
        for r in body["firstparty_observed_receipts"]
    }
    past=[to_source(g, observed_by_team) for g in body["completed_games"]]
    for id_ in (2026020185,2026020186,2026020187):
        target=dict(events[id_])
        assert target["gameState"]=="FUT"
        assert target["startTimeUTC"]>as_of
        target.update({
            "source_id":"NHL_PUBLIC_WEB_API",
            "source_snapshot_id":f"NHL_PUBLIC_OFFICIAL_CAPTURE_20261026_{id_}",
            "source_payload_sha256":projected_digest(events[id_]),
            "source_captured_at_utc":as_of,
        })
        outcome=hydrate_nhl_total_goals_pregame(
            past,target,evidence_as_of_utc=as_of
        )
        assert outcome["status"]=="NHL_TOTALS_PREGAME_RESEARCH_FEATURES_READY_NOT_CERTIFIED"
        assert outcome["evidence"]["target_official_event_id"]==str(id_)
        assert outcome["evidence"]["completed_source_games_supplied"]==184
        assert outcome["evidence"]["home_prior_games"]>=5
        assert outcome["evidence"]["away_prior_games"]>=5
        assert len(outcome["features"])==6
        assert outcome["evidence"]["prior_final_result_observed_by_asof"] is True
        assert outcome["starting_goalies_confirmed"] is False
        assert outcome["source_signature_independently_verified"] is False
        assert outcome["completed_league_schedule_coverage_verified"] is False
        assert outcome["model_registered_for_production"] is False
        assert outcome["certified_probability_package"] is None
        assert outcome["calibrated_lower_bound"] is None
        assert outcome["probability_publishable"] is False
        assert outcome["can_execute"] is False
