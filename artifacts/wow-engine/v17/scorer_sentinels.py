"""Stage-specific engineering sentinels for WOW V17 scorer paths.

Diagnostic infrastructure only: no sporting probability is produced or altered,
and no execution authority is granted.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

RUNTIME_GENERATION = "V17_ACTIVE"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
CAN_EXECUTE = False
SENTINEL_SCHEMA_VERSION = "WOW_SCORER_SENTINEL_V1"

STAGES = (
    "DISCOVERY",
    "IDENTITY",
    "HYDRATION",
    "MODEL_REGISTRY",
    "MODEL_INVOKE",
    "CALIBRATION_BOUNDS",
    "PERSISTENCE",
    "TERMINAL",
    "USER_PATH",
)
VALID_STAGE_STATUSES = frozenset({"PASS", "BLOCKED", "NOT_APPLICABLE"})


@dataclass(frozen=True)
class SentinelStage:
    stage: str
    status: str
    blocker: str | None = None
    stage_ms: float | None = None
    detail: str | None = None

    def validate(self) -> None:
        if self.stage not in STAGES:
            raise ValueError(f"unknown sentinel stage: {self.stage}")
        if self.status not in VALID_STAGE_STATUSES:
            raise ValueError(f"invalid sentinel stage status: {self.status}")
        if self.status == "BLOCKED" and not self.blocker:
            raise ValueError("BLOCKED sentinel stage requires an exact blocker")
        if self.status != "BLOCKED" and self.blocker:
            raise ValueError("only BLOCKED sentinel stages may carry a blocker")
        if self.stage_ms is not None and self.stage_ms < 0:
            raise ValueError("stage_ms must be non-negative")


def stage_pass(stage: str, *, stage_ms: float | None = None, detail: str | None = None) -> SentinelStage:
    result = SentinelStage(stage=stage, status="PASS", stage_ms=stage_ms, detail=detail)
    result.validate()
    return result


def stage_blocked(
    stage: str,
    blocker: str,
    *,
    stage_ms: float | None = None,
    detail: str | None = None,
) -> SentinelStage:
    result = SentinelStage(
        stage=stage,
        status="BLOCKED",
        blocker=blocker,
        stage_ms=stage_ms,
        detail=detail,
    )
    result.validate()
    return result


def stage_not_applicable(stage: str, *, detail: str | None = None) -> SentinelStage:
    result = SentinelStage(stage=stage, status="NOT_APPLICABLE", detail=detail)
    result.validate()
    return result


def evaluate_sentinel(
    *,
    sport: str,
    surface: str,
    route: str,
    stages: Iterable[SentinelStage],
    git_sha: str | None = None,
    specialist: str | None = None,
    model_version: str | None = None,
    artifact_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one deterministic stage receipt and preserve the first exact blocker."""

    supplied = list(stages)
    if not supplied:
        raise ValueError("sentinel requires at least one stage result")

    seen: set[str] = set()
    by_stage: dict[str, SentinelStage] = {}
    for result in supplied:
        result.validate()
        if result.stage in seen:
            raise ValueError(f"duplicate sentinel stage: {result.stage}")
        seen.add(result.stage)
        by_stage[result.stage] = result

    ordered = [by_stage[stage] for stage in STAGES if stage in by_stage]
    first_blocked = next((item for item in ordered if item.status == "BLOCKED"), None)

    return {
        "schema_version": SENTINEL_SCHEMA_VERSION,
        "runtime_generation": RUNTIME_GENERATION,
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": CAN_EXECUTE,
        "sport": sport.upper(),
        "surface": surface.upper(),
        "route": route,
        "git_sha": git_sha,
        "specialist": specialist,
        "model_version": model_version,
        "artifact_id": artifact_id,
        "status": "SENTINEL_BLOCKED" if first_blocked else "SENTINEL_PASS",
        "first_failing_stage": first_blocked.stage if first_blocked else None,
        "blocker": first_blocked.blocker if first_blocked else None,
        "stages": [asdict(item) for item in ordered],
        "metadata": dict(metadata or {}),
    }


def sentinel_failure_capsule_input(receipt: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return Failure Capsule input without creating a competing incident ledger."""

    if receipt.get("status") != "SENTINEL_BLOCKED":
        return None
    return {
        "route": receipt.get("route"),
        "sport": receipt.get("sport"),
        "market_family": receipt.get("surface"),
        "scorer_stage": receipt.get("first_failing_stage"),
        "terminal_code": receipt.get("blocker"),
        "git_sha": receipt.get("git_sha"),
        "specialist": receipt.get("specialist"),
        "model_version": receipt.get("model_version"),
        "artifact_id": receipt.get("artifact_id"),
    }
