"""Research-only V17 model factory: reuse sport-owned maintenance, never certify.

Stage 1 of a common factory. The production certified specialist, fitted
model, calibration, immutable review and terminal path remain independent.
No HTTP endpoint, scheduler, model routing, probability fitting, or approval
operation is introduced by this module.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any, Mapping

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
FACTORY_VERSION = "V17_RESEARCH_EVIDENCE_FACTORY_V1"
SUPPORTED_RECIPES = frozenset({("NBA", "WINNER"), ("NHL", "WINNER")})
_INERT_FLAGS = ("can_execute", "probability_publishable", "automatic_promotion", "automatic_certification", "promotion_attempted")


def _run_nba(db: Any, seasons: tuple[int, ...] | None) -> Mapping[str, Any]:
    from v17.basketball_model_maintenance import run_basketball_model_maintenance

    args: dict[str, Any] = {"sports": ("NBA",)}
    if seasons is not None:
        args["seasons"] = seasons
    return run_basketball_model_maintenance(db, **args)


def _run_nhl(db: Any, seasons: tuple[int, ...] | None, source_head_sha: str) -> Mapping[str, Any]:
    from v17.nhl_model_maintenance import run_nhl_model_maintenance

    args: dict[str, Any] = {"training_code_sha": source_head_sha}
    if seasons is not None:
        args["start_years"] = seasons
    return run_nhl_model_maintenance(db, **args)


def _receipt(sport: str, market: str, sha: str, *, status: str, code: str | None = None,
             native_status: str | None = None, evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    data = {
        "factory_version": FACTORY_VERSION,
        "sport": sport,
        "market": market,
        "source_head_sha": sha,
        "source_sha_verification": "CALLER_DECLARED_NOT_PRODUCTION_ATTESTED",
        "stage": "CANDIDATE_RESEARCH_EVIDENCE",
        "status": status,
        "code": code,
        "native_status": native_status,
        "native_evidence": dict(evidence or {}),
        "certification_status": "NOT_CERTIFIED",
        "independent_review_status": "NOT_PERFORMED",
        "production_acceptance_status": "NOT_PERFORMED",
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }
    data["evidence_sha256"] = sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return data


def _unsafe_native(result: Mapping[str, Any]) -> bool:
    """Reject authorization flags at every nested native result boundary."""
    def walk(value: Any) -> bool:
        if isinstance(value, Mapping):
            if any(value[k] is not False for k in _INERT_FLAGS if k in value):
                return True
            return any(walk(child) for child in value.values())
        if isinstance(value, (tuple, list)):
            return any(walk(child) for child in value)
        return False

    return walk(result)


def prepare_candidate_evidence(db: Any, *, sport: str, market: str,
                               source_head_sha: str,
                               seasons: tuple[int, ...] | None = None) -> dict[str, Any]:
    """Call native maintenance and produce *research* evidence, not a model approval.

    Native NBA hydration/feature/replay checks and native NHL candidate/preflight
    remain the respective scientific authorities. This layer does not interpret
    their probabilities, calibrate, or promote an artifact. No implied-odds
    fallback is possible, and every error remains fail closed.
    """
    s, m = str(sport or "").strip().upper(), str(market or "").strip().upper()
    sha = str(source_head_sha or "").strip().lower()
    if (s, m) not in SUPPORTED_RECIPES:
        return _receipt(s, m, sha, status="HOLD", code="MODEL_FACTORY_RECIPE_UNSUPPORTED")
    if (db is None or re.fullmatch(r"[0-9a-f]{40}", sha) is None or
            (seasons is not None and (
                not isinstance(seasons, tuple) or not seasons or
                any(type(y) is not int or y < 1900 or y > 2200 for y in seasons) or
                tuple(sorted(set(seasons))) != seasons
            ))):
        return _receipt(s, m, sha, status="HOLD", code="MODEL_FACTORY_INPUT_INVALID")
    try:
        result = _run_nba(db, seasons) if s == "NBA" else _run_nhl(db, seasons, sha)
    except Exception:  # noqa: BLE001 - do not leak provider error text or credentials
        return _receipt(s, m, sha, status="HOLD", code="MODEL_FACTORY_MAINTENANCE_EXCEPTION")
    if not isinstance(result, Mapping):
        return _receipt(s, m, sha, status="HOLD", code="MODEL_FACTORY_NATIVE_RESULT_INVALID")
    if _unsafe_native(result):
        return _receipt(s, m, sha, status="HOLD", code="MODEL_FACTORY_NATIVE_INERTNESS_VIOLATION")
    native_status = result.get("status")
    if not isinstance(native_status, str):
        return _receipt(s, m, sha, status="HOLD", code="MODEL_FACTORY_NATIVE_RESULT_INVALID")

    if s == "NBA":
        rows = result.get("rows")
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], Mapping):
            return _receipt(s, m, sha, status="HOLD", code="MODEL_FACTORY_NATIVE_RESULT_INVALID", native_status=native_status)
        row = rows[0]
        replay = row.get("training_replay")
        replay = replay if isinstance(replay, Mapping) else {}
        provenance = replay.get("provenance_preflight")
        provenance = provenance if isinstance(provenance, Mapping) else {}
        freshness = replay.get("freshness_preflight")
        freshness = freshness if isinstance(freshness, Mapping) else {}
        evidence = {
            "replay_status": replay.get("persisted_status") if isinstance(replay.get("persisted_status"), str) else None,
            "rows_updated": result.get("rows_updated") if type(result.get("rows_updated")) is int else None,
            "row_status": row.get("status") if isinstance(row.get("status"), str) else None,
            "hydration_status": row.get("hydration_status") if isinstance(row.get("hydration_status"), str) else None,
            "model_evidence_mode": "BASKETBALL_SHADOW_REPLAY",
        }
        if (native_status == "COMPLETE" and result.get("rows_updated") == 1
                and row.get("sport") == "NBA" and row.get("status") == "SHADOW_EVIDENCE_UPDATED"
                and replay.get("persisted_status") == "SHADOW"
                and provenance.get("provenance_complete") is True
                and freshness.get("freshness_status") == "PASS"):
            return _receipt(s, m, sha, status="RESEARCH_EVIDENCE_PREPARED", native_status=native_status, evidence=evidence)
        code = row.get("code") if isinstance(row.get("code"), str) else "MODEL_FACTORY_NATIVE_HOLD"
        return _receipt(s, m, sha, status="HOLD", code=code, native_status=native_status, evidence=evidence)

    persistence = result.get("persistence")
    persistence = persistence if isinstance(persistence, Mapping) else {}
    candidate = persistence.get("candidate")
    candidate = candidate if isinstance(candidate, Mapping) else {}
    evidence = {
        "candidate_id": candidate.get("candidate_id") if isinstance(candidate.get("candidate_id"), str) else None,
        "model_artifact_version": candidate.get("model_artifact_version") if isinstance(candidate.get("model_artifact_version"), str) else None,
        "model_evidence_mode": "NHL_D1_CANDIDATE",
        "source_event_n": result.get("source_event_n") if type(result.get("source_event_n")) is int else None,
        "training_feature_n": result.get("training_feature_n") if type(result.get("training_feature_n")) is int else None,
        "model_family": result.get("model_family") if isinstance(result.get("model_family"), str) else None,
        "source_review_status": result.get("source_review_status") if isinstance(result.get("source_review_status"), str) else None,
        "research_screen_pass": result.get("research_screen_pass") is True,
    }
    if native_status == "CANDIDATE_EVIDENCE_UPDATED" and result.get("sport") == "NHL":
        preflight = result.get("candidate_preflight")
        if (
            not isinstance(preflight, Mapping)
            or preflight.get("status") != "NHL_CANDIDATE_PREFLIGHT_READY"
            or type(result.get("source_event_n")) is not int
            or result["source_event_n"] <= 0
            or type(result.get("training_feature_n")) is not int
            or result["training_feature_n"] <= 0
            or preflight.get("source_event_n") != result["source_event_n"]
            or preflight.get("training_feature_n") != result["training_feature_n"]
            or not isinstance(preflight.get("training_dataset_hash"), str)
            or re.fullmatch(r"[0-9a-f]{64}", preflight["training_dataset_hash"]) is None
            or persistence.get("status") != "D1_CANDIDATE_PACKAGE_PERSISTED"
            or not isinstance(candidate.get("candidate_id"), str)
            or not candidate["candidate_id"].strip()
            or not isinstance(candidate.get("model_artifact_version"), str)
            or not candidate["model_artifact_version"].strip()
            or candidate.get("lifecycle_state") != "CANDIDATE"
        ):
            return _receipt(s, m, sha, status="HOLD", code="MODEL_FACTORY_NATIVE_RESULT_INVALID", native_status=native_status, evidence=evidence)
        if result.get("research_screen_pass") is not True:
            return _receipt(s, m, sha, status="HOLD", code="RESEARCH_SCREEN_FAILED", native_status=native_status, evidence=evidence)
        return _receipt(s, m, sha, status="RESEARCH_EVIDENCE_PREPARED", native_status=native_status, evidence=evidence)
    code = result.get("code") if isinstance(result.get("code"), str) else "MODEL_FACTORY_NATIVE_HOLD"
    return _receipt(s, m, sha, status="HOLD", code=code, native_status=native_status, evidence=evidence)


__all__ = ["CAN_EXECUTE", "PROBABILITY_PUBLISHABLE", "SUPPORTED_RECIPES", "prepare_candidate_evidence"]
