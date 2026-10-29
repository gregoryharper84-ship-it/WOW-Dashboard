"""Native GitHub Actions queue semantics for the shared implementation writer.

Domain-scoped dispatcher groups stay separate. The downstream writer lock stays
one global mutex. GitHub's default pending queue is ``single``: a newer waiter
cancels the older waiter. ``queue: max`` keeps up to 100 pending runs and
cancels only an arrival that does not fit. ``queue: max`` with
``cancel-in-progress: true`` is a workflow validation error.

Documented cap and cancellation rules:
https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#concurrency
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
DISPATCHER = WORKFLOWS / "wow-v17-engineering-provider-dispatcher.yml"
MANIFEST = ROOT / "artifacts" / "wow-engine" / "v17" / "engineering_dispatch_manifest.json"
WRITER_GROUP = "wow-v17-engineering-implementation-single-writer"
NATIVE_PENDING_CAP = 100
WRITER_WORKFLOWS = (
    "wow-v17-engineering-provider-dispatcher.yml",
    "wow-v17-chatgpt-engineering-worker.yml",
    "wow-v17-claude-engineering-worker.yml",
    "wow-v17-gemini-engineering-worker.yml",
    "wow-v17-engineering-auto-repair.yml",
    "wow-v17-remote-writer-finalize.yml",
)
# Live 2026-10-27 manual admissions. NBA and NCAAF were cancelled before any
# job because the dispatcher group ignored these distinct leases.
OBSERVED_SPORT_LEASES = (
    "P0_LLP_RESTORE",
    "P0_NBA_RESTORE",
    "P0_NCAAF_RESTORE",
    "P0_NHL_RESTORE",
)
MANIFEST_SPORT_ROWS = {
    1841: "P0_LLP_RESTORE",
    1950: "P0_NBA_RESTORE",
    1951: "P0_NCAAF_RESTORE",
    1952: "P0_NHL_RESTORE",
}


class WorkflowValidationError(ValueError):
    pass


def truthy(value):
    return value not in (False, None, "", 0)


def tokenize(expr):
    pattern = re.compile(r"\s+|&&|\|\||==|!=|[()]|'[^']*'|[A-Za-z_][A-Za-z0-9_.]*")
    pos = 0
    tokens = []
    while pos < len(expr):
        match = pattern.match(expr, pos)
        if not match:
            raise ValueError(f"UNPARSED_EXPRESSION:{expr[pos:pos + 24]!r}")
        token = match.group(0)
        pos = match.end()
        if token.strip():
            tokens.append(token)
    return tokens


class _Expr:
    def __init__(self, tokens, env):
        self.tokens = tokens
        self.env = env
        self.index = 0

    def peek(self):
        if self.index >= len(self.tokens):
            return None
        return self.tokens[self.index]

    def pop(self):
        token = self.peek()
        if token is None:
            raise ValueError("TRUNCATED_EXPRESSION")
        self.index += 1
        return token

    def parse(self):
        value = self.disjunction()
        if self.peek() is not None:
            raise ValueError(f"TRAILING_EXPRESSION:{self.peek()}")
        return value

    def disjunction(self):
        left = self.conjunction()
        while self.peek() == "||":
            self.pop()
            right = self.conjunction()
            left = left if truthy(left) else right
        return left

    def conjunction(self):
        left = self.comparison()
        while self.peek() == "&&":
            self.pop()
            right = self.comparison()
            left = right if truthy(left) else left
        return left

    def comparison(self):
        left = self.atom()
        op = self.peek()
        if op not in ("==", "!="):
            return left
        self.pop()
        right = self.atom()
        return (left == right) if op == "==" else (left != right)

    def atom(self):
        token = self.pop()
        if token == "(":
            value = self.disjunction()
            if self.pop() != ")":
                raise ValueError("UNBALANCED_EXPRESSION")
            return value
        if token.startswith("'") and token.endswith("'"):
            return token[1:-1]
        if token not in self.env:
            raise ValueError(f"UNBOUND_EXPRESSION:{token}")
        return self.env[token]


def eval_github(expr, env):
    return _Expr(tokenize(expr), env).parse()


def dispatcher_expression():
    text = DISPATCHER.read_text()
    block = text.split("\nconcurrency:\n", 1)[1].split("\njobs:\n", 1)[0]
    return block.split("${{", 1)[1].split("}}", 1)[0]


def dispatcher_group(*, event, lease_group, force_provider="auto", workflow_run_name=""):
    return eval_github(dispatcher_expression(), {
        "github.event_name": event,
        "github.event.workflow_run.name": workflow_run_name,
        "inputs.force_provider": force_provider,
        "inputs.lease_group": lease_group,
    })


def arrive(state, run, *, queue, cancel_in_progress=False):
    """Apply one arrival while honoring documented Actions concurrency."""
    if queue == "max" and cancel_in_progress is True:
        raise WorkflowValidationError("QUEUE_MAX_WITH_CANCEL_IN_PROGRESS")
    if queue not in ("single", "max"):
        raise WorkflowValidationError("UNSUPPORTED_QUEUE")
    running = state["running"]
    pending = list(state["pending"])
    if running is None:
        if pending:
            raise ValueError("PENDING_WITHOUT_RUNNER")
        return {"running": run, "pending": []}, []
    if cancel_in_progress:
        cancelled = [running, *pending]
        return {"running": run, "pending": []}, cancelled
    if queue == "single":
        return {"running": running, "pending": [run]}, list(pending)
    if len(pending) >= NATIVE_PENDING_CAP:
        return {"running": running, "pending": pending}, [run]
    pending.append(run)
    return {"running": running, "pending": pending}, []


def load_workflow(name):
    return yaml.safe_load((WORKFLOWS / name).read_text())


def concurrency_blocks(node, found=None):
    found = [] if found is None else found
    if isinstance(node, dict):
        if "group" in node and "cancel-in-progress" in node:
            found.append(node)
        for value in node.values():
            concurrency_blocks(value, found)
    elif isinstance(node, list):
        for value in node:
            concurrency_blocks(value, found)
    return found


def writer_blocks():
    blocks = []
    for name in WRITER_WORKFLOWS:
        for block in concurrency_blocks(load_workflow(name)):
            if block.get("group") == WRITER_GROUP:
                blocks.append((name, block))
    return blocks


def test_shared_writer_uses_native_max_queue_and_keeps_one_mutex():
    blocks = writer_blocks()
    assert [name for name, _ in blocks] == list(WRITER_WORKFLOWS)
    for name, block in blocks:
        assert block == {
            "group": WRITER_GROUP,
            "cancel-in-progress": False,
            "queue": "max",
        }, name
    mentioned = sorted(
        path.name for path in WORKFLOWS.glob("*.yml")
        if WRITER_GROUP in path.read_text()
    )
    assert mentioned == sorted(WRITER_WORKFLOWS)


def test_dispatcher_planning_lanes_stay_outside_the_writer_mutex():
    workflow = load_workflow(DISPATCHER.name)
    planning = workflow["concurrency"]
    assert planning["cancel-in-progress"] is False
    assert "queue" not in planning
    assert WRITER_GROUP not in planning["group"]
    assert "inputs.lease_group" in planning["group"]
    groups = [
        dispatcher_group(event="workflow_dispatch", lease_group=lease, force_provider="auto")
        for lease in OBSERVED_SPORT_LEASES
    ]
    assert groups == list(OBSERVED_SPORT_LEASES)
    assert len(set(groups)) == 4
    assert "AUTO" not in groups
    assert WRITER_GROUP not in groups


def test_manifest_sport_rows_remain_independently_leased():
    document = json.loads(MANIFEST.read_text())
    assert document["can_execute"] is False
    assert document["terminal_authority"] == "V17_TERMINAL_REDUCER"
    rows = {row["issue_number"]: row for row in document["restoration"]}
    for issue, lease in MANIFEST_SPORT_ROWS.items():
        assert rows[issue]["lease_group"] == lease
        assert rows[issue]["can_execute"] is False
    planned = [
        dispatcher_group(
            event="workflow_dispatch",
            lease_group=rows[issue]["lease_group"],
            force_provider="auto",
        )
        for issue in (1841, 1950, 1951, 1952)
    ]
    assert planned == [
        "P0_LLP_RESTORE", "P0_NBA_RESTORE", "P0_NCAAF_RESTORE", "P0_NHL_RESTORE",
    ]
    assert len(set(planned)) == 4


@pytest.mark.parametrize(
    ("event", "lease", "provider", "upstream", "expected"),
    [
        ("workflow_dispatch", "P0_NBA_RESTORE", "auto", "", "P0_NBA_RESTORE"),
        ("workflow_dispatch", "GLOBAL", "auto", "", "AUTO"),
        ("workflow_dispatch", "", "gemini", "", "AUTO"),
        ("workflow_dispatch", "P0_NHL_RESTORE", "cursor", "", "CURSOR_SINGLE_AGENT"),
        ("schedule", "P0_NBA_RESTORE", "auto", "", "AUTO"),
        ("workflow_run", "P0_NBA_RESTORE", "auto", "wow-v17-chatgpt-engineering-worker", "GLOBAL"),
        ("workflow_run", "", "auto", "wow-v17-nightly-engineering-scan", "AUTO"),
    ],
)
def test_dispatcher_group_expression_matches_native_precedence(
    event, lease, provider, upstream, expected,
):
    assert dispatcher_group(
        event=event, lease_group=lease, force_provider=provider, workflow_run_name=upstream,
    ) == expected


def test_default_single_queue_cancels_earlier_sport_waiters():
    state = {"running": "holder", "pending": []}
    cancelled = []
    for lease in OBSERVED_SPORT_LEASES:
        state, dropped = arrive(state, lease, queue="single")
        cancelled.extend(dropped)
    assert state == {"running": "holder", "pending": ["P0_NHL_RESTORE"]}
    assert cancelled == ["P0_LLP_RESTORE", "P0_NBA_RESTORE", "P0_NCAAF_RESTORE"]


def test_queue_max_retains_four_sport_waiters_behind_one_writer():
    state = {"running": "holder", "pending": []}
    for lease in OBSERVED_SPORT_LEASES:
        state, dropped = arrive(state, lease, queue="max")
        assert dropped == []
    assert state["running"] == "holder"
    assert state["pending"] == list(OBSERVED_SPORT_LEASES)


def test_queue_max_cancels_only_the_arrival_past_the_native_cap():
    state = {"running": "holder", "pending": []}
    for index in range(NATIVE_PENDING_CAP):
        state, dropped = arrive(state, f"pending-{index}", queue="max")
        assert dropped == []
    state, dropped = arrive(state, "pending-overflow", queue="max")
    assert dropped == ["pending-overflow"]
    assert state["running"] == "holder"
    assert len(state["pending"]) == NATIVE_PENDING_CAP
    assert state["pending"][0] == "pending-0"
    assert state["pending"][-1] == f"pending-{NATIVE_PENDING_CAP - 1}"


def test_queue_max_cannot_cancel_the_running_writer():
    with pytest.raises(WorkflowValidationError, match="QUEUE_MAX_WITH_CANCEL_IN_PROGRESS"):
        arrive(
            {"running": "holder", "pending": ["P0_NBA_RESTORE"]},
            "P0_NHL_RESTORE",
            queue="max",
            cancel_in_progress=True,
        )


def test_missing_or_non_max_queue_is_rejected_for_the_shared_writer():
    def require(block):
        if block.get("group") != WRITER_GROUP:
            raise AssertionError("WRITER_GROUP_SPLIT")
        if block.get("cancel-in-progress") is not False:
            raise AssertionError("WRITER_CANCEL_IN_PROGRESS")
        if block.get("queue") != "max":
            raise AssertionError("WRITER_PENDING_QUEUE_NOT_MAX")

    for _, block in writer_blocks():
        require(block)
    for bad in (
        {"group": WRITER_GROUP, "cancel-in-progress": False},
        {"group": WRITER_GROUP, "cancel-in-progress": False, "queue": "single"},
        {"group": WRITER_GROUP, "cancel-in-progress": False, "queue": 1},
        {"group": WRITER_GROUP + "-nba", "cancel-in-progress": False, "queue": "max"},
        {"group": WRITER_GROUP, "cancel-in-progress": True, "queue": "max"},
    ):
        with pytest.raises(AssertionError):
            require(bad)


def test_cancel_in_progress_still_replaces_the_running_writer_when_explicitly_enabled():
    state, cancelled = arrive(
        {"running": "holder", "pending": ["P0_NBA_RESTORE"]},
        "replacement",
        queue="single",
        cancel_in_progress=True,
    )
    assert state == {"running": "replacement", "pending": []}
    assert cancelled == ["holder", "P0_NBA_RESTORE"]


def test_provider_worker_checkout_is_pinned_before_credential_mint() -> None:
    """A queued worker must never run a newer main than its admitted SHA."""
    for filename in (
        "wow-v17-chatgpt-engineering-worker.yml",
        "wow-v17-claude-engineering-worker.yml",
    ):
        text = (WORKFLOWS / filename).read_text(encoding="utf-8")
        checkout = "Checkout exact dispatch SHA (no persisted credential)"
        stale_guard = "Reject stale queued worker before credential mint"
        credential = "Mint wow-engineering App token"
        assert text.count(checkout) == 1
        assert text.count(stale_guard) == 1
        assert text.index(checkout) < text.index(stale_guard) < text.index(credential)
        pre_credentials = text[text.index(checkout):text.index(credential)]
        assert "ref: ${{ github.sha }}" in pre_credentials
        assert "persist-credentials: false" in pre_credentials
        assert 'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"' in pre_credentials
        assert 'current_main=$(gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/main"' in pre_credentials
        assert 'test "$current_main" = "$GITHUB_SHA"' in pre_credentials
        assert "PROVIDER_WORKER_PROTECTED_MAIN_ADVANCED" in pre_credentials
        assert "PROVIDER_WORKER_CHECKOUT_SHA_DRIFT" in pre_credentials
