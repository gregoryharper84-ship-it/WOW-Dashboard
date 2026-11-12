"""24x7 controller intake lane must coalesce, never flood, its pending queue.

Evidence (workflow wow-engineering-24x7, 2026-11-12): the controller group used
``queue: max`` while nearly every run was a ``workflow_run`` completion event.
Each run re-reads the complete GitHub snapshot, so an older pending run carries
no information a newer pending run lacks. With ``queue: max`` a burst of
completions piled up ~12 waiting 4-9 minute runs, delaying real incidents.

This module changes nothing about the single-writer mutex: dispatch keeps its
own ``queue: max`` group so no writer arrival is ever dropped.
"""
from pathlib import Path

import yaml

from test_v17_implementation_writer_pending_queue import arrive

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/wow-engineering-24x7.yml"
WRITER_GROUP = "wow-v17-engineering-implementation-single-writer"


def load():
    return yaml.safe_load(WORKFLOW.read_text())


def controller_block():
    return load()["concurrency"]


def test_controller_intake_lane_coalesces_pending_runs():
    block = controller_block()
    assert block["cancel-in-progress"] is False  # never cancel the running run
    assert block.get("queue", "single") == "single"  # newer pending replaces older


def test_burst_of_completion_events_leaves_at_most_one_pending_run():
    block = controller_block()
    state = {"running": None, "pending": []}
    cancelled_total = []
    for run in range(1, 14):  # 13 back-to-back workflow_run completions
        state, cancelled = arrive(state, run, queue=block.get("queue", "single"),
                                  cancel_in_progress=block["cancel-in-progress"])
        cancelled_total += cancelled
    assert state["running"] == 1
    assert state["pending"] == [13]  # only the newest snapshot read survives
    assert 13 not in cancelled_total and 1 not in cancelled_total


def test_single_writer_mutex_is_unchanged_and_still_never_drops_arrivals():
    jobs = load()["jobs"]
    writer = jobs["dispatch"]["concurrency"]
    assert writer == {"group": WRITER_GROUP, "cancel-in-progress": False, "queue": "max"}


def test_pr_test_jobs_remain_in_a_separate_group_from_controller_events():
    group = controller_block()["group"]
    assert "github.event_name == 'pull_request'" in group
    assert "format('pr-{0}'" in group


def test_controller_decision_is_a_stateless_snapshot_not_the_event_payload():
    select = load()["jobs"]["select"]
    text = yaml.safe_dump(select)
    for source in ("issues?state=open", "pulls?state=open", "workflow_runs"):
        assert source in text
    assert "github.event.issue" not in text and "github.event.pull_request" not in text
