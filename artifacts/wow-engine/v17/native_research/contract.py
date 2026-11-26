"""Deterministic blocker -> research-task contract.

A research task is derived from a specialist blocker reported by the
certification inventory (``assess``) or by a lane receipt (for example the
soccer forward-evidence ``SOCCER_SOURCE_RESULTS_STALE`` block). Task identity
is a stable hash of what is missing, so the same blocker always maps to the
same task. Whether a task runs again is decided only by new evidence (a
changed blocker fingerprint), a freshness trigger, or an authorized retry.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from v17.cross_sport_certification_inventory import CertificationEvidence, assess

from . import INERT

# Ordered research outcomes. ``progress_stage`` is the measurable progress a
# blocker has made; research alone can never reach "resolved".
PROGRESS_STAGE = {
    "NOT_ATTEMPTED": 0,
    "ACQUISITION_FAILED": 0,
    "BUDGET_EXHAUSTED": 0,
    "NO_SOURCE_FOUND": 1,          # evidence-backed negative result
    "CONFLICTING_EVIDENCE": 1,     # candidate found but fails closed
    "CANDIDATE_FOUND_UNVERIFIED": 2,
    "CANDIDATE_VERIFIED_RIGHTS_REVIEW_REQUIRED": 3,
    "CANDIDATE_VERIFIED_PENDING_INDEPENDENT_REVIEW": 4,
}
OUTCOMES = tuple(PROGRESS_STAGE)
TRANSIENT_OUTCOMES = frozenset({"ACQUISITION_FAILED", "BUDGET_EXHAUSTED"})
MAX_RETRIES_PER_DAY = 3
RETRY_BACKOFF = (timedelta(hours=1), timedelta(hours=4), timedelta(hours=16))


@dataclass(frozen=True)
class ResearchSpec:
    missing_evidence_field: str
    objective: str
    required_source_type: str
    permitted_methods: tuple[str, ...]
    priority: int
    refresh_after: timedelta
    negative_refresh_after: timedelta = timedelta(days=7)


_DATASET_METHODS = ("REGISTERED_SOURCE", "EXTERNAL_EVIDENCE_INTAKE", "LOCAL_INDEX", "GITHUB_DATASET_DISCOVERY")
_FEED_METHODS = ("EXTERNAL_EVIDENCE_INTAKE", "LOCAL_INDEX", "GITHUB_DATASET_DISCOVERY")
_SCIENCE_METHODS = ("LOCAL_INDEX", "CROSSREF")

# Blocker code -> what research must find. Unmapped codes are recorded as
# NOT_RESEARCHABLE; they are never dropped silently.
RESEARCH_SPECS: dict[str, ResearchSpec] = {
    "SOCCER_SOURCE_RESULTS_STALE": ResearchSpec(
        "official_results_current",
        "Find a current, free, automatable results source for {league} {season} that records every "
        "past-dated league match, with usable source rights.",
        "RESULTS_FEED", _DATASET_METHODS, 1, timedelta(hours=72)),
    "SOCCER_KICKOFF_TIME_UNVERIFIED": ResearchSpec(
        "fixture_kickoff_time",
        "Find a free fixture source for {league} {season} that lists exact kickoff times.",
        "FIXTURE_FEED", _DATASET_METHODS, 2, timedelta(hours=72)),
    # Prop specialists nearest certification (NFL, NHL, NBA player props).
    "NFLVERSE_INJURY_REPORT_STALE": ResearchSpec(
        "official_injury_report_current",
        "Find a current, free, automatable NFL injury-report source with publication timestamps and usable "
        "rights for {season}.",
        "INJURY_REPORT_FEED", _FEED_METHODS, 1, timedelta(hours=72)),
    "NFLVERSE_INJURY_REPORT_TIMESTAMP_UNVERIFIED": ResearchSpec(
        "injury_report_publication_timestamp",
        "Find an NFL injury-report source that records when each report was published.",
        "INJURY_REPORT_FEED", _FEED_METHODS, 1, timedelta(hours=72)),
    "NHL_INJURY_REPORT_UNAVAILABLE": ResearchSpec(
        "official_injury_report_current",
        "Find a current, free, automatable NHL injury-report source with usable rights.",
        "INJURY_REPORT_FEED", _FEED_METHODS, 1, timedelta(hours=72)),
    "NHL_GOALIE_MISSING": ResearchSpec(
        "starting_goalie_confirmation",
        "Find free, automatable NHL starting-goalie confirmation sources with timestamps and usable rights.",
        "GOALIE_CONFIRMATION_FEED", _FEED_METHODS, 1, timedelta(hours=72)),
    "TWO_INDEPENDENT_OFFICIAL_GOALIE_SOURCES_REQUIRED": ResearchSpec(
        "independent_goalie_confirmation_sources",
        "Find two independent official NHL starting-goalie confirmation sources.",
        "GOALIE_CONFIRMATION_FEED", _FEED_METHODS, 1, timedelta(hours=72)),
    "NBA_LINEUP_EVIDENCE_SOURCE_MISSING": ResearchSpec(
        "starting_lineup_evidence",
        "Find a free, automatable NBA starting-lineup and availability source with timestamps and usable rights.",
        "LINEUP_FEED", _FEED_METHODS, 1, timedelta(hours=72)),
    "CURRENT_DATA_NOT_CERTIFIED": ResearchSpec(
        "current_season_data",
        "Find a current, free, automatable {sport} data source for {season} with usable rights.",
        "RESULTS_FEED", _DATASET_METHODS, 2, timedelta(hours=72)),
    "SOURCE_PROVENANCE_NOT_CERTIFIED": ResearchSpec(
        "source_rights_evidence",
        "Collect licensing and source-use terms for the {sport} sources in use.",
        "SOURCE_RIGHTS_DOCUMENTATION", ("REGISTERED_SOURCE", "LOCAL_INDEX"), 2, timedelta(days=14)),
    "CALIBRATOR_NOT_READY": ResearchSpec(
        "calibration_method_evidence",
        "Find peer-reviewed calibration methods for {sport} {market} probability models.",
        "SCIENTIFIC_LITERATURE", _SCIENCE_METHODS, 3, timedelta(days=30)),
    "DETERMINISTIC_REPLAY_NOT_READY": ResearchSpec(
        "historical_replay_corpus",
        "Find a free historical {sport} dataset suitable for chronological replay.",
        "HISTORICAL_DATASET", _DATASET_METHODS, 3, timedelta(days=14)),
    "FITTED_MODEL_ARTIFACT_REQUIRED": ResearchSpec(
        "model_method_evidence",
        "Find published {sport} {market} modelling methods and the historical data they require.",
        "SCIENTIFIC_LITERATURE", _SCIENCE_METHODS, 4, timedelta(days=30)),
    "CONTROLLING_SPECIALIST_UNAVAILABLE": ResearchSpec(
        "model_method_evidence",
        "Find published {sport} {market} modelling methods and the historical data they require.",
        "SCIENTIFIC_LITERATURE", _SCIENCE_METHODS, 4, timedelta(days=30)),
}


@dataclass(frozen=True)
class BlockerInput:
    """One observed specialist blocker plus the context it was observed in."""

    sport: str
    market_route: str
    specialist_id: str
    blocker_code: str
    league: str | None = None
    season: str | None = None
    candidate_id: str | None = None
    surface: str = "TEAM_EVENT"
    candidate_ready: bool = False
    # Stable facts about the blocker; a change is "new evidence".
    context: Mapping[str, Any] = field(default_factory=dict)
    # Externally reported source leads (e.g. ChatGPT via a GitHub incident).
    # Leads are verified natively; a new lead is new evidence.
    leads: tuple[Any, ...] = ()


@dataclass(frozen=True)
class ResearchTask:
    task_id: str
    sport: str
    league: str | None
    season: str | None
    market_route: str
    specialist_id: str
    candidate_id: str | None
    blocker_code: str
    certification_status: str
    certification_blockers: tuple[str, ...]
    missing_evidence_field: str
    research_objective: str
    required_source_type: str
    permitted_methods: tuple[str, ...]
    priority: int
    blocker_fingerprint: str
    researchable: bool

    def row(self) -> dict[str, Any]:
        out = asdict(self)
        out["certification_blockers"] = list(self.certification_blockers)
        out["permitted_methods"] = list(self.permitted_methods)
        return out


@dataclass(frozen=True)
class ScheduleDecision:
    schedule: bool
    reason: str
    next_eligible_at: datetime | None = None


def sha256_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
                          .encode("utf-8")).hexdigest()


def _aware(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("NRE_TIMESTAMP_NOT_TIMEZONE_AWARE")
    return parsed.astimezone(timezone.utc)


def task_identity(blocker: BlockerInput, missing_field: str) -> str:
    return sha256_json({
        "schema": "WOW_NRE_TASK_IDENTITY_V1",
        "sport": blocker.sport.upper(),
        "league": (blocker.league or "").upper(),
        "season": blocker.season or "",
        "market_route": blocker.market_route,
        "specialist_id": blocker.specialist_id,
        "candidate_id": blocker.candidate_id or "",
        "blocker_code": blocker.blocker_code,
        "missing_evidence_field": missing_field,
    })


def build_task(blocker: BlockerInput) -> ResearchTask:
    """Map one blocker to its research task, reusing the certification inventory."""
    if not blocker.sport or not blocker.market_route or not blocker.specialist_id or not blocker.blocker_code:
        raise ValueError("NRE_BLOCKER_IDENTITY_INCOMPLETE")
    # Only facts the caller observed are asserted; everything else stays unproven.
    assessment = assess(CertificationEvidence(
        sport=blocker.sport, surface=blocker.surface, candidate_ready=blocker.candidate_ready,
        notes=(blocker.blocker_code,)))
    spec = RESEARCH_SPECS.get(blocker.blocker_code)
    fields = {"sport": blocker.sport.upper(), "league": blocker.league or "", "season": blocker.season or "",
              "market": blocker.market_route}
    missing = spec.missing_evidence_field if spec else "unmapped"
    return ResearchTask(
        task_id=task_identity(blocker, missing),
        sport=blocker.sport.upper(),
        league=(blocker.league or None) and blocker.league.upper(),
        season=blocker.season,
        market_route=blocker.market_route,
        specialist_id=blocker.specialist_id,
        candidate_id=blocker.candidate_id,
        blocker_code=blocker.blocker_code,
        certification_status=assessment.status,
        certification_blockers=tuple(assessment.blockers),
        missing_evidence_field=missing,
        research_objective=(spec.objective.format(**fields) if spec else "NRE_BLOCKER_UNMAPPED"),
        required_source_type=spec.required_source_type if spec else "UNMAPPED",
        permitted_methods=spec.permitted_methods if spec else (),
        priority=spec.priority if spec else 5,
        blocker_fingerprint=sha256_json({"blocker_code": blocker.blocker_code, "context": dict(blocker.context),
                                         **({"leads": sorted(getattr(x, "source_id", str(x)) for x in blocker.leads)}
                                            if blocker.leads else {})}),
        researchable=spec is not None,
    )


def schedule_decision(task: ResearchTask, prior: Mapping[str, Any] | None,
                      attempts: Sequence[Mapping[str, Any]], *, now: datetime) -> ScheduleDecision:
    """Decide whether a task runs now. Unchanged blockers are never re-researched
    without new evidence, a freshness trigger, or an authorized retry."""
    now = _aware(now)
    if not task.researchable:
        return ScheduleDecision(False, "NOT_RESEARCHABLE")
    lease_until = (prior or {}).get("lease_expires_at")
    if lease_until and _aware(lease_until) > now:
        return ScheduleDecision(False, "LEASE_HELD", _aware(lease_until))
    if prior is None or not attempts:
        return ScheduleDecision(True, "NEW_TASK")
    if prior.get("blocker_fingerprint") != task.blocker_fingerprint:
        return ScheduleDecision(True, "NEW_EVIDENCE")
    ordered = sorted(attempts, key=lambda a: _aware(a["finished_at"]))
    last = ordered[-1]
    last_at = _aware(last["finished_at"])
    spec = RESEARCH_SPECS[task.blocker_code]
    if last.get("outcome") in TRANSIENT_OUTCOMES:
        recent = [a for a in ordered if a.get("outcome") in TRANSIENT_OUTCOMES
                  and _aware(a["finished_at"]) > now - timedelta(days=1)]
        if len(recent) >= MAX_RETRIES_PER_DAY:
            return ScheduleDecision(False, "RETRY_LIMIT_REACHED", last_at + timedelta(days=1))
        eligible = last_at + RETRY_BACKOFF[min(len(recent), len(RETRY_BACKOFF)) - 1 if recent else 0]
        if now >= eligible:
            return ScheduleDecision(True, "AUTHORIZED_RETRY")
        return ScheduleDecision(False, "RETRY_BACKOFF", eligible)
    refresh = spec.negative_refresh_after if last.get("outcome") == "NO_SOURCE_FOUND" else spec.refresh_after
    eligible = last_at + refresh
    if now >= eligible:
        return ScheduleDecision(True, "FRESHNESS_TRIGGER")
    return ScheduleDecision(False, "DEDUP_UNCHANGED", eligible)


def next_eligible_after(task: ResearchTask, outcome: str, finished_at: datetime) -> datetime | None:
    spec = RESEARCH_SPECS.get(task.blocker_code)
    if spec is None:
        return None
    if outcome in TRANSIENT_OUTCOMES:
        return finished_at + RETRY_BACKOFF[0]
    if outcome == "NO_SOURCE_FOUND":
        return finished_at + spec.negative_refresh_after
    return finished_at + spec.refresh_after


def remaining_blockers(task: ResearchTask, outcome: str) -> list[str]:
    """Research never clears the originating blocker; it adds what still stands in the way."""
    remaining = [task.blocker_code]
    if outcome == "CANDIDATE_VERIFIED_RIGHTS_REVIEW_REQUIRED":
        remaining.append("SOURCE_RIGHTS_INDEPENDENT_REVIEW_REQUIRED")
    elif outcome == "CANDIDATE_VERIFIED_PENDING_INDEPENDENT_REVIEW":
        remaining.append("SOURCE_INDEPENDENT_REVIEW_REQUIRED")
    elif outcome == "CONFLICTING_EVIDENCE":
        remaining.append("SOURCE_EVIDENCE_CONFLICT")
    elif outcome == "CANDIDATE_FOUND_UNVERIFIED":
        remaining.append("SOURCE_FRESHNESS_UNVERIFIED")
    elif outcome == "NO_SOURCE_FOUND":
        remaining.append("NO_CANDIDATE_SOURCE")
    return remaining


def task_row(task: ResearchTask, *, status: str, outcome: str, best_stage: int,
             evidence_refs: Sequence[str], attempts: int, last_attempt_at: datetime | None,
             next_eligible_at: datetime | None, now: datetime) -> dict[str, Any]:
    stage = PROGRESS_STAGE[outcome]
    row = task.row()
    row.pop("researchable")
    row.update({
        "status": status,
        "outcome": outcome,
        "progress_stage": stage,
        "best_progress_stage": max(stage, best_stage),
        "evidence_refs": list(evidence_refs),
        "remaining_blockers": remaining_blockers(task, outcome),
        "attempt_count": attempts,
        "last_attempt_at": last_attempt_at.isoformat() if last_attempt_at else None,
        "next_eligible_at": next_eligible_at.isoformat() if next_eligible_at else None,
        "updated_at": now.isoformat(),
        "evidence_only": INERT["evidence_only"],
        "prediction_authority": INERT["prediction_authority"],
        "can_execute": INERT["can_execute"],
    })
    return row
