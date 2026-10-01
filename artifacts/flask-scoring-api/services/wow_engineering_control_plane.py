"""LangGraph control plane for the WOW V17 engineering repair lifecycle.

Engineering orchestration only: this module never originates, substitutes,
blends, overrides, publishes, or executes a sporting probability or wager.
"""
from __future__ import annotations

import operator
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Any, Callable, Literal, Mapping, TypedDict

from langgraph.graph import END, START, StateGraph


CUSTOM_GPT_IDENTITY = "WOW_BETTING_ENGINE"
RUNTIME_GENERATION = "V17_ACTIVE"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
CAN_EXECUTE = False
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = True
PROBABILITY_AUTHORITY = "NONE"
MAX_REPAIR_ATTEMPTS = 3

TerminalStatus = Literal[
    "FIXED_AND_VERIFIED",
    "PR_CREATED",
    "EXPERIMENT_CREATED",
    "DUPLICATE",
    "NOT_REPRODUCIBLE",
    "BLOCKED_WITH_EXACT_REASON",
    "DEFERRED_WITH_JUSTIFICATION",
]

TERMINAL_STATUSES: tuple[str, ...] = (
    "FIXED_AND_VERIFIED",
    "PR_CREATED",
    "EXPERIMENT_CREATED",
    "DUPLICATE",
    "NOT_REPRODUCIBLE",
    "BLOCKED_WITH_EXACT_REASON",
    "DEFERRED_WITH_JUSTIFICATION",
)

TRIAGE_TERMINALS = frozenset(
    {
        "DUPLICATE",
        "NOT_REPRODUCIBLE",
        "BLOCKED_WITH_EXACT_REASON",
        "DEFERRED_WITH_JUSTIFICATION",
    }
)

FORBIDDEN_PROBABILITY_FIELDS = frozenset(
    {
        "model_probability",
        "sporting_probability",
        "projected_probability",
        "calibrated_probability",
        "implied_probability",
        "no_vig_probability",
        "probability_override",
        "probability_blend",
        "pick_probability",
        "wager",
        "market_order",
    }
)

NEW_ISSUE_INTERNAL_FIELDS = frozenset(
    {
        "workflow_stage",
        "terminal_status",
        "review_status",
        "governance_status",
        "governance_violations",
        "deployment_status",
        "qa_status",
        "production_verification",
        "attempt_count",
        "max_repair_attempts",
        "blocker",
        "smallest_remaining_action",
        "trace_events",
    }
)

ADAPTER_FORBIDDEN_CONTROL_FIELDS = frozenset(
    {
        "custom_gpt_identity",
        "runtime_generation",
        "terminal_authority",
        "can_execute",
        "dry_run_only_no_live_trading_no_market_orders",
        "probability_authority",
        "promotion_authorized",
        "max_repair_attempts",
        "attempt_count",
        "workflow_stage",
        "terminal_status",
        "trace_events",
        "governance_status",
        "governance_violations",
    }
)


class TraceEvent(TypedDict):
    stage: str
    status: str
    issue_id: str
    timestamp_utc: str
    attempt_count: int
    detail: str


class EngineeringState(TypedDict, total=False):
    issue_id: str
    severity: Literal["P0", "P1", "P2", "P3"]
    change_class: Literal["A", "B", "C"]
    component: str
    sport: str | None
    custom_gpt_identity: str
    runtime_generation: str
    terminal_authority: str
    can_execute: bool
    dry_run_only_no_live_trading_no_market_orders: bool
    probability_authority: str
    workflow_stage: str
    reproduction_evidence: list[str]
    diagnostics: dict[str, Any]
    data_audit: dict[str, Any]
    root_cause: str | None
    changed_files: list[str]
    tests_run: list[str]
    tests_passed: bool
    regression_results: dict[str, Any]
    review_status: str
    governance_status: str
    governance_violations: list[str]
    deployment_status: str
    qa_status: str
    production_verification: str
    promotion_authorized: bool
    triage_disposition: TerminalStatus | None
    attempt_count: int
    max_repair_attempts: int
    blocker: str | None
    smallest_remaining_action: str | None
    terminal_status: TerminalStatus | None
    trace_events: Annotated[list[TraceEvent], operator.add]


NodeHandler = Callable[[EngineeringState], Mapping[str, Any]]
TraceSink = Callable[[TraceEvent], None]


@dataclass(frozen=True)
class EngineeringAdapters:
    """External role/tool adapters used by the deterministic control plane."""

    intake: NodeHandler
    triage: NodeHandler
    diagnostics: NodeHandler
    data_audit: NodeHandler
    root_cause: NodeHandler
    engineer: NodeHandler
    sandbox_test: NodeHandler
    independent_review: NodeHandler
    release: NodeHandler
    production_qa: NodeHandler
    trace_sink: TraceSink | None = None


def _trace(state: EngineeringState, stage: str, status: str, detail: str = "") -> TraceEvent:
    return {
        "stage": stage,
        "status": status,
        "issue_id": str(state.get("issue_id", "UNKNOWN")),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "attempt_count": int(state.get("attempt_count", 0)),
        "detail": detail,
    }


def _emit(adapters: EngineeringAdapters, event: TraceEvent) -> None:
    if adapters.trace_sink is not None:
        adapters.trace_sink(event)


def _find_forbidden_probability_paths(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}"
            if key in FORBIDDEN_PROBABILITY_FIELDS:
                found.append(child_path)
            found.extend(_find_forbidden_probability_paths(nested, child_path))
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            found.extend(_find_forbidden_probability_paths(nested, f"{path}[{index}]"))
    return found


def _assert_no_probability_payload(payload: Mapping[str, Any]) -> None:
    forbidden_paths = sorted(set(_find_forbidden_probability_paths(payload)))
    if forbidden_paths:
        raise ValueError(
            "ENGINEERING_CONTROL_PLANE_PROBABILITY_BOUNDARY_VIOLATION: "
            + ",".join(forbidden_paths)
        )


def _assert_new_issue_boundary(state: Mapping[str, Any]) -> None:
    forbidden = sorted(NEW_ISSUE_INTERNAL_FIELDS.intersection(state.keys()))
    if forbidden:
        raise ValueError(
            "ENGINEERING_CONTROL_PLANE_INTERNAL_STATE_INJECTION_REJECTED: "
            + ",".join(forbidden)
        )


def _assert_adapter_control_boundary(raw: Mapping[str, Any]) -> None:
    forbidden = sorted(ADAPTER_FORBIDDEN_CONTROL_FIELDS.intersection(raw.keys()))
    if forbidden:
        raise ValueError(
            "ENGINEERING_CONTROL_PLANE_ADAPTER_CONTROL_FIELD_REJECTED: "
            + ",".join(forbidden)
        )


def _handler_update(
    *,
    state: EngineeringState,
    stage: str,
    handler: NodeHandler,
    adapters: EngineeringAdapters,
    set_stage: bool = True,
) -> dict[str, Any]:
    _assert_no_probability_payload(state)
    raw = dict(handler(dict(state)))
    _assert_no_probability_payload(raw)
    _assert_adapter_control_boundary(raw)
    event = _trace(state, stage, "COMPLETED")
    _emit(adapters, event)
    if set_stage:
        raw["workflow_stage"] = stage
    raw["trace_events"] = [event]
    return raw


def _assert_input_governance(state: Mapping[str, Any]) -> None:
    expected_if_supplied = {
        "custom_gpt_identity": CUSTOM_GPT_IDENTITY,
        "runtime_generation": RUNTIME_GENERATION,
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": CAN_EXECUTE,
        "dry_run_only_no_live_trading_no_market_orders": DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS,
        "probability_authority": PROBABILITY_AUTHORITY,
    }
    violations = [
        f"{key}={state[key]!r}; expected {wanted!r}"
        for key, wanted in expected_if_supplied.items()
        if key in state and state[key] != wanted
    ]
    if violations:
        raise ValueError(
            "ENGINEERING_CONTROL_PLANE_GOVERNANCE_INPUT_REJECTED: " + "; ".join(violations)
        )


def _invariant_violations(state: EngineeringState) -> list[str]:
    violations: list[str] = []
    expected = {
        "custom_gpt_identity": CUSTOM_GPT_IDENTITY,
        "runtime_generation": RUNTIME_GENERATION,
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": CAN_EXECUTE,
        "dry_run_only_no_live_trading_no_market_orders": DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS,
        "probability_authority": PROBABILITY_AUTHORITY,
    }
    for key, wanted in expected.items():
        if state.get(key) != wanted:
            violations.append(f"{key}={state.get(key)!r}; expected {wanted!r}")
    if state.get("change_class") not in {"A", "B", "C"}:
        violations.append("change_class must be A, B, or C")
    if int(state.get("max_repair_attempts", 0)) != MAX_REPAIR_ATTEMPTS:
        violations.append(f"max_repair_attempts must equal {MAX_REPAIR_ATTEMPTS}")
    if int(state.get("attempt_count", 0)) > MAX_REPAIR_ATTEMPTS:
        violations.append(f"attempt_count may not exceed {MAX_REPAIR_ATTEMPTS}")
    try:
        _assert_no_probability_payload(state)
    except ValueError as exc:
        violations.append(str(exc))
    return violations


def _normalize_input(state: EngineeringState) -> dict[str, Any]:
    return {
        "custom_gpt_identity": CUSTOM_GPT_IDENTITY,
        "runtime_generation": RUNTIME_GENERATION,
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": CAN_EXECUTE,
        "dry_run_only_no_live_trading_no_market_orders": DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS,
        "probability_authority": PROBABILITY_AUTHORITY,
        "attempt_count": 0,
        "max_repair_attempts": MAX_REPAIR_ATTEMPTS,
        "promotion_authorized": bool(state.get("promotion_authorized", False)),
        "trace_events": [],
    }


def build_engineering_graph(adapters: EngineeringAdapters, *, checkpointer: Any = None):
    """Compile the governed WOW engineering graph."""
    builder = StateGraph(EngineeringState)

    def intake(state: EngineeringState) -> dict[str, Any]:
        normalized = _normalize_input(state)
        merged = dict(state)
        merged.update(normalized)
        update = _handler_update(
            state=merged,
            stage="REPORTER_INTAKE",
            handler=adapters.intake,
            adapters=adapters,
        )
        return {**normalized, **update}

    def triage(state: EngineeringState) -> dict[str, Any]:
        result = _handler_update(
            state=state,
            stage="RESEARCH_TRIAGE",
            handler=adapters.triage,
            adapters=adapters,
        )
        disposition = result.get("triage_disposition")
        if disposition is not None:
            if disposition not in TRIAGE_TERMINALS:
                raise ValueError(f"INVALID_TRIAGE_TERMINAL_STATUS:{disposition!r}")
            result["terminal_status"] = disposition
        return result

    def diagnostics(state: EngineeringState) -> dict[str, Any]:
        return _handler_update(
            state=state,
            stage="DIAGNOSTICS",
            handler=adapters.diagnostics,
            adapters=adapters,
            set_stage=False,
        )

    def data_audit(state: EngineeringState) -> dict[str, Any]:
        return _handler_update(
            state=state,
            stage="DATA_AUDIT",
            handler=adapters.data_audit,
            adapters=adapters,
            set_stage=False,
        )

    def root_cause(state: EngineeringState) -> dict[str, Any]:
        return _handler_update(
            state=state,
            stage="ROOT_CAUSE",
            handler=adapters.root_cause,
            adapters=adapters,
        )

    def engineer(state: EngineeringState) -> dict[str, Any]:
        attempt = int(state.get("attempt_count", 0)) + 1
        updated = dict(state)
        updated["attempt_count"] = attempt
        result = _handler_update(
            state=updated,
            stage="ENGINEERING",
            handler=adapters.engineer,
            adapters=adapters,
        )
        result["attempt_count"] = attempt
        return result

    def sandbox_test(state: EngineeringState) -> dict[str, Any]:
        result = _handler_update(
            state=state,
            stage="SANDBOX_TEST",
            handler=adapters.sandbox_test,
            adapters=adapters,
        )
        result["tests_passed"] = bool(result.get("tests_passed", False))
        return result

    def blocked_after_retries(state: EngineeringState) -> dict[str, Any]:
        detail = f"sandbox tests failed after {state.get('attempt_count', 0)} governed repair attempts"
        event = _trace(state, "REPAIR_LOOP", "BLOCKED", detail)
        _emit(adapters, event)
        return {
            "workflow_stage": "REPAIR_LOOP",
            "blocker": detail,
            "smallest_remaining_action": "re-triage with the latest failing test output",
            "terminal_status": "BLOCKED_WITH_EXACT_REASON",
            "trace_events": [event],
        }

    def v17_governance(state: EngineeringState) -> dict[str, Any]:
        violations = _invariant_violations(state)
        status = "PASSED" if not violations else "FAILED"
        event = _trace(state, "V17_GOVERNANCE", status, "; ".join(violations))
        _emit(adapters, event)
        update: dict[str, Any] = {
            "workflow_stage": "V17_GOVERNANCE",
            "governance_status": status,
            "governance_violations": violations,
            "trace_events": [event],
        }
        if violations:
            update.update(
                {
                    "blocker": "V17 governance invariant violation: " + "; ".join(violations),
                    "smallest_remaining_action": "restore all V17 invariants before review",
                    "terminal_status": "BLOCKED_WITH_EXACT_REASON",
                }
            )
        return update

    def independent_review(state: EngineeringState) -> dict[str, Any]:
        return _handler_update(
            state=state,
            stage="INDEPENDENT_REVIEW",
            handler=adapters.independent_review,
            adapters=adapters,
        )

    def governance_gate(state: EngineeringState) -> dict[str, Any]:
        event = _trace(state, "GOVERNANCE_GATE", "EVALUATED")
        _emit(adapters, event)
        update: dict[str, Any] = {
            "workflow_stage": "GOVERNANCE_GATE",
            "trace_events": [event],
        }
        if state.get("review_status") != "APPROVED":
            update.update(
                {
                    "blocker": f"independent review status={state.get('review_status')!r}",
                    "smallest_remaining_action": "address independent review findings and rerun",
                    "terminal_status": "BLOCKED_WITH_EXACT_REASON",
                }
            )
        elif state.get("change_class") == "C":
            update["terminal_status"] = "EXPERIMENT_CREATED"
        elif not bool(state.get("promotion_authorized", False)):
            update["terminal_status"] = "PR_CREATED"
        return update

    def release(state: EngineeringState) -> dict[str, Any]:
        return _handler_update(
            state=state,
            stage="RELEASE_OBSERVABILITY",
            handler=adapters.release,
            adapters=adapters,
        )

    def production_qa(state: EngineeringState) -> dict[str, Any]:
        result = _handler_update(
            state=state,
            stage="QA_VERIFICATION",
            handler=adapters.production_qa,
            adapters=adapters,
        )
        if (
            result.get("qa_status") == "PASSED"
            and result.get("production_verification") == "VERIFIED"
        ):
            result["terminal_status"] = "FIXED_AND_VERIFIED"
        else:
            result.update(
                {
                    "blocker": "production QA or deployed-artifact verification incomplete",
                    "smallest_remaining_action": "complete production QA and exact deployed-artifact verification",
                    "terminal_status": "BLOCKED_WITH_EXACT_REASON",
                }
            )
        return result

    def terminal_reducer(state: EngineeringState) -> dict[str, Any]:
        terminal = state.get("terminal_status")
        if terminal not in TERMINAL_STATUSES:
            raise ValueError(f"INVALID_ENGINEERING_TERMINAL_STATUS:{terminal!r}")
        event = _trace(state, "REPORTER_CLOSURE", str(terminal))
        _emit(adapters, event)
        return {"workflow_stage": "REPORTER_CLOSURE", "trace_events": [event]}

    def root_cause_blocked(state: EngineeringState) -> dict[str, Any]:
        detail = "root cause was not established after diagnostics and data audit"
        event = _trace(state, "ROOT_CAUSE", "BLOCKED", detail)
        _emit(adapters, event)
        return {
            "blocker": detail,
            "smallest_remaining_action": "collect additional reproduction evidence and re-triage",
            "terminal_status": "BLOCKED_WITH_EXACT_REASON",
            "trace_events": [event],
        }

    def release_blocked(state: EngineeringState) -> dict[str, Any]:
        detail = f"release did not reach deployed state: {state.get('deployment_status')!r}"
        event = _trace(state, "RELEASE_OBSERVABILITY", "BLOCKED", detail)
        _emit(adapters, event)
        return {
            "blocker": detail,
            "smallest_remaining_action": "repair release/deployment failure and verify exact deployed SHA",
            "terminal_status": "BLOCKED_WITH_EXACT_REASON",
            "trace_events": [event],
        }

    def route_after_triage(state: EngineeringState) -> str | list[str]:
        if state.get("terminal_status"):
            return "terminal_reducer"
        return ["diagnostics", "data_audit"]

    def route_after_root_cause(state: EngineeringState) -> str:
        if state.get("root_cause"):
            return "engineer"
        return "root_cause_blocked"

    def route_after_test(state: EngineeringState) -> str:
        if state.get("tests_passed"):
            return "v17_governance"
        if int(state.get("attempt_count", 0)) < MAX_REPAIR_ATTEMPTS:
            return "engineer"
        return "blocked_after_retries"

    def route_after_governance(state: EngineeringState) -> str:
        return "terminal_reducer" if state.get("terminal_status") else "independent_review"

    def route_after_gate(state: EngineeringState) -> str:
        return "terminal_reducer" if state.get("terminal_status") else "release"

    def route_after_release(state: EngineeringState) -> str:
        if state.get("deployment_status") in {"DEPLOYED", "VERIFIED"}:
            return "production_qa"
        return "release_blocked"

    builder.add_node("intake", intake)
    builder.add_node("triage", triage)
    builder.add_node("diagnostics", diagnostics)
    builder.add_node("data_audit", data_audit)
    builder.add_node("root_cause", root_cause)
    builder.add_node("root_cause_blocked", root_cause_blocked)
    builder.add_node("engineer", engineer)
    builder.add_node("sandbox_test", sandbox_test)
    builder.add_node("blocked_after_retries", blocked_after_retries)
    builder.add_node("v17_governance", v17_governance)
    builder.add_node("independent_review", independent_review)
    builder.add_node("governance_gate", governance_gate)
    builder.add_node("release", release)
    builder.add_node("release_blocked", release_blocked)
    builder.add_node("production_qa", production_qa)
    builder.add_node("terminal_reducer", terminal_reducer)

    builder.add_edge(START, "intake")
    builder.add_edge("intake", "triage")
    builder.add_conditional_edges(
        "triage",
        route_after_triage,
        {
            "terminal_reducer": "terminal_reducer",
            "diagnostics": "diagnostics",
            "data_audit": "data_audit",
        },
    )
    builder.add_edge(["diagnostics", "data_audit"], "root_cause")
    builder.add_conditional_edges(
        "root_cause",
        route_after_root_cause,
        {"engineer": "engineer", "root_cause_blocked": "root_cause_blocked"},
    )
    builder.add_edge("root_cause_blocked", "terminal_reducer")
    builder.add_edge("engineer", "sandbox_test")
    builder.add_conditional_edges(
        "sandbox_test",
        route_after_test,
        {
            "engineer": "engineer",
            "v17_governance": "v17_governance",
            "blocked_after_retries": "blocked_after_retries",
        },
    )
    builder.add_edge("blocked_after_retries", "terminal_reducer")
    builder.add_conditional_edges(
        "v17_governance",
        route_after_governance,
        {"terminal_reducer": "terminal_reducer", "independent_review": "independent_review"},
    )
    builder.add_edge("independent_review", "governance_gate")
    builder.add_conditional_edges(
        "governance_gate",
        route_after_gate,
        {"terminal_reducer": "terminal_reducer", "release": "release"},
    )
    builder.add_conditional_edges(
        "release",
        route_after_release,
        {"production_qa": "production_qa", "release_blocked": "release_blocked"},
    )
    builder.add_edge("release_blocked", "terminal_reducer")
    builder.add_edge("production_qa", "terminal_reducer")
    builder.add_edge("terminal_reducer", END)

    return builder.compile(
        checkpointer=checkpointer,
        name="WOW_V17_ENGINEERING_CONTROL_PLANE",
    )


def invoke_issue(graph: Any, issue: EngineeringState) -> EngineeringState:
    """Start one canonical issue using its issue_id as durable thread identity."""
    issue_id = str(issue.get("issue_id", "")).strip()
    if not issue_id:
        raise ValueError("issue_id is required")
    _assert_new_issue_boundary(issue)
    _assert_input_governance(issue)
    _assert_no_probability_payload(issue)
    return graph.invoke(issue, {"configurable": {"thread_id": issue_id}})


def resume_issue(graph: Any, issue_id: str) -> EngineeringState:
    """Resume a checkpointed issue without replaying completed upstream nodes."""
    thread_id = str(issue_id).strip()
    if not thread_id:
        raise ValueError("issue_id is required")
    return graph.invoke(None, {"configurable": {"thread_id": thread_id}})


@contextmanager
def durable_postgres_control_plane(
    adapters: EngineeringAdapters,
    db_uri: str | None = None,
):
    """Yield a production-durable graph backed by PostgreSQL checkpoints."""
    uri = db_uri or os.getenv("WOW_ENGINEERING_CHECKPOINT_DB_URI")
    if not uri:
        raise RuntimeError(
            "WOW_ENGINEERING_CHECKPOINT_DB_URI is required for durable mode"
        )
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(uri) as saver:
        saver.setup()
        yield build_engineering_graph(adapters, checkpointer=saver)
