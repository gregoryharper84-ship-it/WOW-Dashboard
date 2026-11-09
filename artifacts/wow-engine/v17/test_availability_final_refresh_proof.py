"""Release evidence: the final refresh performs a genuine reacquisition.

A hold is not a demonstrated refresh. These tests drive the real
``score_pick_request`` route and assert that, inside the pregame window, the
runtime issues exactly one additional official read, that the persisted
evidence advances to that newer observation instead of replaying the cached
one, and that unchanged or unavailable evidence never promotes the row.

The official response payloads are injected here; no live provider call is
made. This proves the plumbing and the change detection, not the live feed
shape, which the report states separately.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pick_request_runtime_core as runtime_core
import test_pick_request_runtime as harness
from v17.test_availability_api_route import (
    _confirmed_block,
    _post,
    _provisional_row,
)
from v17.player_availability_gate import PLAYER_AVAILABILITY_RECHECK_REQUIRED

OFFICIAL_RECHECK_SOURCE = "MLB_STATS_API_OFFICIAL_STARTER_RECHECK"


def _spy(block: dict):
    calls: list[dict] = []

    def recheck(**kwargs):
        calls.append(kwargs)
        return block

    return recheck, calls


def test_final_refresh_issues_a_second_official_read_and_persists_it(monkeypatch):
    client, persisted, _routed, _scored = harness._build(monkeypatch)
    row = _provisional_row("proof-reacquire")
    provisional = row["evidence"]["role_status"]["availability"]
    provisional_captured = datetime.fromisoformat(provisional["captured_at"])
    observed_captured = datetime.now(timezone.utc).isoformat()
    recheck, calls = _spy(_confirmed_block(captured_at=observed_captured))
    monkeypatch.setattr(runtime_core, "recheck_provisional_participation", recheck)

    terminal = _post(client, [row]).json()["rows"][0]

    # Exactly one additional acquisition, shaped like the provisional obligation.
    assert len(calls) == 1
    call = calls[0]
    assert call["sport"] == "MLB"
    assert call["player"] == row["player"]
    assert call["event_start_time"] == row["event_start_time"]
    assert call["availability"]["basis"] == "PROBABLE_STARTER"
    assert call["availability"]["requires_revalidation"] is True

    # The persisted evidence is the newer observation, not a replay.
    stored = persisted[0]["role_status"]["availability"]
    assert stored["basis"] == "CONFIRMED_STARTER"
    assert datetime.fromisoformat(stored["captured_at"]) > provisional_captured
    assert stored["source"] == OFFICIAL_RECHECK_SOURCE
    assert stored["evidence_status"] == "RETRIEVED"
    assert stored["report_complete"] is True
    assert stored["athlete_id"] == provisional["athlete_id"]
    assert stored["event_id"] == provisional["event_id"]

    # Retrieval time is recorded alongside the source evidence time.
    receipt = terminal["acquisition"]["availability_recheck"]
    assert receipt["status"] == "CONFIRMED"
    assert datetime.fromisoformat(receipt["checked_at"]) >= provisional_captured
    assert datetime.fromisoformat(receipt["checked_at"]) >= datetime.fromisoformat(stored["captured_at"])

    assert persisted[0]["blockers"] == []
    assert terminal["terminal_status"] == "COMPLETED"


def test_research_score_outside_the_window_reads_nothing_again(monkeypatch):
    client, _persisted, _routed, _scored = harness._build(monkeypatch)
    recheck, calls = _spy(_confirmed_block())
    monkeypatch.setattr(runtime_core, "recheck_provisional_participation", recheck)
    row = harness._row("proof-not-due")
    row["evidence"]["role_status"]["availability"] = _provisional_row(
        "proof-seed"
    )["evidence"]["role_status"]["availability"]

    terminal = _post(client, [row]).json()["rows"][0]

    assert calls == []
    assert terminal["acquisition"]["availability_recheck"]["status"] == "NOT_DUE"
    assert terminal["acquisition"]["availability_status"] == "PROVISIONAL_RECHECK_REQUIRED"
    assert terminal["terminal_status"] == "HELD"


def test_unchanged_evidence_is_detected_and_not_promoted(monkeypatch):
    client, persisted, _routed, _scored = harness._build(monkeypatch)
    unchanged = _confirmed_block(
        status="PROBABLE_STARTER",
        basis="PROBABLE_STARTER",
        confirmation="PROBABLE",
        source="MLB_STATS_API_SCHEDULE_PROBABLE_PITCHER",
    )
    recheck, calls = _spy(unchanged)
    monkeypatch.setattr(runtime_core, "recheck_provisional_participation", recheck)

    terminal = _post(client, [_provisional_row("proof-unchanged")]).json()["rows"][0]

    assert len(calls) == 1
    assert terminal["acquisition"]["availability_recheck"]["status"] == "INCONCLUSIVE"
    assert terminal["terminal_status"] == "HELD"
    assert terminal["rank_eligible"] is False
    assert persisted[0]["blockers"] == [PLAYER_AVAILABILITY_RECHECK_REQUIRED]
    assert persisted[0]["role_status"]["availability"]["basis"] == "PROBABLE_STARTER"


def test_timeout_at_final_refresh_keeps_the_publication_hold(monkeypatch):
    client, persisted, _routed, _scored = harness._build(monkeypatch)

    def timeout(**_kwargs):
        raise TimeoutError("official provider timed out")

    monkeypatch.setattr(runtime_core, "recheck_provisional_participation", timeout)

    terminal = _post(client, [_provisional_row("proof-timeout")]).json()["rows"][0]

    receipt = terminal["acquisition"]["availability_recheck"]
    assert receipt["status"] == "SOURCE_UNAVAILABLE"
    assert receipt["error_type"] == "TimeoutError"
    assert terminal["terminal_status"] == "HELD"
    assert persisted[0]["blockers"] == [PLAYER_AVAILABILITY_RECHECK_REQUIRED]
