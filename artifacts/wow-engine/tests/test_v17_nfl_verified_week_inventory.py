"""Negative source/provenance contract tests for the NFL full-week inventory adapter."""
from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from v17.cross_sport_acceptance_factory import Lane, choose_current_pregame_cohort
from v17.nfl_multiday_acceptance_preflight import plan_nfl_week_dates
from v17.nfl_verified_week_inventory import (
    NflWeekInventoryHold, SCHEDULE_URL, VerifiedKickoff, VerifiedScheduleCapture,
    VerifiedWeek, build_verified_nfl_week_inventory, execute_verified_nfl_week_preflight,
)
from v17.nfl_multiday_snapshot_adapter import AUDIENCE, AuthenticatedSnapshotResponse

NOW = datetime(2026, 10, 16, 13, 0, tzinfo=timezone.utc)
TEAM_CODES = (
    "DAL", "NYG", "NYJ", "PHI", "WAS", "GB", "CHI", "DET", "MIN",
    "ATL", "CAR", "NO", "TB", "BUF", "MIA", "NE", "BAL", "CIN",
    "CLE", "PIT", "JAX", "HOU", "TEN", "IND", "KC", "LV",
)
START = datetime(2026, 10, 18, 18, 0, tzinfo=timezone.utc)
WINDOW = VerifiedWeek(
    season=2026, game_type="REG", week=7,
    start_utc=datetime(2026, 10, 17, tzinfo=timezone.utc),
    end_utc=datetime(2026, 10, 22, tzinfo=timezone.utc),
)
FIELDS = (
    "game_id", "season", "game_type", "week", "gameday",
    "home_team", "away_team", "home_score", "away_score",
)


def _csv_bytes(rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _inputs(*, count=13):
    rows = []
    starts = {}
    attested = {}
    for i in range(count):
        start = START + timedelta(hours=2 * i)
        home, away = TEAM_CODES[2 * i], TEAM_CODES[2 * i + 1]
        game_id = f"2026_07_{away}_{home}"
        rows.append({
            "game_id": game_id, "season": "2026", "game_type": "REG",
            "week": "7", "gameday": start.astimezone(
                ZoneInfo("America/New_York")
            ).date().isoformat(),
            "home_team": home, "away_team": away,
            "home_score": "", "away_score": "",
        })
        attested[game_id] = VerifiedKickoff(
            game_id=game_id, season=2026, week=7, game_type="REG",
            start_utc=start, status="SCHEDULED", home_team=home, away_team=away,
        )
        starts[game_id] = start
    raw = _csv_bytes(rows)
    capture = VerifiedScheduleCapture(
        source_id="nflverse:captured-source-test",
        source_url=SCHEDULE_URL,
        content_sha256=hashlib.sha256(raw).hexdigest(),
        fetched_at=NOW - timedelta(minutes=20),
        authenticated_https=True,
    )
    return raw, rows, capture, attested


def _build(raw=None, *, capture=None, attestations=None, weeks=(WINDOW,)):
    r, _, c, a = _inputs()
    return build_verified_nfl_week_inventory(
        r if raw is None else raw,
        capture=c if capture is None else capture,
        kickoff_evidence=a if attestations is None else attestations,
        declared_weeks=weeks,
        trusted_now=NOW,
    )


def _held(code, **overrides):
    with pytest.raises(NflWeekInventoryHold) as exc:
        _build(**overrides)
    assert exc.value.code == code


def test_real_week_preflight_wiring_uses_thirteen_canonical_ids_without_scoring():
    inv, weeks = _build()
    assert len(inv.fixtures) == 13 and len(weeks) == 1
    assert weeks[0].week_key == "REG:07"
    lane = Lane("NFL", "MONEYLINE", "scoreWowTeamEventRequest", "NFL_GAME_WIN_V17")
    selection = choose_current_pregame_cohort(
        lane, inv, weeks, now=NOW, minimum_unique_events=13,
    )
    assert selection["status"] == "COHORT_ELIGIBLE_FOR_TEST"
    plan = plan_nfl_week_dates(
        selection, inv,
        independently_authenticated_source_sha256=inv.payload_sha256,
        trusted_now=NOW,
    )
    assert plan["status"] == "DATE_PLAN_READY_NOT_ACTION_ACCEPTED"
    assert len(plan["expected_event_ids"]) == 13
    assert len(plan["dates"]) >= 2
    assert plan["can_execute"] is False and plan["probability_publishable"] is False


def test_source_digest_and_verified_origin_required():
    raw, _, capture, _ = _inputs()
    _held("NFL_WEEK_SOURCE_DIGEST_UNVERIFIED", capture=replace(
        capture, content_sha256="0" * 64
    ))
    _held("NFL_WEEK_AUTHENTICATED_CAPTURE_REQUIRED", capture=replace(
        capture, authenticated_https=False
    ))
    _held("NFL_WEEK_AUTHENTICATED_CAPTURE_REQUIRED", capture=replace(
        capture, source_url="https://example.com/unchecked.csv"
    ))
    _held("NFL_WEEK_SOURCE_DIGEST_UNVERIFIED", raw=raw + b" ")


def test_stale_or_future_capture_fails_before_scoring():
    _, _, capture, _ = _inputs()
    for fetched_at in (NOW - timedelta(hours=7), NOW + timedelta(seconds=1)):
        _held("NFL_WEEK_SOURCE_STALE_OR_FUTURE", capture=replace(
            capture, fetched_at=fetched_at
        ))


def test_missing_or_extra_official_attestation_blocks_complete_week():
    _, _, _, attest = _inputs()
    _held("NFL_WEEK_KICKOFF_INVENTORY_INCOMPLETE_OR_EXTRA",
          attestations={k: v for k, v in attest.items() if k != next(iter(attest))})
    _held("NFL_WEEK_KICKOFF_INVENTORY_INCOMPLETE_OR_EXTRA",
          attestations={**attest, "invented-id": next(iter(attest.values()))})


def test_canonical_schedule_identity_conflict_and_wrong_kickoff_block():
    _, _, _, att = _inputs()
    first = next(iter(att))
    _held("NFL_WEEK_KICKOFF_CANONICAL_IDENTITY_CONFLICT",
          attestations={**att, first: replace(att[first], home_team="MIA")})
    _held("NFL_WEEK_KICKOFF_OUTSIDE_DECLARED_WEEK",
          attestations={**att, first: replace(att[first],
              start_utc=WINDOW.end_utc + timedelta(hours=2))})


def test_duplicate_team_with_distinct_game_ids_never_qualifies_as_full_week():
    raw, rows, capture, att = _inputs()
    second_id = rows[1]["game_id"]
    duplicated_home_team = rows[0]["home_team"]
    new_id = f'2026_07_{rows[1]["away_team"]}_{duplicated_home_team}'
    rows[1]["home_team"] = duplicated_home_team
    rows[1]["game_id"] = new_id
    raw = _csv_bytes(rows)
    second = att.pop(second_id)
    att[new_id] = replace(second, game_id=new_id, home_team=duplicated_home_team)
    _held("NFL_WEEK_DUPLICATE_TEAM_WITHIN_WEEK", raw=raw, attestations=att,
          capture=replace(capture, content_sha256=hashlib.sha256(raw).hexdigest()))


def test_provider_alias_or_wrong_week_team_id_is_not_canonical_nflverse():
    _, rows, capture, att = _inputs()
    first_id = rows[0]["game_id"]
    for forged_id in ("espn-401999001", "2026_06_NYG_DAL", "2026_07_XYZ_DAL"):
        forged = [dict(row) for row in rows]
        forged[0]["game_id"] = forged_id
        raw = _csv_bytes(forged)
        forged_att = dict(att)
        fixture = forged_att.pop(first_id)
        forged_att[forged_id] = replace(fixture, game_id=forged_id)
        _held("NFL_WEEK_CANONICAL_GAME_ID_FORMAT_INVALID",
              raw=raw, attestations=forged_att,
              capture=replace(capture, content_sha256=hashlib.sha256(raw).hexdigest()))


def test_syntactically_valid_but_non_nfl_team_code_is_rejected():
    _, rows, capture, att = _inputs()
    original = rows[0]["game_id"]
    invalid_code = "XYZ"
    rows[0]["home_team"] = invalid_code
    rows[0]["game_id"] = f'2026_07_{rows[0]["away_team"]}_{invalid_code}'
    new_id = rows[0]["game_id"]
    raw = _csv_bytes(rows)
    old = att.pop(original)
    att[new_id] = replace(old, game_id=new_id, home_team=invalid_code)
    _held("NFL_WEEK_CANONICAL_GAME_ID_FORMAT_INVALID", raw=raw,
          attestations=att, capture=replace(
              capture, content_sha256=hashlib.sha256(raw).hexdigest(),
          ))


def test_scored_game_cannot_be_labeled_pregame():
    _, rows, capture, _ = _inputs()
    rows[0]["home_score"] = "21"
    raw = _csv_bytes(rows)
    _held("NFL_WEEK_SCORE_VS_PREGAME_CONFLICT", raw=raw, capture=replace(
        capture, content_sha256=hashlib.sha256(raw).hexdigest()
    ))


def test_duplicate_canonical_id_cannot_inflate_week_count():
    _, rows, capture, _ = _inputs()
    rows.append(dict(rows[0]))
    raw = _csv_bytes(rows)
    _held("NFL_WEEK_DUPLICATE_OR_MISSING_CANONICAL_ID", raw=raw, capture=replace(
        capture, content_sha256=hashlib.sha256(raw).hexdigest()
    ))


def test_gameday_conflict_and_unknown_status_rejected():
    _, rows, capture, _ = _inputs()
    rows[0]["gameday"] = "2026-11-11"
    raw = _csv_bytes(rows)
    _held("NFL_WEEK_GAMEDAY_KICKOFF_CONFLICT", raw=raw, capture=replace(
        capture, content_sha256=hashlib.sha256(raw).hexdigest()
    ))
    _, _, _, att = _inputs()
    first = next(iter(att))
    _held("NFL_WEEK_KICKOFF_TIME_OR_STATUS_UNVERIFIED",
          attestations={**att, first: replace(att[first], status="UNKNOWN")})


def test_declared_window_must_be_original_source_evidence_not_inferred():
    bad = replace(WINDOW, end_utc=WINDOW.start_utc)
    _held("NFL_WEEK_DECLARED_WINDOW_TIME_INVALID", weeks=(bad,))
    overlapping = replace(WINDOW, week=8)
    _held("NFL_WEEK_DECLARED_WINDOWS_OVERLAP", weeks=(WINDOW, overlapping))
    _held("NFL_WEEK_DECLARED_WINDOWS_INVALID", weeks=())


def test_correctly_holds_thin_12_game_cohort_without_loosening_governance():
    raw, _, capture, attest = _inputs(count=12)
    inventory, weeks = _build(raw, capture=capture, attestations=attest)
    lane = Lane("NFL", "MONEYLINE", "scoreWowTeamEventRequest", "NFL_GAME_WIN_V17")
    selection = choose_current_pregame_cohort(
        lane, inventory, weeks, now=NOW, minimum_unique_events=13,
    )
    assert selection["status"] == "COHORT_HELD"
    assert "ACCEPTANCE_ELIGIBLE_COHORT_INSUFFICIENT" in (
        selection["attempted_week_receipts"][0]["blockers"]
    )
    assert selection["can_execute"] is False


def _combined(raw, capture, attest, post):
    return execute_verified_nfl_week_preflight(
        raw,
        capture=capture,
        kickoff_evidence=attest,
        declared_weeks=(WINDOW,),
        trusted_now=NOW,
        post_daily_snapshot=post,
    )


def test_joined_week_authenticates_daily_receipts_and_reconciles_all_13_without_publishing():
    raw, _, capture, attest = _inputs()
    calls = []

    def post(payload):
        day = payload["requested_slate_date"]
        calls.append(dict(payload))
        event_ids = [
            x.game_id for x in attest.values()
            if x.start_utc.astimezone(ZoneInfo("America/Chicago")).date().isoformat() == day
        ]
        return AuthenticatedSnapshotResponse(200, AUDIENCE, True, {
            "requested_slate_date": day, "requested_timezone": "America/Chicago",
            "run_id": "synthetic-" + day, "run_status": "COMPLETED",
            "terminal": True, "automation_auth": "GITHUB_ACTIONS_OIDC",
            "can_execute": False, "global_terminal_authority": "V17_TERMINAL_REDUCER",
            "reconciliation": {"balanced": True},
            "cross_sport_discovery_audit": {"reconciliation": {
                "row_reconciliation": "PASS", "events_discovered": len(event_ids),
                "events_accounted": len(event_ids),
            }},
            "rows": [{"lane": "MONEYLINE", "can_execute": False,
                      "identity": {"sport": "NFL", "official_event_id": eid},
                      "result": {}} for eid in event_ids],
        })

    result = _combined(raw, capture, attest, post)
    assert result["status"] == "WEEK_SNAPSHOT_RECONCILED_NOT_ACTION_OR_MODEL_CERTIFIED"
    assert result["nfl_rows"] == 13
    assert len(calls) == 2
    assert len({p["requested_slate_date"] for p in calls}) == 2
    assert all(x["lanes"] == ["MONEYLINE"] and x["max_props"] == 0 for x in calls)
    assert all(x["max_team_events"] == 12 for x in calls)
    assert result["can_execute"] is False and result["probability_publishable"] is False


def test_joined_stale_sha_blocks_every_scoring_post():
    raw, _, capture, attest = _inputs()
    calls = []
    result = _combined(raw, replace(capture, content_sha256="0" * 64),
                       attest, lambda payload: calls.append(payload))
    assert result["status"] == "PREFLIGHT_HELD"
    assert result["blockers"] == ["NFL_WEEK_SOURCE_DIGEST_UNVERIFIED"]
    assert calls == []


def test_joined_thin_verified_week_cannot_dispatch_or_reduce_threshold():
    raw, _, capture, attest = _inputs(count=12)
    calls = []
    result = _combined(raw, capture, attest, lambda payload: calls.append(payload))
    assert result["status"] == "COHORT_HELD"
    assert result["can_execute"] is False and result["probability_publishable"] is False
    assert result["attempted_week_receipts"][0]["eligible_count"] == 12
    assert calls == []


def test_joined_ambiguous_scoring_post_never_retries_second_day():
    raw, _, capture, attest = _inputs()
    calls = []
    def post(payload):
        calls.append(dict(payload))
        raise TimeoutError("synthetic lost acknowledgment")
    result = _combined(raw, capture, attest, post)
    assert result["status"] == "PREFLIGHT_HELD"
    assert result["blockers"] == ["NFL_WEEK_SNAPSHOT_TRANSPORT_UNVERIFIED"]
    assert len(calls) == 1


def test_joined_untrusted_plain_dictionary_cannot_pass_https_gate():
    raw, _, capture, attest = _inputs()
    calls = []
    def post(payload):
        calls.append(payload)
        return {"requested_slate_date": payload["requested_slate_date"], "run_status": "COMPLETED"}
    result = _combined(raw, capture, attest, post)
    assert result["status"] == "PREFLIGHT_HELD"
    assert result["blockers"] == ["NFL_WEEK_AUTHENTICATED_TRANSPORT_REQUIRED"]
    assert len(calls) == 1
