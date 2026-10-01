"""Fail-closed LangGraph runtime readiness for the resident WOW engineering worker.

This module does not run sporting models, mutate sporting probabilities, write
code, merge pull requests, deploy services, or execute wagers. It only proves
whether the resident engineering worker is eligible to host the governed
LangGraph control plane once a durable checkpoint backend is explicitly
configured.
"""
from __future__ import annotations

import logging
import os
from typing import Any

_logger = logging.getLogger("wow.v17.engineering_langgraph_runtime")

TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
CUSTOM_GPT_IDENTITY = "WOW_BETTING_ENGINE"
RUNTIME_GENERATION = "V17_ACTIVE"
PROBABILITY_AUTHORITY = "NONE"
CAN_EXECUTE = False


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _enabled() -> bool:
    return _truthy(os.getenv("WOW_ENGINEERING_LANGGRAPH_ENABLED", "0"))


def _governance_blocker() -> str | None:
    if _truthy(os.getenv("WOW_CAN_EXECUTE", "false")):
        return "WOW_CAN_EXECUTE_MUST_REMAIN_FALSE"
    if not _truthy(os.getenv("WOW_DRY_RUN_ONLY", "true")):
        return "WOW_DRY_RUN_ONLY_MUST_REMAIN_TRUE"
    return None


def langgraph_runtime_status() -> dict[str, Any]:
    """Return non-secret readiness state for the resident engineering worker."""
    base: dict[str, Any] = {
        "custom_gpt_identity": CUSTOM_GPT_IDENTITY,
        "runtime_generation": RUNTIME_GENERATION,
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": CAN_EXECUTE,
        "probability_authority": PROBABILITY_AUTHORITY,
        "enabled": _enabled(),
        "checkpoint_backend": "POSTGRES",
        "checkpoint_uri_configured": False,
        "langgraph_importable": False,
        "postgres_checkpointer_importable": False,
    }

    blocker = _governance_blocker()
    if blocker:
        return {
            **base,
            "status": "BLOCKED",
            "terminal_status": "BLOCKED_WITH_EXACT_REASON",
            "blocker": blocker,
            "smallest_remaining_action": "restore V17 non-execution runtime invariants",
        }

    if not _enabled():
        return {
            **base,
            "status": "DISABLED",
            "terminal_status": "DEFERRED_WITH_JUSTIFICATION",
            "blocker": "LANGGRAPH_ENGINEERING_RUNTIME_NOT_ENABLED",
            "smallest_remaining_action": (
                "configure an approved durable checkpoint URI, validate the worker adapter, "
                "then explicitly enable WOW_ENGINEERING_LANGGRAPH_ENABLED"
            ),
        }

    # Import checks are deliberately late so the disabled path remains cheap and
    # deterministic. Exact package versions are controlled by requirements.txt.
    try:
        import langgraph  # noqa: F401
    except Exception as exc:
        return {
            **base,
            "status": "BLOCKED",
            "terminal_status": "BLOCKED_WITH_EXACT_REASON",
            "blocker": f"LANGGRAPH_IMPORT_FAILED:{type(exc).__name__}",
            "smallest_remaining_action": "repair the worker LangGraph dependency installation",
        }
    base["langgraph_importable"] = True

    try:
        from langgraph.checkpoint.postgres import PostgresSaver  # noqa: F401
    except Exception as exc:
        return {
            **base,
            "status": "BLOCKED",
            "terminal_status": "BLOCKED_WITH_EXACT_REASON",
            "blocker": f"LANGGRAPH_POSTGRES_CHECKPOINTER_IMPORT_FAILED:{type(exc).__name__}",
            "smallest_remaining_action": "repair the worker Postgres checkpoint dependency installation",
        }
    base["postgres_checkpointer_importable"] = True

    checkpoint_uri = os.getenv("WOW_ENGINEERING_CHECKPOINT_DB_URI", "").strip()
    if not checkpoint_uri:
        return {
            **base,
            "status": "BLOCKED",
            "terminal_status": "BLOCKED_WITH_EXACT_REASON",
            "blocker": "WOW_ENGINEERING_CHECKPOINT_DB_URI_MISSING",
            "smallest_remaining_action": (
                "configure an approved least-privilege PostgreSQL checkpoint URI through Render secret management"
            ),
        }
    base["checkpoint_uri_configured"] = True

    return {
        **base,
        "status": "READY_FOR_ADAPTER_INTEGRATION",
        "terminal_status": "PR_CREATED",
        "blocker": "ENGINEERING_WORKER_ADAPTER_NOT_YET_PROMOTED",
        "smallest_remaining_action": (
            "connect the governed Reporter/Triage/Diagnostics/Data-Audit/Engineering/Review/Release/QA adapters "
            "to the merged LangGraph control plane and validate a production pilot issue"
        ),
    }


def log_langgraph_runtime_status() -> dict[str, Any]:
    """Log only redacted readiness facts; never log the checkpoint URI itself."""
    status = langgraph_runtime_status()
    _logger.warning(
        "WOW_ENGINEERING_LANGGRAPH status=%s terminal_status=%s blocker=%s "
        "enabled=%s checkpoint_uri_configured=%s terminal_authority=%s "
        "probability_authority=%s can_execute=false",
        status.get("status"),
        status.get("terminal_status"),
        status.get("blocker"),
        status.get("enabled"),
        status.get("checkpoint_uri_configured"),
        TERMINAL_AUTHORITY,
        PROBABILITY_AUTHORITY,
    )
    return status


def install_celery_worker_hooks() -> None:
    """Attach one readiness receipt to resident Celery worker startup."""
    from celery.signals import worker_ready

    @worker_ready.connect(weak=False)
    def _report_langgraph_readiness(**_: object) -> None:
        log_langgraph_runtime_status()
