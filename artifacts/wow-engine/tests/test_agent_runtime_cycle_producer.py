"""Fail-closed regression coverage for #2317's real-candidate producer."""
from __future__ import annotations

import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from agent_runtime.source_discovery import select_current_daily_candidates
from agent_runtime.runner import execute_envelope
from agent_runtime.schemas import WorkerJobEnvelope
from v17.agent_runtime_cycle_producer import admission_for_hour, admit_cycle

NOW = datetime(2026, 10, 31, 13, 19, tzinfo=timezone.utc)
EVENT = "2026-10-31T20:00:00Z"
RUN_ID = "b760d5af-bd43-4f87-82f8-77c54bb6f31e"


def moneyline_row(*, sport="NFL", event=EVENT, captured=None, status="PREGAME",
                  event_id="official-nfl-1"):
    return {
        "lane": "MONEYLINE",
        "row_status": "HELD",
        "captured_at": (captured or NOW - timedelta(minutes=25)).isoformat(),
        "run_id": "source-daily-run",
        "identity": {
            "sport": sport, "official_event_id": event_id,
            "home_team": "Home Team", "away_team": "Away Team",
            "commence_time_utc": event,
        },
        "detail": {"result": {"event_status": status, "league": sport}},
    }


def test_only_current_verified_events_are_discovered():
    rows = [
        moneyline_row(), moneyline_row(),  # same event from overlapping source
        moneyline_row(event_id="old", event="2026-10-30T20:00:00Z"),
        moneyline_row(event_id="started", status="LIVE"),
        moneyline_row(event_id="stale", captured=NOW - timedelta(hours=9)),
        moneyline_row(sport="SOCCER", event_id="3way"),
    ]
    candidates, skipped = select_current_daily_candidates(rows, now=NOW)
    assert len(candidates) == 1
    one = candidates[0]
    assert one["official_event_id"] == "official-nfl-1"
    assert one["participant"] == "Home Team"
    assert one["market_family"] == "OUTRIGHT_WINNER"
    assert one["discovery_authority"] == "RESEARCH_INTEREST"
    assert one["can_execute"] is False
    assert one["event_status"] == "CURRENT_STATUS_UNVERIFIED"
    assert one["settlement_identity_verified"] is False
    assert "model_probability" not in one and "calibrated_lower_bound" not in one
    assert skipped["DUPLICATE_SOURCE_INSTANCE"] == 1
    assert skipped["NOT_CURRENT_PREGAME"] == 1
    assert skipped["EVENT_STATUS_NOT_PREGAME"] == 1
    assert skipped["STALE_SOURCE"] == 1
    assert skipped["SETTLEMENT_ADAPTER_NOT_VERIFIED"] == 1


def test_unresolved_identity_and_bad_timestamps_cannot_be_promoted():
    cases = [
        moneyline_row(event="2026-10-31T20:00:00"),  # no timezone
        moneyline_row(captured=NOW + timedelta(hours=2)),
        {**moneyline_row(), "detail": {"result": {}}},
        {**moneyline_row(), "identity": {"sport": "NFL"}},
        {**moneyline_row(), "lane": "PROPS"},
        {**moneyline_row(), "run_id": "bad\ncontrol"},
    ]
    candidates, skipped = select_current_daily_candidates(cases, now=NOW)
    assert not candidates
    assert sum(skipped.values()) == len(cases)
    assert skipped["NOT_CURRENT_PREGAME"] == 1
    assert skipped["STALE_SOURCE"] == 1
    assert skipped["SOURCE_RUN_ID_INVALID"] == 1


def test_bound_enforced_without_creating_unscored_filler():
    rows = [moneyline_row(event_id=f"event-{x}") for x in range(35)]
    candidates, skipped = select_current_daily_candidates(rows, now=NOW)
    assert len(candidates) == 32
    assert skipped["DISCOVERY_CANDIDATE_LIMIT"] == 3


def test_source_batch_and_clock_are_bounded():
    with pytest.raises(ValueError, match="DISCOVERY_SOURCE_BATCH_INVALID"):
        select_current_daily_candidates([{}] * 257, now=NOW)
    with pytest.raises(ValueError, match="DISCOVERY_CLOCK_NOT_AWARE"):
        select_current_daily_candidates([], now=NOW.replace(tzinfo=None))


def test_scheduled_hour_creates_stable_idempotent_nonexecution_envelope():
    key1, req1 = admission_for_hour(NOW)
    key2, req2 = admission_for_hour(NOW + timedelta(minutes=24))
    assert key1 == key2 == "wow-agent-prod-2026103113"
    assert req1 == req2
    assert req1["discovery_enabled"] is True
    assert req1["candidate_inputs"] == []
    assert req1["can_execute"] is False


def test_producer_rejects_missing_or_short_credential_without_network():
    with patch("urllib.request.urlopen") as network:
        with pytest.raises(ValueError, match="PRODUCER_AUTH_OR_ORIGIN_UNAVAILABLE"):
            admit_cycle(now=NOW, origin="https://example.invalid", runtime_key="short")
        network.assert_not_called()


class Response(io.BytesIO):
    status = 202


def test_producer_posts_scoped_token_and_reconciles_202_run_receipt():
    body = json.dumps({
        "ok": True, "run_id": RUN_ID, "status": "DISCOVERY_QUEUED",
        "reused": False, "can_execute": False,
    }).encode()
    auth_reject = urllib.error.HTTPError("https://wow-governed-probability-engine.onrender.com/wow/runs",
                                         401, "Unauthorized", {}, None)
    with patch("urllib.request.urlopen", side_effect=[auth_reject, Response(body)]) as network:
        receipt = admit_cycle(
            now=NOW, origin="https://wow-governed-probability-engine.onrender.com",
            runtime_key="x" * 40,
        )
    assert network.call_count == 2
    probe_request = network.call_args_list[0].args[0]
    assert probe_request.get_header("Authorization") is None
    assert probe_request.get_method() == "GET"
    assert probe_request.data is None
    assert probe_request.full_url.endswith("/wow/runs/not-a-valid-uuid")
    request = network.call_args_list[1].args[0]
    assert request.get_method() == "POST"
    assert request.full_url == "https://wow-governed-probability-engine.onrender.com/wow/runs"
    assert request.get_header("Idempotency-key") == "wow-agent-prod-2026103113"
    assert request.get_header("Authorization") == "Bearer " + "x" * 40
    posted = json.loads(request.data)
    assert posted["can_execute"] is False and posted["discovery_enabled"] is True
    assert receipt["run_id"] == RUN_ID
    assert receipt["job_execution_verified"] is False
    assert receipt["model_probability_verified"] is False


def test_producer_cannot_treat_an_untrusted_response_as_admission():
    bad = json.dumps({
        "ok": True, "run_id": RUN_ID, "can_execute": True,
    }).encode()
    auth_reject = urllib.error.HTTPError("https://wow-governed-probability-engine.onrender.com/wow/runs",
                                         401, "Unauthorized", {}, None)
    with patch("urllib.request.urlopen", side_effect=[auth_reject, Response(bad)]):
        with pytest.raises(RuntimeError, match="PRODUCER_RUN_RECEIPT_INVALID"):
            admit_cycle(now=NOW, origin="https://wow-governed-probability-engine.onrender.com",
                        runtime_key="x" * 40)


def test_old_unsecured_runtime_422_fails_closed_before_real_post():
    old_response = urllib.error.HTTPError(
        "https://wow-governed-probability-engine.onrender.com/wow/runs",
        422, "EXECUTION_PROHIBITED", {}, None,
    )
    with patch("urllib.request.urlopen", side_effect=old_response) as network:
        with pytest.raises(RuntimeError, match="PRODUCER_AUTH_PRECONDITION_NOT_DEPLOYED"):
            admit_cycle(now=NOW, origin="https://wow-governed-probability-engine.onrender.com",
                        runtime_key="x" * 40)
        network.assert_called_once()


def test_runtime_credential_rejects_header_injection_without_network():
    with patch("urllib.request.urlopen") as network:
        with pytest.raises(ValueError, match="PRODUCER_CREDENTIAL_INVALID"):
            admit_cycle(
                now=NOW, origin="https://wow-governed-probability-engine.onrender.com",
                runtime_key="A" * 40 + "\r\nInjected: yes",
            )
        network.assert_not_called()


def test_producer_origin_cannot_be_redirected_to_attacker_host():
    with patch("urllib.request.urlopen") as network:
        with pytest.raises(ValueError, match="PRODUCER_ORIGIN_NOT_APPROVED"):
            admit_cycle(now=NOW, origin="https://wow.example",
                        runtime_key="x" * 40)
        network.assert_not_called()


def test_snapshot_candidate_cannot_reach_model_without_live_status_and_settlement():
    candidates, _ = select_current_daily_candidates([moneyline_row()], now=NOW)
    candidate = candidates[0]
    env = WorkerJobEnvelope(
        run_id="r1", job_id="j1", candidate_id="c1",
        worker_id="wow.slate-integrity-expert", worker_version="1.0.0",
        as_of=NOW.isoformat(), input_hash="h", payload={"candidate": candidate},
    )
    held = execute_envelope(env)
    assert held.status == "BLOCKED"
    assert "SLATE_IDENTITY_INCOMPLETE" in held.blockers
    assert held.output["model_invocation_allowed"] is False
    assert held.output["missing_fields"] == [
        "fresh_event_status", "verified_settlement_identity",
    ]
    candidate["event_status"] = "PREGAME"
    candidate["settlement_identity_verified"] = True
    env.payload["candidate"] = candidate
    passed = execute_envelope(env)
    assert passed.status == "SUCCEEDED"
    assert passed.ceiling == "IDENTITY_VERIFIED"
