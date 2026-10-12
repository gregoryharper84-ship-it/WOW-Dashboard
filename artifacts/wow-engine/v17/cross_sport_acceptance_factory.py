"""Read-only cross-sport production-acceptance evidence contract (issue #1689).

This module does NOT discover events, certify models, publish probabilities, sign
release receipts, grant Action access, or place wagers. Trusted callers must
supply independently sourced, immutable event and release evidence. Existing
sport-specific registries and V17_TERMINAL_REDUCER retain all authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import Any, Mapping

ACCEPTANCE_CONTRACT_VERSION = "WOW_V17_CROSS_SPORT_ACCEPTANCE_V1"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
CAN_EXECUTE = False
_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_MARKETS = frozenset({"MONEYLINE", "SPREAD", "RUN_LINE", "PUCK_LINE", "PROP"})
_PREGAME_STATES = frozenset({"SCHEDULED", "PRE_GAME"})
_KNOWN_NONPREGAME = frozenset({"IN_PROGRESS", "FINAL", "POSTPONED", "CANCELLED", "CANCELED"})
# Played states; POSTPONED/CANCELLED are not evidence the window was played.
_ELAPSED_STATES = frozenset({"IN_PROGRESS", "FINAL"})


def _aware(value: datetime) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


@dataclass(frozen=True)
class Lane:
    sport: str
    market: str
    operation_id: str
    controlling_specialist: str
    stat_type: str | None = None
    period: str | None = None
    direction: str | None = None
    # Declared routing metadata, not certification.
    research_only: bool = True

    def __post_init__(self) -> None:
        if not self.sport or self.sport != self.sport.upper():
            raise ValueError("ACCEPTANCE_SPORT_INVALID")
        if self.market not in _SUPPORTED_MARKETS:
            raise ValueError("ACCEPTANCE_MARKET_INVALID")
        if not self.operation_id or not self.controlling_specialist:
            raise ValueError("ACCEPTANCE_SPECIALIST_OR_ACTION_MISSING")
        if self.market == "PROP" and not self.stat_type:
            raise ValueError("ACCEPTANCE_PROP_STAT_TYPE_MISSING")
        if self.market != "PROP" and (self.stat_type or self.period or self.direction):
            raise ValueError("ACCEPTANCE_NON_PROP_IDENTITY_INVALID")

    def identity(self) -> dict[str, str | None]:
        return {
            "sport": self.sport, "market": self.market, "stat_type": self.stat_type,
            "period": self.period, "direction": self.direction,
            "controlling_specialist": self.controlling_specialist,
            "action_operation_id": self.operation_id,
        }


@dataclass(frozen=True)
class Fixture:
    sport: str
    official_event_id: str
    start_utc: datetime
    status: str


@dataclass(frozen=True)
class SourceInventory:
    source_id: str
    payload_sha256: str
    fetched_at: datetime
    fixtures: tuple[Fixture, ...]


@dataclass(frozen=True)
class CohortPolicy:
    # Explicit sport-specific threshold, not a universal NFL count.
    minimum_unique_events: int
    window_start: datetime
    window_end: datetime
    max_window_days: int = 9
    max_source_age: timedelta = timedelta(hours=6)


@dataclass(frozen=True)
class CohortResult:
    status: str
    blockers: tuple[str, ...]
    event_ids: tuple[str, ...]
    eligible_count: int
    required_count: int
    source_id: str
    source_payload_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "acceptance_contract_version": ACCEPTANCE_CONTRACT_VERSION,
            "status": self.status,
            "blockers": list(self.blockers),
            "canonical_event_ids": list(self.event_ids),
            "eligible_event_count": self.eligible_count,
            "required_event_count": self.required_count,
            "source_id": self.source_id,
            "source_payload_sha256": self.source_payload_sha256,
            "global_terminal_authority": TERMINAL_AUTHORITY,
            "probability_publishable": False,
            "can_execute": False,
        }


def evaluate_cohort(lane: Lane, inventory: SourceInventory, policy: CohortPolicy, *, now: datetime) -> CohortResult:
    """Evaluate canonical pregame *coverage*, never sporting probability.

    No hardcoded dates or inferred schedules. The caller supplies a bounded
    league-aware window and source inventory. A thin slate is a typed HOLD.
    """
    blockers: list[str] = []
    window_limit_valid = type(policy.max_window_days) is int and 1 <= policy.max_window_days <= 14
    source_age_valid = isinstance(policy.max_source_age, timedelta) and policy.max_source_age > timedelta(0)
    if not window_limit_valid:
        blockers.append("ACCEPTANCE_WINDOW_LIMIT_INVALID")
    if not source_age_valid:
        blockers.append("ACCEPTANCE_SOURCE_AGE_POLICY_INVALID")
    if not all(_aware(t) for t in (now, inventory.fetched_at, policy.window_start, policy.window_end)):
        blockers.append("ACCEPTANCE_TIMEZONE_UNVERIFIED")
    else:
        if source_age_valid and (now < inventory.fetched_at or now - inventory.fetched_at > policy.max_source_age):
            blockers.append("ACCEPTANCE_SOURCE_STALE_OR_FUTURE")
        if window_limit_valid and (policy.window_end <= policy.window_start or policy.window_end - policy.window_start > timedelta(days=policy.max_window_days)):
            blockers.append("ACCEPTANCE_COHORT_WINDOW_INVALID")
        if policy.window_end <= now:
            blockers.append("ACCEPTANCE_HISTORICAL_COHORT_NOT_PREGAME")
    if not inventory.source_id or not isinstance(inventory.payload_sha256, str) or not _SHA256.fullmatch(inventory.payload_sha256):
        blockers.append("ACCEPTANCE_SOURCE_PROVENANCE_MISSING")
    if type(policy.minimum_unique_events) is not int or policy.minimum_unique_events < 1:
        blockers.append("ACCEPTANCE_THRESHOLD_INVALID")
    eligible: dict[str, Fixture] = {}
    for event in inventory.fixtures:
        if not event.official_event_id or not _aware(event.start_utc):
            blockers.append("ACCEPTANCE_EVENT_IDENTITY_OR_TIME_MISSING")
            continue
        if event.sport != lane.sport:
            blockers.append("ACCEPTANCE_INVENTORY_CROSS_SPORT")
            continue
        if event.status not in (_PREGAME_STATES | _KNOWN_NONPREGAME):
            blockers.append("ACCEPTANCE_EVENT_STATE_UNKNOWN")
            continue
        if not (_aware(policy.window_start) and _aware(policy.window_end) and _aware(now)):
            continue
        if not policy.window_start <= event.start_utc < policy.window_end:
            continue
        # #1690: an in-window game that is live/final, or whose pregame-status
        # kickoff has already passed (fail closed on stale status), means the
        # window is partly played. Remaining games never form a complete week.
        if event.status in _ELAPSED_STATES or (event.status in _PREGAME_STATES and event.start_utc <= now):
            blockers.append("ACCEPTANCE_WEEK_PARTIALLY_ELAPSED")
            continue
        if event.status not in _PREGAME_STATES:
            continue
        if event.official_event_id in eligible:
            blockers.append("ACCEPTANCE_CANONICAL_EVENT_DUPLICATE")
        eligible[event.official_event_id] = event
    ids = tuple(k for k, _ in sorted(eligible.items(), key=lambda x: (x[1].start_utc, x[0])))
    if type(policy.minimum_unique_events) is int and len(ids) < policy.minimum_unique_events:
        blockers.append("ACCEPTANCE_ELIGIBLE_COHORT_INSUFFICIENT")
    blockers = list(dict.fromkeys(blockers))
    return CohortResult(
        "COHORT_ELIGIBLE_FOR_TEST" if not blockers else "COHORT_HELD",
        tuple(blockers), ids, len(ids), policy.minimum_unique_events,
        inventory.source_id, inventory.payload_sha256,
    )


# Observational stages, never model, release or publication authority.
_REQUIRED_GATES = (
    "BACKEND_MODEL_PACKAGE", "MODEL_CALIBRATION_AND_BOUNDS", "MODEL_CERTIFICATION",
    "CANONICAL_IDENTITY", "FINAL_TERMINAL_REDUCER", "BACKEND_ACTION_CANARY",
    "LIVE_GPT_EDITOR_PARITY", "FULL_POOL_RECONCILIATION",
)
_GATE_VALUES = frozenset({"PASS", "HOLD", "FAIL", "DEFERRED", "NOT_CHECKED"})


def reconcile_lane(
    lane: Lane, *,
    source_commit_sha: str, deployed_commit_sha: str, render_deploy_id: str,
    checks: Mapping[str, str], cohort: CohortResult,
    artifact_checksum: str | None = None, calibrator_version: str | None = None,
    action_operation_id: str | None = None, terminal_receipt_id: str | None = None,
) -> dict[str, Any]:
    """Snapshot supplied evidence; cannot certify or publish a probability.

    Signed model/terminal/QA authority must be verified at each source. Even
    complete evidence here cannot cause release, scoring or model promotion.
    """
    blockers: list[str] = []
    if not (isinstance(source_commit_sha, str) and _SHA.fullmatch(source_commit_sha)) or not (isinstance(deployed_commit_sha, str) and _SHA.fullmatch(deployed_commit_sha)):
        blockers.append("ACCEPTANCE_DEPLOY_SHA_INVALID")
    elif source_commit_sha != deployed_commit_sha:
        blockers.append("ACCEPTANCE_DEPLOY_SHA_MISMATCH")
    if not render_deploy_id:
        blockers.append("ACCEPTANCE_RENDER_RECEIPT_MISSING")
    if action_operation_id != lane.operation_id:
        blockers.append("ACCEPTANCE_ACTION_OPERATION_MISMATCH")
    if not artifact_checksum or not calibrator_version:
        blockers.append("ACCEPTANCE_FITTED_ARTIFACT_OR_CALIBRATOR_MISSING")
    if not terminal_receipt_id:
        blockers.append("ACCEPTANCE_TERMINAL_RECEIPT_MISSING")
    if cohort.status != "COHORT_ELIGIBLE_FOR_TEST":
        blockers.extend(cohort.blockers or ("ACCEPTANCE_COHORT_NOT_ELIGIBLE",))
    for stage in _REQUIRED_GATES:
        result = checks.get(stage, "NOT_CHECKED")
        if result not in _GATE_VALUES:
            blockers.append("ACCEPTANCE_GATE_STATUS_INVALID:" + stage)
        elif result != "PASS":
            blockers.append("ACCEPTANCE_GATE_" + result + ":" + stage)
    if any(stage not in _REQUIRED_GATES for stage in checks):
        blockers.append("ACCEPTANCE_UNKNOWN_GATE")
    if lane.research_only:
        blockers.append("ACCEPTANCE_RESEARCH_ONLY_NOT_PUBLISHABLE")

    blockers = list(dict.fromkeys(blockers))
    return {
        "acceptance_contract_version": ACCEPTANCE_CONTRACT_VERSION,
        "lane": lane.identity(),
        "observed_state": "ACCEPTANCE_EVIDENCE_COMPLETE" if not blockers else "ACCEPTANCE_HELD",
        "blockers": blockers,
        "stage_results": {k: checks.get(k, "NOT_CHECKED") for k in _REQUIRED_GATES},
        "source_commit_sha": source_commit_sha,
        "deployed_commit_sha": deployed_commit_sha,
        "render_deploy_id": render_deploy_id,
        "artifact_checksum": artifact_checksum,
        "calibrator_version": calibrator_version,
        "terminal_receipt_id": terminal_receipt_id,
        "cohort": cohort.as_dict(),
        "probability_publishable": False,
        "can_execute": False,
        "global_terminal_authority": TERMINAL_AUTHORITY,
    }


@dataclass(frozen=True)
class SourceWeekWindow:
    """League-designated week from an independently validated schedule source.

    A caller must authenticate and canonicalize the upstream schedule; this
    object does not certify a provider, fixture, model, or official week.
    """
    sport: str
    season_key: str
    week_key: str
    window_start: datetime
    window_end: datetime


def choose_current_pregame_cohort(
    lane: Lane,
    inventory: SourceInventory,
    source_weeks: tuple[SourceWeekWindow, ...],
    *,
    now: datetime,
    minimum_unique_events: int,
    max_lookahead: timedelta = timedelta(days=14),
) -> dict[str, Any]:
    """Select the nearest complete source-declared future/current league week.

    This is ONLY coverage selection. Do not infer week boundaries from clock
    dates, splice games from different weeks, reuse completed events, or lower
    coverage thresholds when the current week is already partly played.
    Model/Action/editor/terminal certification remains wholly independent.
    """
    def hold(code: str, attempted: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return {
            "status": "COHORT_HELD",
            "blockers": [code],
            "selected_week": None,
            "cohort": None,
            "attempted_week_receipts": attempted or [],
            "probability_publishable": False,
            "can_execute": False,
            "global_terminal_authority": TERMINAL_AUTHORITY,
        }

    if not _aware(now):
        return hold("ACCEPTANCE_TIMEZONE_UNVERIFIED")
    if type(minimum_unique_events) is not int or minimum_unique_events < 1:
        return hold("ACCEPTANCE_THRESHOLD_INVALID")
    if not isinstance(max_lookahead, timedelta) or not timedelta(0) < max_lookahead <= timedelta(days=14):
        return hold("ACCEPTANCE_LOOKAHEAD_INVALID")
    if not isinstance(source_weeks, (list, tuple)):
        return hold("ACCEPTANCE_VERIFIED_WEEK_INPUT_INVALID")

    seen: set[tuple[str, str, str]] = set()
    windows: list[SourceWeekWindow] = []
    for week in source_weeks:
        if not isinstance(week, SourceWeekWindow):
            return hold("ACCEPTANCE_VERIFIED_WEEK_INPUT_INVALID")
        if week.sport != lane.sport or not week.season_key or not week.week_key:
            return hold("ACCEPTANCE_VERIFIED_WEEK_IDENTITY_INVALID")
        if not _aware(week.window_start) or not _aware(week.window_end):
            return hold("ACCEPTANCE_TIMEZONE_UNVERIFIED")
        if not timedelta(0) < week.window_end - week.window_start <= timedelta(days=9):
            return hold("ACCEPTANCE_VERIFIED_WEEK_WINDOW_INVALID")
        key = (week.sport, week.season_key, week.week_key)
        if key in seen:
            return hold("ACCEPTANCE_DUPLICATE_VERIFIED_WEEK")
        seen.add(key)
        if week.window_end > now and week.window_start <= now + max_lookahead:
            windows.append(week)

    if not windows:
        return hold("ACCEPTANCE_NO_CURRENT_OR_FUTURE_VERIFIED_WEEK")

    attempts: list[dict[str, Any]] = []
    for week in sorted(windows, key=lambda w: (w.window_start, w.season_key, w.week_key)):
        cohort = evaluate_cohort(
            lane, inventory,
            CohortPolicy(minimum_unique_events, week.window_start, week.window_end),
            now=now,
        )
        receipt = {
            "season_key": week.season_key,
            "week_key": week.week_key,
            "status": cohort.status,
            "blockers": list(cohort.blockers),
            "eligible_count": cohort.eligible_count,
            "required_count": cohort.required_count,
        }
        attempts.append(receipt)
        if cohort.status == "COHORT_ELIGIBLE_FOR_TEST":
            return {
                "status": "COHORT_ELIGIBLE_FOR_TEST",
                "blockers": [],
                "selected_week": {"sport": week.sport, "season_key": week.season_key, "week_key": week.week_key},
                "cohort": cohort.as_dict(),
                "attempted_week_receipts": attempts,
                "probability_publishable": False,
                "can_execute": False,
                "global_terminal_authority": TERMINAL_AUTHORITY,
            }

    # Preserve every per-week reason. The service may not manufacture a slate.
    return {
        "status": "COHORT_HELD",
        "blockers": ["ACCEPTANCE_NO_FULL_VERIFIED_WEEK"],
        "selected_week": None,
        "cohort": None,
        "attempted_week_receipts": attempts,
        "probability_publishable": False,
        "can_execute": False,
        "global_terminal_authority": TERMINAL_AUTHORITY,
    }
