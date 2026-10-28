"""Synthetic-only exact NBA official settlement tests; no live writes or wagers."""
from copy import deepcopy
from datetime import datetime, timezone

import v17.prop_exact_route_settlement as base
import v17.prop_exact_route_settlement_scope_guard as scope
from v17.nba_prop_official_settlement import NBA_SUPPORTED, settle_nba_scalar

NOW = datetime(2026, 10, 28, 6, 0, tzinfo=timezone.utc)
START = "2026-10-28T02:00:00+00:00"
PLAYER_ID = "123456"
GAME_ID = "0022600001"
ESPN_ID = "espn-401234567"


class Response:
    status_code = 200
    def __init__(self, payload, code=200):
        self._payload = payload
        self.status_code = code
    def json(self):
        return self._payload


def prediction(*, stat_type="POINTS", line=26.5, direction="MORE"):
    return {
        "prediction_id": "synthetic-nba-pred-1",
        "source_snapshot_id": "nba-snap-1",
        "event_id": ESPN_ID, "event_start_time": START,
        "model_timestamp": "2026-10-28T01:00:00+00:00",
        "locked_at": "2026-10-28T01:30:00+00:00",
        "player": "Jane Example", "sport": "NBA", "stat_type": stat_type,
        "line": line, "direction": direction,
        "primary_failure_path": "SYNTHETIC_TEST",
        "evidence_source_kind": "IMMUTABLE_PREGAME_SETTLED",
    }


def snapshot(*, stat_type="POINTS", line=26.5):
    return {
        "source_snapshot_id": "nba-snap-1", "captured_at": "2026-10-28T00:50:00+00:00",
        "event_id": ESPN_ID, "event_start_time": START,
        "player": "Jane Example", "sport": "NBA",
        "stat_type": stat_type, "line": line,
        "opportunity_ledger": {"period": "FULL_GAME", "literal_threshold": str(line)},
        "role_status": {
            "official_game_id": GAME_ID, "player_id": PLAYER_ID,
            "source_identity": {
                "nba_game_id": GAME_ID, "nba_person_id": PLAYER_ID,
                "espn_event_id": ESPN_ID, "team_tricode": "BOS",
                "event_start_time": START,
                "espn_captured_at": "2026-10-28T00:30:00+00:00",
                "nba_captured_at": "2026-10-28T00:45:00+00:00",
                "source_names": ["ESPN_GAME_SUMMARY", "NBA_CDN_LIVE_BOXSCORE"],
            },
        },
    }


def boxscore(*, final=True, played=True, reason=None):
    return {"game": {
        "gameId": GAME_ID, "gameStatus": 3 if final else 2,
        "gameTimeUTC": START,
        "homeTeam": {"teamTricode": "BOS", "players": [{
            "personId": int(PLAYER_ID),
            "name": "Jane Example",
            "played": played,
            "notPlayingReason": reason,
            "statistics": {"points": 27, "reboundsTotal": 9, "assists": 7},
        }]},
        "awayTeam": {"teamTricode": "NYK", "players": []},
    }}


def run(p=None, s=None, feed=None):
    p = p or prediction()
    s = s or snapshot(stat_type=p["stat_type"], line=p["line"])
    feed = feed if feed is not None else boxscore()
    return settle_nba_scalar(p, s, now=NOW, http_get=lambda url, **kwargs: Response(feed))


def test_inventory_registers_exact_supported_routes_not_fantasy_or_wager():
    for stat in ("POINTS", "REBOUNDS", "ASSISTS", "PRA", "POINTS_REBOUNDS",
                 "POINTS_ASSISTS", "REBOUNDS_ASSISTS"):
        assert ("NBA", stat) in NBA_SUPPORTED
        rows = [r for r in base.build_settlement_inventory()
                if r["sport"] == "NBA" and r["stat_type"] == stat]
        assert len(rows) == 1
        assert rows[0]["status"] == base.SETTLEMENT_READY
        assert rows[0]["can_execute"] is False
    assert ("NBA", "FANTASY_SCORE") not in NBA_SUPPORTED


def test_components_one_official_player_event_and_all_directions():
    cases = {
        "POINTS": 27., "REBOUNDS": 9., "ASSISTS": 7.,
        "PRA": 43., "POINTS_REBOUNDS": 36.,
        "POINTS_ASSISTS": 34., "REBOUNDS_ASSISTS": 16.,
    }
    for stat, total in cases.items():
        p = prediction(stat_type=stat, line=total-0.5)
        out = run(p)
        assert out["status"] == "SETTLED"
        assert out["outcome"]["actual_stat"] == total
        assert out["outcome"]["official_result"] == "HIT"
        assert out["settlement_source_kind"] == "NBA_OFFICIAL_CDN_FINAL_BOXSCORE"
        assert out["official_player_id"] == PLAYER_ID
        assert out["can_execute"] is False
        p["line"] = total + 0.5
        p["direction"] = "LESS"
        assert run(p)["outcome"]["official_result"] == "HIT"
        p["line"] = total - 0.5
        assert run(p)["outcome"]["official_result"] == "MISS"
        p["line"] = total
        p["direction"] = "MORE"
        assert run(p)["outcome"]["official_result"] == "PUSH"


def test_nonfinal_and_cross_event_mismatch_fail_closed():
    assert run(feed=boxscore(final=False))["status"] == "NOT_FINAL"
    b = boxscore(); b["game"]["gameId"] = "0022609999"
    assert run(feed=b)["status"] == "OFFICIAL_EVENT_ID_MISMATCH"
    b = boxscore(); b["game"]["gameTimeUTC"] = "2026-10-28T03:00:00+00:00"
    assert run(feed=b)["blocker"] == "NBA_OFFICIAL_EVENT_START_CONTRADICTION"


def test_player_id_name_team_and_duplicate_require_exact_match():
    b = boxscore(); b["game"]["homeTeam"]["players"] = []
    assert run(feed=b)["status"] == "OFFICIAL_PLAYER_STAT_ROW_MISSING"
    b = boxscore(); b["game"]["homeTeam"]["players"][0]["name"] = "Other Player"
    assert run(feed=b)["blocker"] == "NBA_OFFICIAL_PLAYER_NAME_MISMATCH"
    b = boxscore(); b["game"]["awayTeam"]["players"].append(deepcopy(b["game"]["homeTeam"]["players"][0]))
    assert run(feed=b)["status"] == "OFFICIAL_PLAYER_EVENT_IDENTITY_AMBIGUOUS"
    b = boxscore(); b["game"]["homeTeam"]["teamTricode"] = "LAL"
    assert run(feed=b)["blocker"] == "NBA_OFFICIAL_TEAM_CROSSWALK_MISMATCH"


def test_explicit_dnp_can_void_missing_player_cannot():
    out = run(feed=boxscore(played=False, reason="INACTIVE"))
    assert out["status"] == "SETTLED_VOID_DNP"
    assert out["outcome"]["void"] is True
    assert out["outcome"]["actual_stat"] is None
    out = run(feed=boxscore(played=False, reason=None))
    assert out["status"] == "OFFICIAL_STAT_MISSING"
    b = boxscore(); b["game"]["homeTeam"]["players"][0].pop("played")
    assert run(feed=b)["blocker"] == "NBA_EXPLICIT_PLAYED_STATUS_REQUIRED"


def test_missing_or_bad_stat_never_zero_fills_or_settles():
    b = boxscore(); b["game"]["homeTeam"]["players"][0]["statistics"].pop("reboundsTotal")
    assert run(prediction(stat_type="PRA"), feed=b)["status"] == "OFFICIAL_STAT_MISSING"
    b = boxscore(); b["game"]["homeTeam"]["players"][0]["statistics"]["points"] = True
    assert run(feed=b)["status"] == "OFFICIAL_STAT_MISSING"
    b = boxscore(); b["game"]["homeTeam"]["players"][0]["statistics"]["assists"] = -1
    assert run(prediction(stat_type="ASSISTS"), feed=b)["status"] == "OFFICIAL_STAT_MISSING"


def test_offer_period_and_literal_at_least_are_unambiguous():
    s = snapshot(); s["opportunity_ledger"]["period"] = "FIRST_HALF"
    assert run(s=s)["blocker"] == "NBA_EXACT_FULL_GAME_PERIOD_REQUIRED"
    s = snapshot(); s["opportunity_ledger"]["literal_threshold"] = "7+"
    assert run(s=s)["blocker"] == "NBA_AT_LEAST_OFFER_ADAPTER_NOT_CERTIFIED"


def test_exact_pregame_prediction_and_source_temporal_proof():
    p = prediction(); p["model_timestamp"] = START
    assert run(p=p)["blocker"] == "NBA_FORWARD_PREGAME_TEMPORAL_PROOF_REQUIRED"
    p = prediction(); p["locked_at"] = START
    assert run(p=p)["blocker"] == "NBA_FORWARD_PREGAME_TEMPORAL_PROOF_REQUIRED"
    s = snapshot(); s["captured_at"] = START
    assert run(s=s)["blocker"] == "NBA_FORWARD_PREGAME_TEMPORAL_PROOF_REQUIRED"
    s = snapshot(); s["role_status"]["source_identity"]["nba_captured_at"] = START
    assert run(s=s)["blocker"] == "NBA_OFFICIAL_PREGAME_SOURCE_TIMESTAMPS_INVALID"


def test_reject_missing_or_contradictory_cross_provider_proof():
    s = snapshot(); s["role_status"].pop("source_identity")
    assert run(s=s)["blocker"] == "NBA_VERIFIED_PREGAME_CROSS_PROVIDER_IDENTITY_REQUIRED"
    s = snapshot(); s["role_status"]["source_identity"]["nba_person_id"] = "777"
    assert run(s=s)["blocker"] == "NBA_OFFICIAL_EVENT_PLAYER_CROSSWALK_CONTRADICTION"
    s = snapshot(); s["role_status"]["source_identity"]["espn_event_id"] = "espn-99999999"
    assert run(s=s)["blocker"] == "NBA_OFFICIAL_EVENT_PLAYER_CROSSWALK_CONTRADICTION"
    s = snapshot(); s["role_status"]["source_identity"]["source_names"] = ["ESPN_GAME_SUMMARY"]
    assert run(s=s)["blocker"] == "NBA_OFFICIAL_EVENT_PLAYER_CROSSWALK_INCOMPLETE"


def test_exact_snapshot_line_stat_and_event_disagree_holds():
    s = snapshot(); s["line"] = 20.5
    assert run(s=s)["blocker"] == "NBA_EXACT_LINE_OR_DIRECTION_INVALID"
    s = snapshot(); s["event_id"] = "espn-11111111"
    assert run(s=s)["blocker"] == "NBA_EXACT_SNAPSHOT_IDENTITY_MISMATCH"
    s = snapshot(); s["stat_type"] = "ASSISTS"
    assert run(s=s)["blocker"] == "NBA_EXACT_SNAPSHOT_IDENTITY_MISMATCH"


def test_bad_source_payload_or_http_does_not_settle():
    assert run(feed={"unexpected": True})["status"] == "OFFICIAL_STAT_SOURCE_INVALID"
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("provider offline")
    out = settle_nba_scalar(prediction(), snapshot(), now=NOW, http_get=unavailable)
    assert out["status"] == "OFFICIAL_SOURCE_UNAVAILABLE"
    assert "outcome" not in out


def test_immutable_forward_selector_rejects_non_forward_and_poststart_rows(monkeypatch):
    p = prediction()
    p.update(model_provider_identity=base.PROVIDER, source_snapshot_id="nba-snap-1")
    ordinary = dict(p, prediction_id="ordinary", evidence_source_kind="NOT_A_FORWARD")
    late = dict(p, prediction_id="late", model_timestamp=START)
    monkeypatch.setattr(base, "_paginate", lambda *_args, **_kwargs: [p, ordinary, late])
    result = scope._eligible_forward_predictions(
        db=object(), requested_routes={("NBA", "POINTS")}, now=NOW
    )
    assert [row["prediction_id"] for row in result] == [p["prediction_id"]]


def test_runner_persists_only_successful_immutable_forward_outcome(monkeypatch):
    p = prediction()
    monkeypatch.setattr(scope, "_eligible_forward_predictions", lambda *args, **kwargs: [p])
    monkeypatch.setattr(base, "_existing_outcomes", lambda *args: set())
    monkeypatch.setattr(base, "_snapshots", lambda *args: {"nba-snap-1": snapshot()})
    saved = []
    monkeypatch.setattr(base, "_persist_outcome", lambda db, outcome: saved.append(outcome))
    response = scope.run_exact_route_settlement(
        base.ExactRouteSettlementRequest(routes=["NBA:POINTS"], limit=4),
        db=object(), now=NOW, http_get=lambda *_a, **_kw: Response(boxscore())
    )
    assert response["settlement_scope"] == "IMMUTABLE_FORWARD_EVIDENCE_ONLY"
    assert response["settled_or_voided"] == 1
    assert len(saved) == 1 and saved[0]["actual_stat"] == 27.0
    assert response["calibration_performed"] is False
    assert response["can_execute"] is False


def test_no_independent_proof_means_no_persistence(monkeypatch):
    monkeypatch.setattr(scope, "_eligible_forward_predictions", lambda *args, **kwargs: [prediction()])
    monkeypatch.setattr(base, "_existing_outcomes", lambda *args: set())
    s = snapshot(); s["role_status"].pop("source_identity")
    monkeypatch.setattr(base, "_snapshots", lambda *args: {"nba-snap-1": s})
    saved = []
    monkeypatch.setattr(base, "_persist_outcome", lambda db, outcome: saved.append(outcome))
    response = scope.run_exact_route_settlement(
        base.ExactRouteSettlementRequest(routes=["NBA:POINTS"], limit=4),
        db=object(), now=NOW, http_get=lambda *_a, **_kw: Response(boxscore())
    )
    assert response["held"] == 1
    assert saved == []
    assert response["can_execute"] is False


def test_literal_offer_required_and_numeric_offer_must_match_prediction():
    s = snapshot()
    s["opportunity_ledger"].pop("literal_threshold")
    assert run(s=s)["blocker"] == "NBA_LITERAL_EXACT_OFFER_REQUIRED"
    s = snapshot()
    s["opportunity_ledger"]["literal_threshold"] = "27.5"
    assert run(s=s)["blocker"] == "NBA_LITERAL_EXACT_OFFER_LINE_MISMATCH"
    s = snapshot()
    s["opportunity_ledger"]["threshold_type"] = "AT_LEAST"
    assert run(s=s)["blocker"] == "NBA_AT_LEAST_OFFER_ADAPTER_NOT_CERTIFIED"
    s = snapshot()
    s["opportunity_ledger"]["literal_threshold"] = "NaN"
    assert run(s=s)["blocker"] == "NBA_LITERAL_EXACT_OFFER_FORMAT_UNSUPPORTED"
