"""Availability contracts through the real ``score_pick_request`` route.

The gate is only useful if its verdict survives the route: rejection before the
scorer for a ruled-out player, a research-only hold for unverified evidence, a
recheck block for provisional probable-starter clearance, and a changed ruling
between two requests for the same row.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pick_request_runtime_core as runtime_core
import test_pick_request_runtime as harness
from v17.player_availability_gate import PLAYER_AVAILABILITY_RECHECK_REQUIRED


def _with_availability(row: dict, **changes) -> dict:
    """Apply designation changes to a row's official availability evidence."""
    row["evidence"]["role_status"]["availability"].update(changes)
    return row


def _post(client, rows):
    """POST rows to the canonical pick-request action route."""
    return client.post("/score-pick-request", json={"rows": rows})


def test_ruled_out_row_is_rejected_before_the_scorer_runs(monkeypatch):
    client, persisted, _routed, scored = harness._build(monkeypatch)
    row = _with_availability(harness._row("api-out"), status="OUT", designation="OUT")

    body = _post(client, [row]).json()
    terminal = body["rows"][0]

    assert terminal["terminal_status"] == "REJECTED"
    assert terminal["code"] == "PLAYER_RULED_OUT"
    assert terminal["acquisition"]["mode"] == "CALLER_SUPPLIED_RAW_EVIDENCE"
    assert scored == []
    assert persisted == []
    assert body["rows_rejected"] == 1
    assert body["rows_completed"] == 0
    assert body["can_execute"] is False


def test_row_without_participation_evidence_is_held_out_of_the_verified_pool(monkeypatch):
    client, _persisted, _routed, scored = harness._build(monkeypatch)
    row = harness._row("api-unverified")
    del row["evidence"]["role_status"]["availability"]

    body = _post(client, [row]).json()
    terminal = body["rows"][0]

    # Research scoring is still permitted...
    assert len(scored) == 1
    # ...but the row cannot be completed or rank eligible.
    assert body["rows_held"] == 1
    assert body["rows_completed"] == 0
    assert terminal["terminal_status"] == "HELD"
    assert terminal["rank_eligible"] is False
    assert "PLAYER_AVAILABILITY_UNVERIFIED" in json.dumps(terminal)


def test_provisional_probable_starter_is_scored_but_needs_a_final_recheck(monkeypatch):
    client, _persisted, _routed, scored = harness._build(monkeypatch)
    row = harness._row("api-provisional")
    row["evidence"]["role_status"]["availability"] = {
        "status": "PROBABLE_STARTER",
        "basis": "PROBABLE_STARTER",
        "confirmation": "PROBABLE",
        "source": "MLB_STATS_API_SCHEDULE_PROBABLE_PITCHER",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "evidence_status": "RETRIEVED",
        "fresh": True,
        "athlete_id": "660271",
        "event_id": "823001",
        "team": "LAD",
    }

    body = _post(client, [row]).json()
    terminal = body["rows"][0]

    assert len(scored) == 1
    assert body["rows_held"] == 1
    assert terminal["terminal_status"] == "HELD"
    assert terminal["rank_eligible"] is False
    # The native terminal family is the unverified hold (provisional clearance is
    # not a rule-out), and the acquisition receipt names the recheck obligation.
    assert terminal["code"] == "PLAYER_AVAILABILITY_UNVERIFIED"
    assert terminal["acquisition"]["availability_status"] == "PROVISIONAL_RECHECK_REQUIRED"
    assert terminal["acquisition"]["availability_recheck"]["status"] == "NOT_DUE"
    assert "PLAYER_AVAILABILITY_UNVERIFIED" in json.dumps(terminal)


def _provisional_row(row_key: str) -> dict:
    """A probable-starter row whose game starts inside the recheck window."""
    row = harness._row(row_key)
    row["event_start_time"] = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    row["evidence"]["role_status"]["availability"] = {
        "status": "PROBABLE_STARTER",
        "basis": "PROBABLE_STARTER",
        "confirmation": "PROBABLE",
        "source": "MLB_STATS_API_SCHEDULE_PROBABLE_PITCHER",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "evidence_status": "RETRIEVED",
        "fresh": True,
        "athlete_id": "660271",
        "event_id": "823001",
        "team": "LAD",
    }
    return row


def _confirmed_block(**overrides) -> dict:
    block = {
        "status": "AVAILABLE",
        "basis": "CONFIRMED_STARTER",
        "confirmation": "CONFIRMED",
        "source": "MLB_STATS_API_OFFICIAL_STARTER_RECHECK",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "fresh": True,
        "evidence_status": "RETRIEVED",
        "report_complete": True,
        "athlete_id": "660271",
        "event_id": "823001",
        "team": "LAD",
    }
    block.update(overrides)
    return block


def test_final_recheck_confirmation_clears_the_hold(monkeypatch):
    client, persisted, _routed, scored = harness._build(monkeypatch)
    monkeypatch.setattr(
        runtime_core,
        "recheck_provisional_participation",
        lambda **kwargs: _confirmed_block(captured_at=kwargs["now"].isoformat()),
    )

    body = _post(client, [_provisional_row("api-recheck-confirmed")]).json()
    terminal = body["rows"][0]

    assert len(scored) == 1
    assert body["rows_completed"] == 1
    assert terminal["terminal_status"] == "COMPLETED"
    assert terminal["acquisition"]["availability_status"] == "AVAILABLE"
    assert terminal["acquisition"]["availability_recheck"]["status"] == "CONFIRMED"
    assert persisted[0]["blockers"] == []
    assert persisted[0]["role_status"]["availability"]["basis"] == "CONFIRMED_STARTER"


def test_final_recheck_rule_out_rejects_before_the_scorer(monkeypatch):
    client, persisted, _routed, scored = harness._build(monkeypatch)
    monkeypatch.setattr(
        runtime_core,
        "recheck_provisional_participation",
        lambda **_kwargs: _confirmed_block(status="OUT", designation="OUT"),
    )

    body = _post(client, [_provisional_row("api-recheck-out")]).json()

    assert body["rows"][0]["code"] == "PLAYER_RULED_OUT"
    assert body["rows_rejected"] == 1
    assert scored == []
    assert persisted == []


def test_unavailable_recheck_source_keeps_the_hold(monkeypatch):
    client, persisted, _routed, scored = harness._build(monkeypatch)

    def unavailable(**_kwargs):
        raise RuntimeError("official source unavailable")

    monkeypatch.setattr(runtime_core, "recheck_provisional_participation", unavailable)

    body = _post(client, [_provisional_row("api-recheck-source")]).json()
    terminal = body["rows"][0]

    assert len(scored) == 1
    assert body["rows_held"] == 1
    assert terminal["acquisition"]["availability_recheck"]["status"] == "SOURCE_UNAVAILABLE"
    assert persisted[0]["blockers"] == [PLAYER_AVAILABILITY_RECHECK_REQUIRED]


def test_post_hydration_ruling_change_rejects_the_second_attempt(monkeypatch):
    client, persisted, _routed, scored = harness._build(monkeypatch)

    first = _post(client, [harness._row("api-change")]).json()
    assert first["rows_completed"] == 1

    changed = _with_availability(harness._row("api-change"), status="OUT", designation="OUT")
    second = _post(client, [changed]).json()

    assert second["rows_rejected"] == 1
    assert second["rows"][0]["code"] == "PLAYER_RULED_OUT"
    assert len(scored) == 1  # only the first, cleared attempt was scored
    assert len(persisted) == 1
    assert second["can_execute"] is False
