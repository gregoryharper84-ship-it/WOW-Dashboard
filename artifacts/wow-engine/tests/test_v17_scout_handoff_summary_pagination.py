"""read_run_summary/read_run_status must page past PostgREST's 1,000-row response cap.

Regression for the nightly multiscout failure SCOUT_TERMINAL_ACCEPTANCE_WORKER_PATH_NOT_PROVEN
(320 jobs x 6 state events = 1,920 events; an unpaginated read kept only the first 1,000, all
early-stage, so SPECIALIST_PROCESSING / MODEL_EVALUATED events were invisible).
"""
from v17 import scout_handoff_queue as queue
from v17 import scout_handoff_terminal_acceptance as acceptance

POSTGREST_CAP = 1000
STATES = ["DISCOVERED", "RESEARCH_INTEREST", "RED_TEAM_PASSED", "SPECIALIST_HANDOFF_QUEUED",
          "SPECIALIST_PROCESSING", "MODEL_EVALUATED"]


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows):
        self._rows, self._eq, self._order, self._range = rows, {}, [], None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._eq[col] = val
        return self

    def order(self, col, **_k):
        self._order.append(col)
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def execute(self):
        rows = [r for r in self._rows if all(r.get(k) == v for k, v in self._eq.items())]
        rows.sort(key=lambda r: tuple(str(r.get(c)) for c in (self._order or [])))
        start, end = self._range if self._range else (0, POSTGREST_CAP - 1)
        end = min(end, start + POSTGREST_CAP - 1)  # server-side max-rows cap
        return _Result(rows[start:end + 1])


class _FakeDB:
    def __init__(self, jobs, events):
        self._t = {"wow_scout_handoff_jobs": jobs, "wow_scout_handoff_state_events": events}

    def table(self, name):
        return _Query(self._t[name])


def _db(n_jobs=320, run="run-1"):
    jobs, events, eid = [], [], 0
    for i in range(n_jobs):
        jid, cid = f"job-{i:05d}", f"cand-{i}"
        receipt = {"result": {"outcomes": [{"terminal_status": "HELD", "code": "MODEL_UNAVAILABLE"}]}}
        jobs.append({"job_id": jid, "source_run_id": run, "research_run_id": "rr", "candidate_id": cid,
                     "target_lane": "LLP_TEAM_BETTING_ENGINE", "research_priority": "HIGH",
                     "current_state": "MODEL_EVALUATED", "terminal": True, "can_execute": False,
                     "created_at": "2026-11-11T17:36:55+00:00", "specialist_receipt": receipt})
        for k, state in enumerate(STATES):
            eid += 1
            # Early states share one timestamp (as in production), later states are later.
            ts = "2026-11-11T17:36:55+00:00" if k < 4 else f"2026-11-11T17:37:{10 + k:02d}+00:00"
            events.append({"event_id": eid, "job_id": jid, "source_run_id": run, "candidate_id": cid,
                           "target_lane": "LLP_TEAM_BETTING_ENGINE", "state": state, "code": state,
                           "can_execute": False, "created_at": ts})
    return _FakeDB(jobs, events)


def test_summary_sees_every_event_beyond_the_1000_row_cap():
    s = queue.read_run_summary(_db(320), "run-1")
    assert s["candidate_jobs"] == 320
    assert s["specialist_processing_seen"] == 320
    assert s["model_evaluated"] == 320
    assert s["discovered"] == 320


def test_summary_sees_every_job_beyond_the_1000_row_cap():
    s = queue.read_run_summary(_db(1100), "run-1")
    assert s["candidate_jobs"] == s["rows_in"] == 1100
    assert s["specialist_processing_seen"] == 1100


def test_status_counts_every_job_beyond_the_cap():
    s = queue.read_run_status(_db(1100), "run-1")
    assert s["candidate_jobs"] == 1100


def test_acceptance_no_longer_reports_worker_path_not_proven():
    summary = queue.read_run_summary(_db(320), "run-1")
    summary["jobs"], summary["state_events"] = [], []
    blockers = acceptance.validate_terminal_summary(summary)["blockers"]
    assert "SCOUT_TERMINAL_ACCEPTANCE_WORKER_PATH_NOT_PROVEN" not in blockers
