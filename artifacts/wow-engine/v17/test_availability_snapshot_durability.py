"""Durability of the availability verdict from the route into the snapshot row.

The gate verdict is only durable if it reaches ``wow_prop_evidence_snapshots``.
A re-reader that sees ``hydration_status=PASS`` and ``blockers=[]`` treats a row
as ready evidence, so an availability hold that lived only in the HTTP response
would disappear after the run. These tests use the real route and the exact
persisted payload, not a second implementation of the same rule.
"""
from __future__ import annotations

from datetime import datetime, timezone

import test_pick_request_runtime as harness
from v17.daily_terminal_reduction import classify_stage_status
from v17.player_availability_gate import (
    PLAYER_AVAILABILITY_RECHECK_REQUIRED,
    PLAYER_AVAILABILITY_UNVERIFIED,
    PLAYER_RULED_OUT,
)


def _post(client, rows):
    """POST rows to the canonical pick-request action route."""
    return client.post("/score-pick-request", json={"rows": rows})


def test_unverified_row_persists_the_hold_on_its_snapshot(monkeypatch):
    client, persisted, _routed, scored = harness._build(monkeypatch)
    row = harness._row("durable-unverified")
    del row["evidence"]["role_status"]["availability"]

    terminal = _post(client, [row]).json()["rows"][0]

    assert len(scored) == 1
    # Evidence itself is complete; the gate verdict is what blocks the row.
    assert persisted[0]["hydration_status"] == "PASS"
    assert persisted[0]["role_status"]["status"] == "ACTIVE"
    assert persisted[0]["blockers"] == [PLAYER_AVAILABILITY_UNVERIFIED]
    assert terminal["code"] == PLAYER_AVAILABILITY_UNVERIFIED
    # The identifier is evidence identity, so it must not depend on the verdict.
    assert terminal["source_snapshot_id"] == persisted[0]["source_snapshot_id"]


def test_provisional_row_persists_the_recheck_obligation(monkeypatch):
    client, persisted, _routed, scored = harness._build(monkeypatch)
    row = harness._row("durable-provisional")
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

    _post(client, [row])

    assert len(scored) == 1
    assert persisted[0]["blockers"] == [PLAYER_AVAILABILITY_RECHECK_REQUIRED]


def test_cleared_row_persists_an_empty_blocker_list(monkeypatch):
    client, persisted, _routed, _scored = harness._build(monkeypatch)

    _post(client, [harness._row("durable-cleared")])

    assert persisted[0]["blockers"] == []
    assert persisted[0]["hydration_status"] == "PASS"


def test_ruled_out_row_never_reaches_the_evidence_store(monkeypatch):
    client, persisted, _routed, scored = harness._build(monkeypatch)
    row = harness._row("durable-out")
    row["evidence"]["role_status"]["availability"].update(status="OUT", designation="OUT")

    terminal = _post(client, [row]).json()["rows"][0]

    # A settled rule-out is a terminal decision, not frozen evidence to re-read.
    assert terminal["code"] == PLAYER_RULED_OUT
    assert persisted == []
    assert scored == []


def test_durable_blocker_labels_classify_as_held_in_daily():
    # Daily reads the label recorded on the durable row; it must never soften a
    # hold into a completion, and a rule-out must purge rather than hold.
    assert classify_stage_status(
        {
            "terminal_label": PLAYER_AVAILABILITY_UNVERIFIED,
            "probability_publishable": True,
            "rank_eligible": False,
        }
    ) == "HELD"
    assert classify_stage_status(
        {
            "terminal_label": PLAYER_AVAILABILITY_RECHECK_REQUIRED,
            "probability_publishable": True,
            "rank_eligible": False,
        }
    ) == "HELD"
    assert classify_stage_status({"terminal_label": PLAYER_RULED_OUT}) == "PURGED"
