from __future__ import annotations

import asyncio

from fastapi import HTTPException

from v17 import scout_handoff_queue as queue
from v17.scout_handoff_queue import build_handoff_plan, process_claimed_job


def _handoff():
    prop = {
        "official_event_id": "mlb-event-1",
        "sport_key": "baseball_mlb",
        "commence_time": "2026-10-03T20:10:00Z",
        "home_team": "Home",
        "away_team": "Away",
        "route": "WOW_PROP_LANE",
        "research_ceiling": "RESEARCH_INTEREST",
        "market_evidence": {
            "bookmaker": "book-a",
            "market_key": "pitcher_strikeouts",
            "outcome_name": "Over",
            "description": "Example Pitcher",
            "price": -120,
            "point": 5.5,
            "market_last_update": "2026-10-03T14:00:00Z",
        },
    }
    duplicate = {
        **prop,
        "market_evidence": {**prop["market_evidence"], "bookmaker": "book-b", "price": -118},
    }
    event = {
        "official_event_id": "12345",
        "sport_key": "baseball_mlb",
        "commence_time": "2026-10-03T20:10:00Z",
        "home_team": "Home",
        "away_team": "Away",
        "route": "LLP_TEAM_BETTING_ENGINE",
        "research_ceiling": "RESEARCH_INTEREST",
        "market_evidence": [],
    }
    return {
        "status": "DISCOVERY_COMPLETE",
        "generated_at": "2026-10-03T14:01:00Z",
        "run_id": "wow-scout-test-1",
        "research_run_id": "wow-scout-test-1",
        "model_handoff_ready": True,
        "model_handoff": {
            "prop_candidates": [prop, duplicate],
            "team_event_candidates": [event],
        },
        "governance": {"can_execute": False},
    }


def test_plan_collapses_duplicate_books_and_fans_out_team_objectives():
    plan = build_handoff_plan(_handoff())
    assert plan.can_execute is False
    assert plan.governance["queue_probability_authority"] is False
    assert plan.governance["v17_terminal_reducer_is_terminal_authority"] is True

    queued = [row for row in plan.candidates if row.red_team_status == "RED_TEAM_PASSED"]
    assert len(queued) == 3
    props = [row for row in queued if row.target_lane == "WOW_PROP_LANE"]
    teams = [row for row in queued if row.target_lane == "LLP_TEAM_BETTING_ENGINE"]
    assert len(props) == 1
    assert len(teams) == 2
    assert {row.request_payload["objective_lane"] for row in teams} == {
        "OUTRIGHT_WIN_PROBABILITY",
        "UPSET_PROBABILITY",
    }
    assert all(row.can_execute is False for row in queued)
    assert all("model_probability" not in row.request_payload for row in queued)
    assert all("calibrated_probability" not in row.request_payload for row in queued)


def test_mapping_failure_becomes_explicit_handoff_block_not_silent_drop():
    handoff = _handoff()
    handoff["model_handoff"]["team_event_candidates"][0]["home_team"] = ""
    plan = build_handoff_plan(handoff)

    blocked = [row for row in plan.candidates if row.red_team_status == "HANDOFF_BLOCKED"]
    assert len(blocked) == 1
    assert blocked[0].target_lane == "LLP_TEAM_BETTING_ENGINE"
    assert blocked[0].blocked_code == "TEAM_EVENT_IDENTITY_INCOMPLETE"
    assert blocked[0].request_payload == {}
    assert blocked[0].can_execute is False


class _Response:
    def __init__(self, data):
        self.data = data


class _RPC:
    def __init__(self, db, name, params):
        self.db = db
        self.name = name
        self.params = params

    def execute(self):
        self.db.calls.append((self.name, self.params))
        if self.name == "wow_finish_scout_handoff_job":
            receipt = self.params.get("p_specialist_receipt") or {}
            result = receipt.get("result") if isinstance(receipt, dict) else {}
            rows = (result.get("outcomes") or result.get("rows") or []) if isinstance(result, dict) else []
            outcome = rows[0] if len(rows) == 1 and isinstance(rows[0], dict) else {}
            qualified = (
                str(outcome.get("terminal_status") or outcome.get("status") or "").upper() == "COMPLETED"
                and outcome.get("probability_publishable") is True
                and outcome.get("rank_eligible") is True
                and outcome.get("card_admission_eligible") is True
                and outcome.get("can_execute") is False
                and result.get("can_execute") is False
                and receipt.get("can_execute") is False
            )
            state = "V17_QUALIFIED" if qualified else "MODEL_EVALUATED"
        elif self.name == "wow_retry_scout_handoff_job":
            state = "SPECIALIST_HANDOFF_QUEUED"
        else:
            state = "HANDOFF_BLOCKED"
        return _Response({"current_state": state, "can_execute": False})


class _DB:
    def __init__(self):
        self.calls = []

    def rpc(self, name, params):
        return _RPC(self, name, params)


def _prop_job():
    plan = build_handoff_plan(_handoff())
    row = next(row for row in plan.candidates if row.target_lane == "WOW_PROP_LANE")
    return {
        "job_id": "00000000-0000-0000-0000-000000000001",
        "source_run_id": row.source_run_id,
        "research_run_id": row.research_run_id,
        "candidate_id": row.candidate_id,
        "target_lane": row.target_lane,
        "target_route": row.target_route,
        "request_id": row.request_id,
        "request_payload": row.request_payload,
        "attempt_count": 1,
    }


def _team_job(sport="NFL", league="NFL"):
    return {
        "job_id": "00000000-0000-0000-0000-000000000002",
        "source_run_id": "r",
        "research_run_id": "rr",
        "candidate_id": "team-1",
        "target_lane": "LLP_TEAM_BETTING_ENGINE",
        "target_route": "/score-team-event-request",
        "request_id": "rr:scout:team-1",
        "request_payload": {
            "research_run_id": "rr",
            "objective_lane": "OUTRIGHT_WIN_PROBABILITY",
            "sport": sport,
            "league": league,
            "event_key": f"{sport}:1",
            "event_state": "PREGAME",
            "event_date": "2026-10-03",
            "timezone": "America/Chicago",
            "price_required_for_objective": False,
            "event_start_time_utc": "2026-10-03T20:00:00Z",
            "home_team": "Home",
            "away_team": "Away",
        },
        "attempt_count": 1,
    }


def test_prop_worker_finishes_one_row_without_touching_siblings():
    db = _DB()

    def prop_score(batch, x_wow_model_identity=None):
        assert len(batch.rows) == 1
        return {
            "rows": [{
                "row_key": batch.rows[0].row_key,
                "terminal_status": "COMPLETED",
                "probability_publishable": True,
                "rank_eligible": True,
                "card_admission_eligible": True,
                "can_execute": False,
            }],
            "can_execute": False,
        }

    result = process_claimed_job(
        db,
        _prop_job(),
        worker_id="worker-1",
        prop_score_fn=prop_score,
        team_score_fn=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("wrong lane")),
    )
    assert result["current_state"] == "V17_QUALIFIED"
    assert [name for name, _ in db.calls] == ["wow_finish_scout_handoff_job"]


def test_identity_contract_failure_dead_letters_only_that_team_row():
    db = _DB()

    def team_score(batch, x_wow_model_identity=None):
        raise HTTPException(
            status_code=422,
            detail={"code": "TEAM_EVENT_IDENTITY_INCOMPLETE"},
        )

    result = process_claimed_job(
        db,
        _team_job(),
        worker_id="worker-1",
        prop_score_fn=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("wrong lane")),
        team_score_fn=team_score,
    )
    assert result["current_state"] == "HANDOFF_BLOCKED"
    name, params = db.calls[-1]
    assert name == "wow_block_scout_handoff_job"
    assert params["p_error_code"] == "TEAM_EVENT_IDENTITY_INCOMPLETE"


def test_non_repeat_safe_team_runtime_failure_is_not_blindly_retried():
    db = _DB()

    def team_score(batch, x_wow_model_identity=None):
        raise RuntimeError("boom")

    result = process_claimed_job(
        db,
        _team_job("NFL", "NFL"),
        worker_id="worker-1",
        prop_score_fn=lambda *args, **kwargs: None,
        team_score_fn=team_score,
    )
    assert result["current_state"] == "HANDOFF_BLOCKED"
    name, params = db.calls[-1]
    assert name == "wow_block_scout_handoff_job"
    assert params["p_error_code"] == "SCOUT_HANDOFF_AMBIGUOUS_RETRY_PROHIBITED"
    assert params["p_error_detail"]["original_error_code"] == "SPECIALIST_RUNTIME_EXCEPTION"


def test_repeat_safe_prop_transient_failure_requeues_with_bounded_retry():
    db = _DB()

    def prop_score(batch, x_wow_model_identity=None):
        raise HTTPException(status_code=503, detail={"code": "PROVIDER_UNAVAILABLE"})

    result = process_claimed_job(
        db,
        _prop_job(),
        worker_id="worker-1",
        prop_score_fn=prop_score,
        team_score_fn=lambda *args, **kwargs: None,
    )
    assert result["current_state"] == "SPECIALIST_HANDOFF_QUEUED"
    name, params = db.calls[-1]
    assert name == "wow_retry_scout_handoff_job"
    assert params["p_error_code"] == "PROVIDER_UNAVAILABLE"
    assert params["p_delay_seconds"] == 15


def test_existing_research_red_team_quarantine_blocks_before_specialist_queue():
    handoff = _handoff()
    candidate = handoff["model_handoff"]["prop_candidates"][0]
    candidate["contradictory_evidence"] = ["material contradiction"]
    plan = build_handoff_plan(handoff)
    prop = next(row for row in plan.candidates if row.target_lane == "WOW_PROP_LANE")
    assert prop.red_team_status == "HANDOFF_BLOCKED"
    assert prop.blocked_code == "SCOUT_RED_TEAM_QUARANTINED"
    assert prop.blocked_detail["research_status"] == "QUARANTINED"
    assert prop.can_execute is False


def test_worker_reclaims_process_memory_after_each_claimed_job(monkeypatch):
    stop = asyncio.Event()
    calls = []

    monkeypatch.setattr(queue, "_claim", lambda db, worker_id: {"job_id": "job-1"})

    def fake_process(db, job, **kwargs):
        calls.append(("process", job["job_id"]))
        stop.set()
        return {"current_state": "MODEL_EVALUATED", "can_execute": False}

    monkeypatch.setattr(queue, "process_claimed_job", fake_process)
    monkeypatch.setattr(queue, "_release_process_memory", lambda: calls.append(("trim", "job-1")))

    asyncio.run(queue.worker_loop(
        db_client_fn=lambda: object(),
        prop_score_fn=lambda *args, **kwargs: {},
        team_score_fn=lambda *args, **kwargs: {},
        worker_id="worker-memory-test",
        stop_event=stop,
    ))

    assert calls == [("process", "job-1"), ("trim", "job-1")]


def test_worker_reclaims_process_memory_when_specialist_job_raises(monkeypatch):
    stop = asyncio.Event()
    calls = []

    monkeypatch.setattr(queue, "_claim", lambda db, worker_id: {"job_id": "job-err"})

    def fake_process(db, job, **kwargs):
        calls.append(("process", job["job_id"]))
        stop.set()
        raise RuntimeError("boom")

    monkeypatch.setattr(queue, "process_claimed_job", fake_process)
    monkeypatch.setattr(queue, "_release_process_memory", lambda: calls.append(("trim", "job-err")))

    asyncio.run(queue.worker_loop(
        db_client_fn=lambda: object(),
        prop_score_fn=lambda *args, **kwargs: {},
        team_score_fn=lambda *args, **kwargs: {},
        worker_id="worker-memory-test",
        stop_event=stop,
    ))

    assert calls == [("process", "job-err"), ("trim", "job-err")]


def test_worker_memory_pressure_defers_before_durable_claim(monkeypatch):
    stop = asyncio.Event()

    def pressure(_operation):
        stop.set()
        raise queue.memory_admission.HeavyJobDeferred(
            code="MEMORY_PRESSURE",
            operation="SCOUT_HANDOFF",
            detail={"reason": "MEMORY_PRESSURE", "retry_after_seconds": 1},
        )

    monkeypatch.setattr(queue.memory_admission, "try_acquire_heavy_job", pressure)
    monkeypatch.setattr(queue.memory_admission, "pressure_retry_seconds", lambda: 1.0)
    monkeypatch.setattr(
        queue,
        "_claim",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("memory-pressured Scout worker claimed durable work")
        ),
    )

    asyncio.run(queue.worker_loop(
        db_client_fn=lambda: object(),
        prop_score_fn=lambda *args, **kwargs: {},
        team_score_fn=lambda *args, **kwargs: {},
        worker_id="worker-pressure-test",
        stop_event=stop,
    ))

    assert stop.is_set()


def test_read_run_summary_derives_worker_proof_from_claimed_jobs_when_transition_events_are_missing():
    def specialist_receipt(status, code):
        return {
            "can_execute": False,
            "result": {
                "can_execute": False,
                "rows": [{
                    "terminal_status": status,
                    "code": code,
                    "probability_publishable": status == "COMPLETED",
                    "rank_eligible": status == "COMPLETED",
                    "card_admission_eligible": status == "COMPLETED",
                    "can_execute": False,
                }],
            },
        }

    jobs = [
        {
            "job_id": "job-evaluated",
            "source_run_id": "run-1",
            "research_run_id": "research-1",
            "candidate_id": "shared-candidate",
            "target_lane": "LLP_TEAM_BETTING_ENGINE",
            "research_priority": "HIGH",
            "current_state": "MODEL_EVALUATED",
            "terminal": True,
            "attempt_count": 1,
            "specialist_receipt": specialist_receipt("HELD", "MODEL_UNAVAILABLE"),
            "last_error_code": None,
            "created_at": "2026-11-07T16:00:00Z",
        },
        {
            "job_id": "job-qualified",
            "source_run_id": "run-1",
            "research_run_id": "research-1",
            "candidate_id": "shared-candidate",
            "target_lane": "WOW_PROP_LANE",
            "research_priority": "HIGH",
            "current_state": "V17_QUALIFIED",
            "terminal": True,
            "attempt_count": 1,
            "specialist_receipt": specialist_receipt("COMPLETED", "V17_QUALIFIED"),
            "last_error_code": None,
            "created_at": "2026-11-07T16:00:01Z",
        },
        {
            "job_id": "job-retried",
            "source_run_id": "run-1",
            "research_run_id": "research-1",
            "candidate_id": "retry-candidate",
            "target_lane": "LLP_TEAM_BETTING_ENGINE",
            "research_priority": "LOW",
            "current_state": "SPECIALIST_HANDOFF_QUEUED",
            "terminal": False,
            "attempt_count": 1,
            "specialist_receipt": None,
            "last_error_code": "PROVIDER_UNAVAILABLE",
            "created_at": "2026-11-07T16:00:02Z",
        },
        {
            "job_id": "job-unclaimed",
            "source_run_id": "run-1",
            "research_run_id": "research-1",
            "candidate_id": "unclaimed-candidate",
            "target_lane": "LLP_TEAM_BETTING_ENGINE",
            "research_priority": "LOW",
            "current_state": "MODEL_EVALUATED",
            "terminal": True,
            "attempt_count": 0,
            "specialist_receipt": specialist_receipt("HELD", "MODEL_UNAVAILABLE"),
            "last_error_code": None,
            "created_at": "2026-11-07T16:00:03Z",
        },
    ]
    events = [
        {
            "job_id": "job-evaluated",
            "candidate_id": "shared-candidate",
            "target_lane": "LLP_TEAM_BETTING_ENGINE",
            "state": "SPECIALIST_PROCESSING",
            "code": "SPECIALIST_WORKER_CLAIMED",
            "can_execute": False,
            "created_at": "2026-11-07T16:00:00Z",
        },
        {
            "job_id": "job-qualified",
            "candidate_id": "shared-candidate",
            "target_lane": "WOW_PROP_LANE",
            "state": "SPECIALIST_HANDOFF_QUEUED",
            "code": "SPECIALIST_HANDOFF_ENQUEUED",
            "can_execute": False,
            "created_at": "2026-11-07T15:59:00Z",
        },
        {
            "job_id": "job-retried",
            "candidate_id": "retry-candidate",
            "target_lane": "LLP_TEAM_BETTING_ENGINE",
            "state": "SPECIALIST_HANDOFF_QUEUED",
            "code": "PROVIDER_UNAVAILABLE",
            "can_execute": False,
            "created_at": "2026-11-07T16:00:02Z",
        },
    ]

    class _ReadQuery:
        def __init__(self, rows):
            self.rows = rows

        def select(self, *_args):
            return self

        def eq(self, *_args):
            return self

        def order(self, *_args):
            return self

        def range(self, *_args):
            return self

        def execute(self):
            return _Response(self.rows)

    class _ReadDB:
        def table(self, name):
            if name == "wow_scout_handoff_jobs":
                return _ReadQuery(jobs)
            if name == "wow_scout_handoff_state_events":
                return _ReadQuery(events)
            raise AssertionError(f"unexpected table: {name}")

    summary = queue.read_run_summary(_ReadDB(), "run-1", include_receipts=True)

    assert summary["specialist_processing_seen"] == 3
    assert summary["model_evaluated"] == 2
    assert summary["v17_qualified"] == 1
    assert summary["worker_processing_event_gap_jobs"] == 2
    assert summary["model_evaluated_event_gap_jobs"] == 2
    assert summary["state_event_coverage_pass"] is False


def test_read_run_summary_paginates_state_events_past_postgrest_default_row_limit():
    outcome = {
        "terminal_status": "HELD",
        "code": "MODEL_UNAVAILABLE",
        "can_execute": False,
    }
    jobs = [{
        "job_id": "job-after-first-page",
        "source_run_id": "run-paged",
        "research_run_id": "research-paged",
        "candidate_id": "candidate-after-first-page",
        "target_lane": "LLP_TEAM_BETTING_ENGINE",
        "research_priority": "HIGH",
        "current_state": "MODEL_EVALUATED",
        "terminal": True,
        "attempt_count": 1,
        "specialist_receipt": {
            "can_execute": False,
            "result": {"can_execute": False, "rows": [outcome]},
        },
        "last_error_code": None,
        "created_at": "2026-11-07T16:00:00Z",
    }]
    events = [{
        "job_id": f"filler-{index}",
        "candidate_id": f"filler-{index}",
        "target_lane": "LLP_TEAM_BETTING_ENGINE",
        "state": "DISCOVERED",
        "code": "DISCOVERED",
        "can_execute": False,
        "created_at": f"2026-11-07T15:{index // 60:02d}:{index % 60:02d}Z",
    } for index in range(1000)]
    events.extend([
        {
            "job_id": "job-after-first-page",
            "candidate_id": "candidate-after-first-page",
            "target_lane": "LLP_TEAM_BETTING_ENGINE",
            "state": "SPECIALIST_PROCESSING",
            "code": "SPECIALIST_WORKER_CLAIMED",
            "can_execute": False,
            "created_at": "2026-11-07T16:00:01Z",
        },
        {
            "job_id": "job-after-first-page",
            "candidate_id": "candidate-after-first-page",
            "target_lane": "LLP_TEAM_BETTING_ENGINE",
            "state": "MODEL_EVALUATED",
            "code": "SPECIALIST_MODEL_EVALUATED",
            "can_execute": False,
            "created_at": "2026-11-07T16:00:02Z",
        },
    ])

    class _PagedReadQuery:
        def __init__(self, rows):
            self.rows = rows
            self.start = 0
            self.end = 999
            self.range_calls = []

        def select(self, *_args):
            return self

        def eq(self, *_args):
            return self

        def order(self, *_args):
            return self

        def range(self, start, end):
            self.start = start
            self.end = end
            self.range_calls.append((start, end))
            return self

        def execute(self):
            return _Response(self.rows[self.start:self.end + 1])

    class _PagedReadDB:
        def __init__(self):
            self.queries = {}

        def table(self, name):
            rows = jobs if name == "wow_scout_handoff_jobs" else events
            query = _PagedReadQuery(rows)
            self.queries[name] = query
            return query

    db = _PagedReadDB()
    summary = queue.read_run_summary(db, "run-paged", include_receipts=True)

    assert summary["specialist_processing_seen"] == 1
    assert summary["model_evaluated"] == 1
    assert summary["worker_processing_event_gap_jobs"] == 0
    assert summary["model_evaluated_event_gap_jobs"] == 0
    assert summary["state_event_coverage_pass"] is True
    assert db.queries["wow_scout_handoff_state_events"].range_calls == [
        (0, 999),
        (1000, 1999),
    ]

    # Frequent status polls must not fetch every event page on every 2-second
    # poll. The detailed terminal read above is the one complete event scan.
    poll_db = _PagedReadDB()
    poll_summary = queue.read_run_summary(poll_db, "run-paged")
    assert poll_summary["specialist_processing_seen"] == 1
    assert poll_summary["model_evaluated"] == 1
    assert poll_summary["state_event_read_complete"] is False
    assert poll_summary["state_event_coverage_pass"] is None
    assert poll_summary["worker_processing_event_gap_jobs"] is None
    assert poll_db.queries["wow_scout_handoff_state_events"].range_calls == [(0, 999)]


def test_state_event_pagination_carries_event_id_for_page_boundary_integrity():
    """Offset pagination is not a snapshot.

    PostgREST range pagination advances a window, so an insert during the scan
    shifts rows across the boundary. The projection must therefore carry
    ``event_id`` -- the ordering tiebreak key -- or the caller cannot detect a
    duplicated or skipped event after the fact.
    """
    outcome = {"terminal_status": "HELD", "code": "MODEL_UNAVAILABLE", "can_execute": False}
    job_rows = [{
        "job_id": "job-boundary",
        "source_run_id": "run-boundary",
        "research_run_id": "research-boundary",
        "candidate_id": "candidate-boundary",
        "target_lane": "LLP_TEAM_BETTING_ENGINE",
        "research_priority": "HIGH",
        "current_state": "MODEL_EVALUATED",
        "terminal": True,
        "attempt_count": 1,
        "specialist_receipt": {
            "can_execute": False,
            "result": {"can_execute": False, "rows": [outcome]},
        },
        "last_error_code": None,
        "created_at": "2026-11-07T16:00:00Z",
    }]
    event_rows = [
        {
            "event_id": "event-0001",
            "job_id": "job-boundary",
            "candidate_id": "candidate-boundary",
            "target_lane": "LLP_TEAM_BETTING_ENGINE",
            "state": "SPECIALIST_PROCESSING",
            "code": "SPECIALIST_WORKER_CLAIMED",
            "can_execute": False,
            "created_at": "2026-11-07T16:00:01Z",
        },
        {
            "event_id": "event-0002",
            "job_id": "job-boundary",
            "candidate_id": "candidate-boundary",
            "target_lane": "LLP_TEAM_BETTING_ENGINE",
            "state": "MODEL_EVALUATED",
            "code": "SPECIALIST_MODEL_EVALUATED",
            "can_execute": False,
            "created_at": "2026-11-07T16:00:02Z",
        },
    ]

    class _Response:
        def __init__(self, data):
            self.data = data

        def execute(self):
            return self

    class _RecordingQuery:
        def __init__(self, rows):
            self.rows = rows
            self.select_columns = []
            self.range_calls = []

        def select(self, columns):
            self.select_columns.append(columns)
            return self

        def eq(self, *_args):
            return self

        def order(self, *_args):
            return self

        def range(self, start, end):
            self.range_calls.append((start, end))
            return _Response(self.rows[start:end + 1])

    class _RecordingDB:
        def __init__(self):
            self.queries = {}

        def table(self, name):
            rows = job_rows if name == "wow_scout_handoff_jobs" else event_rows
            query = _RecordingQuery(rows)
            self.queries[name] = query
            return query

    db = _RecordingDB()
    summary = queue.read_run_summary(db, "run-boundary", include_receipts=True)

    assert summary["state_event_read_complete"] is True
    projected = db.queries["wow_scout_handoff_state_events"].select_columns[0].split(",")
    assert "event_id" in projected, f"state-event projection must carry the tiebreak key, got {projected}"

    # A concurrent insert shifts the offset window, so event-0999 reappears on page 2.
    pages = [
        [{"event_id": f"event-{index:04d}"} for index in range(1000)],
        [{"event_id": "event-0999"}, {"event_id": "event-1000"}],
    ]

    class _BoundaryQuery:
        def __init__(self):
            self.calls = []

        def range(self, start, end):
            self.calls.append((start, end))
            return _Response(pages[len(self.calls) - 1])

    boundary_query = _BoundaryQuery()
    paged = queue._paginate_query(boundary_query, page_size=1000)

    assert boundary_query.calls == [(0, 999), (1000, 1999)]
    event_ids = [row["event_id"] for row in paged]
    assert len(event_ids) > len(set(event_ids)), "a shifted page boundary must remain detectable to the caller"
