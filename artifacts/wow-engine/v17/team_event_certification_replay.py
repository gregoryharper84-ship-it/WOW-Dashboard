"""Read-only V17 certification replay for team/event model-development lanes.

Certification is lane-specific. Production replay consumes exact candidate-bound
certification evidence receipts. This module never certifies, promotes, activates,
registers, publishes, ranks, or executes a probability.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from fastapi import FastAPI

from github_actions_oidc import scout_route_auth_dependency
from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS
from v17.team_event_model_development_manifest import development_lane

CAN_EXECUTE = False
CANDIDATE_TABLE = "wow_d1_candidate_artifacts"
EVIDENCE_TABLE = "wow_d1_certification_evidence_receipts"


@dataclass(frozen=True)
class CertificationReplayResult:
    sport: str
    status: str
    blockers: tuple[str, ...]
    candidate_id: str | None
    model_artifact_version: str | None
    research_screen_pass: bool | None
    source_review_status: str | None
    next_gate: str | None
    league: str | None = None
    model_family: str | None = None
    lane_id: str | None = None
    candidate_bound_evidence_pass: bool = False
    automatic_certification: bool = False
    automatic_promotion: bool = False
    probability_publishable: bool = False
    can_execute: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "sport": self.sport,
            "league": self.league,
            "model_family": self.model_family,
            "lane_id": self.lane_id,
            "status": self.status,
            "blockers": list(self.blockers),
            "candidate_id": self.candidate_id,
            "model_artifact_version": self.model_artifact_version,
            "research_screen_pass": self.research_screen_pass,
            "source_review_status": self.source_review_status,
            "candidate_bound_evidence_pass": self.candidate_bound_evidence_pass,
            "next_gate": self.next_gate,
            "automatic_certification": False,
            "automatic_promotion": False,
            "probability_publishable": False,
            "can_execute": False,
        }


def _lane_identity(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("sport") or "").strip().upper(),
        str(row.get("league") or row.get("sport") or "").strip().upper(),
        str(row.get("model_family") or "").strip().upper(),
    )


def _lane_id(row: Mapping[str, Any]) -> str:
    sport, league, family = _lane_identity(row)
    return f"{sport}:{league}:{family}" if family else f"{sport}:{league}"


def _evidence_identity(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("candidate_id") or "").strip().lower(),
        str(row.get("model_artifact_version") or "").strip(),
        str(row.get("training_dataset_hash") or "").strip().lower(),
        str(row.get("artifact_checksum") or "").strip().lower(),
    )


def _candidate_bound_passes(receipts: Sequence[Mapping[str, Any]]) -> set[tuple[str, str, str, str]]:
    passes: set[tuple[str, str, str, str]] = set()
    for row in receipts:
        if str(row.get("source_review_status") or "").strip().upper() != "PASS":
            continue
        if str(row.get("replay_status") or "").strip().upper() != "PASS":
            continue
        identity = _evidence_identity(row)
        if identity[0] and identity[1] and len(identity[2]) == 64 and len(identity[3]) == 64:
            passes.add(identity)
    return passes


def _latest_by_lane(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    for raw in rows:
        row = dict(raw)
        key = _lane_identity(row)
        if not key[0] or not key[2]:
            continue
        current = latest.get(key)
        if current is None or str(row.get("created_at") or "") > str(current.get("created_at") or ""):
            latest[key] = row
    return latest


def _artifact_identity_complete(row: Mapping[str, Any]) -> bool:
    checksum = str(row.get("artifact_checksum") or "").strip().lower()
    dataset_hash = str(row.get("training_dataset_hash") or "").strip().lower()
    training_sha = str(row.get("training_code_sha") or "").strip().lower()
    return bool(
        str(row.get("model_family") or "").strip()
        and str(row.get("model_artifact_version") or "").strip()
        and len(checksum) == 64
        and len(dataset_hash) == 64
        and len(training_sha) >= 7
    )


def _partitions_valid(row: Mapping[str, Any]) -> bool:
    try:
        return all(int(row.get(name) or 0) > 0 for name in ("training_rows", "calibration_rows", "test_rows"))
    except (TypeError, ValueError):
        return False


def assess_candidate(
    sport: str,
    candidate: Mapping[str, Any] | None,
    *,
    replay_evidence_pass: bool = False,
    source_review_evidence_pass: bool = False,
    candidate_bound_evidence_pass: bool = False,
) -> CertificationReplayResult:
    normalized = str(sport or "").strip().upper()
    lane = development_lane(normalized)
    next_gate = lane.next_gate if lane is not None else None
    league = None if candidate is None else str(candidate.get("league") or normalized).strip().upper() or None
    model_family = None if candidate is None else str(candidate.get("model_family") or "").strip() or None
    lane_id = None if candidate is None else _lane_id(candidate)

    if lane is not None and lane.status == "PRODUCTION_MODEL_PRESENT":
        return CertificationReplayResult(
            sport=normalized,
            status="PRODUCTION_MODEL_PRESENT",
            blockers=(),
            candidate_id=None if candidate is None else str(candidate.get("candidate_id") or "") or None,
            model_artifact_version=None if candidate is None else str(candidate.get("model_artifact_version") or "") or None,
            research_screen_pass=None if candidate is None else bool(candidate.get("research_screen_pass")),
            source_review_status=None if candidate is None else str(candidate.get("source_review_status") or "") or None,
            next_gate=next_gate,
            league=league,
            model_family=model_family,
            lane_id=lane_id,
            candidate_bound_evidence_pass=candidate_bound_evidence_pass,
        )

    if lane is not None and lane.status == "BUILD_REQUIRED":
        return CertificationReplayResult(
            sport=normalized,
            status="BUILD_REQUIRED",
            blockers=("FITTED_CANDIDATE_PIPELINE_REQUIRED",),
            candidate_id=None,
            model_artifact_version=None,
            research_screen_pass=None,
            source_review_status=None,
            next_gate=next_gate,
        )

    if candidate is None:
        return CertificationReplayResult(
            sport=normalized,
            status="CANDIDATE_EVIDENCE_MISSING",
            blockers=("CANDIDATE_ARTIFACT_MISSING",),
            candidate_id=None,
            model_artifact_version=None,
            research_screen_pass=None,
            source_review_status=None,
            next_gate=next_gate,
        )

    blockers: list[str] = []
    if candidate.get("can_execute") is not False:
        blockers.append("CANDIDATE_CAN_EXECUTE_FORBIDDEN")
    if candidate.get("probability_publishable") not in (None, False):
        blockers.append("CANDIDATE_PUBLICATION_FLAG_FORBIDDEN")
    if any(bool(candidate.get(name)) for name in ("automatic_certification", "automatic_promotion", "promoted", "active")):
        blockers.append("CANDIDATE_INERTNESS_VIOLATION")
    if str(candidate.get("lifecycle_state") or "").strip().upper() != "CANDIDATE":
        blockers.append("CANDIDATE_LIFECYCLE_INVALID")
    if not _artifact_identity_complete(candidate):
        blockers.append("CANDIDATE_ARTIFACT_IDENTITY_INCOMPLETE")
    if not _partitions_valid(candidate):
        blockers.append("CANDIDATE_PARTITIONS_INVALID")
    if candidate.get("research_screen_pass") is not True:
        blockers.append("RESEARCH_SCREEN_FAILED")

    source_review = str(candidate.get("source_review_status") or "REQUIRED").strip().upper()
    if source_review == "FAIL":
        blockers.append("SOURCE_REVIEW_FAILED")
    elif source_review != "PASS" and not source_review_evidence_pass:
        blockers.append("SOURCE_REVIEW_REQUIRED")

    if not replay_evidence_pass:
        blockers.append("PROSPECTIVE_OR_CERTIFICATION_REPLAY_EVIDENCE_REQUIRED")

    if "RESEARCH_SCREEN_FAILED" in blockers:
        status = "RESEARCH_SCREEN_FAILED"
    elif "SOURCE_REVIEW_FAILED" in blockers:
        status = "SOURCE_REVIEW_FAILED"
    elif "SOURCE_REVIEW_REQUIRED" in blockers:
        status = "SOURCE_REVIEW_PENDING"
    elif blockers:
        status = "CERTIFICATION_REPLAY_BLOCKED"
    else:
        status = "CERTIFICATION_REPLAY_PASS"

    return CertificationReplayResult(
        sport=normalized,
        status=status,
        blockers=tuple(dict.fromkeys(blockers)),
        candidate_id=str(candidate.get("candidate_id") or "") or None,
        model_artifact_version=str(candidate.get("model_artifact_version") or "") or None,
        research_screen_pass=bool(candidate.get("research_screen_pass")),
        source_review_status=source_review,
        next_gate=next_gate,
        league=league,
        model_family=model_family,
        lane_id=lane_id,
        candidate_bound_evidence_pass=candidate_bound_evidence_pass,
    )


def _aggregate_candidate_sport(sport: str, lane_rows: list[dict[str, Any]]) -> dict[str, Any]:
    priority = {
        "CERTIFICATION_REPLAY_PASS": 0,
        "SOURCE_REVIEW_PENDING": 1,
        "CERTIFICATION_REPLAY_BLOCKED": 2,
        "SOURCE_REVIEW_FAILED": 3,
        "RESEARCH_SCREEN_FAILED": 4,
        "CANDIDATE_EVIDENCE_MISSING": 5,
    }
    best = min(lane_rows, key=lambda row: priority.get(str(row.get("status")), 99))
    blockers = sorted({blocker for row in lane_rows for blocker in row.get("blockers", [])})
    return {
        "sport": sport,
        "status": best["status"],
        "blockers": blockers,
        "lane_count": len(lane_rows),
        "research_pass_lane_count": sum(row.get("research_screen_pass") is True for row in lane_rows),
        "replay_pass_lane_count": sum(row.get("status") == "CERTIFICATION_REPLAY_PASS" for row in lane_rows),
        "candidate_bound_evidence_pass_lane_count": sum(row.get("candidate_bound_evidence_pass") is True for row in lane_rows),
        "lanes": lane_rows,
        "next_gate": best.get("next_gate"),
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def build_certification_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    certification_evidence_receipts: Sequence[Mapping[str, Any]] | None = None,
    replay_evidence_by_lane: Mapping[str, bool] | None = None,
    replay_evidence_by_sport: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    latest = _latest_by_lane(rows)
    exact_passes = _candidate_bound_passes(certification_evidence_receipts or [])
    lane_evidence = {str(k).upper(): bool(v) for k, v in (replay_evidence_by_lane or {}).items()}
    sport_evidence = {str(k).upper(): bool(v) for k, v in (replay_evidence_by_sport or {}).items()}
    all_lane_results: list[dict[str, Any]] = []
    sport_results: list[dict[str, Any]] = []

    for sport in EXPECTED_TEAM_EVENT_SPORTS:
        lane = development_lane(sport)
        sport_candidates = [row for key, row in latest.items() if key[0] == sport]
        if lane is not None and lane.status == "PRODUCTION_MODEL_PRESENT":
            candidate = max(sport_candidates, key=lambda row: str(row.get("created_at") or ""), default=None)
            sport_results.append(assess_candidate(sport, candidate).as_dict())
            continue
        if lane is not None and lane.status == "BUILD_REQUIRED":
            sport_results.append(assess_candidate(sport, None).as_dict())
            continue
        if not sport_candidates:
            sport_results.append(assess_candidate(sport, None).as_dict())
            continue

        lane_rows: list[dict[str, Any]] = []
        for candidate in sorted(sport_candidates, key=lambda row: _lane_id(row)):
            lid = _lane_id(candidate)
            exact_pass = _evidence_identity(candidate) in exact_passes
            helper_replay_pass = lane_evidence.get(lid.upper(), sport_evidence.get(sport, False))
            assessed = assess_candidate(
                sport,
                candidate,
                replay_evidence_pass=exact_pass or helper_replay_pass,
                source_review_evidence_pass=exact_pass,
                candidate_bound_evidence_pass=exact_pass,
            ).as_dict()
            lane_rows.append(assessed)
            all_lane_results.append(assessed)
        sport_results.append(_aggregate_candidate_sport(sport, lane_rows))

    return {
        "status": "CERTIFICATION_REPLAY_COMPLETE",
        "sports": sport_results,
        "candidate_lanes": all_lane_results,
        "summary": {
            "cataloged_sports": len(sport_results),
            "candidate_lanes": len(all_lane_results),
            "production_model_present": sum(1 for row in sport_results if row["status"] == "PRODUCTION_MODEL_PRESENT"),
            "sports_with_replay_pass_lane": sum(1 for row in sport_results if row["status"] == "CERTIFICATION_REPLAY_PASS"),
            "research_pass_candidate_lanes": sum(row.get("research_screen_pass") is True for row in all_lane_results),
            "candidate_bound_evidence_pass_lanes": sum(row.get("candidate_bound_evidence_pass") is True for row in all_lane_results),
            "blocked_candidate_lanes": sum(row.get("status") != "CERTIFICATION_REPLAY_PASS" for row in all_lane_results),
        },
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def run_certification_replay(db: Any) -> dict[str, Any]:
    result = (
        db.table(CANDIDATE_TABLE)
        .select(
            "candidate_id,created_at,sport,league,model_family,model_artifact_version,training_dataset_hash,"
            "training_code_sha,artifact_checksum,training_rows,calibration_rows,test_rows,"
            "research_screen_pass,source_review_status,lifecycle_state,promoted,active,"
            "automatic_certification,automatic_promotion,probability_publishable,can_execute"
        )
        .order("created_at", desc=True)
        .limit(2000)
        .execute()
    )
    rows = list(getattr(result, "data", None) or [])
    try:
        evidence_result = (
            db.table(EVIDENCE_TABLE)
            .select(
                "candidate_id,model_artifact_version,training_dataset_hash,artifact_checksum,"
                "source_review_status,replay_status,created_at"
            )
            .order("created_at", desc=True)
            .limit(4000)
            .execute()
        )
        receipts = list(getattr(evidence_result, "data", None) or [])
    except Exception:
        receipts = []
    return build_certification_report(rows, certification_evidence_receipts=receipts)


def install_team_event_certification_replay_route(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
) -> None:
    path = "/internal/v17/team-event-certification-replay"
    if any(getattr(route, "path", None) == path for route in app.router.routes):
        return

    @app.post(
        path,
        dependencies=[scout_route_auth_dependency(auth_dependency)],
        operation_id="runWowV17TeamEventCertificationReplay",
    )
    def replay() -> dict[str, Any]:
        return run_certification_replay(db_client_fn())


__all__ = [
    "CAN_EXECUTE",
    "CertificationReplayResult",
    "assess_candidate",
    "build_certification_report",
    "install_team_event_certification_replay_route",
    "run_certification_replay",
]
