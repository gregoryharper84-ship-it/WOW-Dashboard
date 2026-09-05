"""Fail-closed replay audit for WNBA fitted-model lifecycle review.

This module compares a fresh deterministic replay against the checked candidate
artifacts and the governed source manifest. Passing this audit means only that
source rights/provenance and fitted artifact reproduction are ready for a
separate lifecycle review. It never activates a model, publishes a probability,
or grants execution authority.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping, Sequence

PROVIDER = "SPORTSDATAVERSE_WNBA_STATS"
REQUIRED_LICENSE_ID = "CC-BY-4.0"
EXPECTED_STATS = {
    "POINTS",
    "REBOUNDS",
    "ASSISTS",
    "THREE_POINTERS_MADE",
}
READY_FOR_LIFECYCLE_REVIEW = "READY_FOR_LIFECYCLE_REVIEW"
CERTIFICATION_REPLAY_BLOCKED = "CERTIFICATION_REPLAY_BLOCKED"


class WNBACertificationReplayError(ValueError):
    pass


def _artifact_index(items: Sequence[Mapping[str, Any]], *, label: str) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for item in items:
        stat = str(item.get("stat_type") or "").strip().upper()
        if not stat:
            raise WNBACertificationReplayError(f"{label}: artifact stat_type missing")
        if stat in indexed:
            raise WNBACertificationReplayError(f"{label}: duplicate artifact route {stat}")
        indexed[stat] = item
    return indexed


def _manifest_source(manifest: Mapping[str, Any]) -> Mapping[str, Any] | None:
    matches = [
        item
        for item in manifest.get("sources", [])
        if str(item.get("sport") or "").upper() == "WNBA"
        and str(item.get("provider") or "").upper() == PROVIDER
    ]
    if len(matches) > 1:
        raise WNBACertificationReplayError("duplicate SportsDataverse WNBA source rows")
    return matches[0] if matches else None


def _comparable_artifact(item: Mapping[str, Any]) -> dict[str, Any]:
    """Return deterministic fitted content, excluding run-specific code provenance."""
    comparable = deepcopy(dict(item))
    comparable.pop("training_code_sha", None)
    return comparable


def _append_unique(blockers: list[str], code: str) -> None:
    if code not in blockers:
        blockers.append(code)


def _validate_artifact_invariants(
    item: Mapping[str, Any],
    *,
    replay: bool,
    blockers: list[str],
) -> None:
    stat = str(item.get("stat_type") or "UNKNOWN").upper()
    prefix = f"WNBA_{stat}"
    if str(item.get("sport") or "").upper() != "WNBA":
        _append_unique(blockers, f"{prefix}_SPORT_MISMATCH")
    if item.get("can_execute") is not False:
        _append_unique(blockers, f"{prefix}_EXECUTION_MUST_REMAIN_DISABLED")
    if item.get("probability_publishable") is not False:
        _append_unique(blockers, f"{prefix}_PREMATURE_PROBABILITY_PUBLICATION")
    if item.get("active") is not False or item.get("promoted") is not False:
        _append_unique(blockers, f"{prefix}_PREMATURE_LIFECYCLE_PROMOTION")
    if str(item.get("lifecycle_state") or "").upper() != "CANDIDATE":
        _append_unique(blockers, f"{prefix}_EXPECTED_CANDIDATE_STATE")
    if item.get("certification_eligible") is not True:
        _append_unique(blockers, f"{prefix}_NOT_CERTIFICATION_ELIGIBLE")

    metrics = item.get("validation_metrics")
    if not isinstance(metrics, Mapping):
        _append_unique(blockers, f"{prefix}_VALIDATION_METRICS_MISSING")
    else:
        if str(metrics.get("validation_status") or "").upper() != "PASS":
            _append_unique(blockers, f"{prefix}_VALIDATION_NOT_PASS")
        if list(metrics.get("blockers") or []):
            _append_unique(blockers, f"{prefix}_VALIDATION_BLOCKERS_PRESENT")
        if metrics.get("can_execute") is not False:
            _append_unique(blockers, f"{prefix}_METRICS_EXECUTION_MUST_REMAIN_DISABLED")
        if metrics.get("probability_publishable") is not False:
            _append_unique(blockers, f"{prefix}_METRICS_PREMATURE_PUBLICATION")

    payload = item.get("artifact_payload")
    if not isinstance(payload, Mapping) or not str(payload.get("source_sha256") or "").strip():
        _append_unique(blockers, f"{prefix}_PINNED_SOURCE_HASH_MISSING")

    if replay:
        training_code_sha = str(item.get("training_code_sha") or "").strip()
        if not training_code_sha or training_code_sha == "UNRESOLVED_TRAINING_CODE_SHA":
            _append_unique(blockers, f"{prefix}_REPLAY_CODE_SHA_UNRESOLVED")


def audit_wnba_certification_replay(
    *,
    checked_artifacts: Sequence[Mapping[str, Any]],
    replay_artifacts: Sequence[Mapping[str, Any]],
    source_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Audit source rights and deterministic artifact reproduction.

    A PASS is intentionally named ``READY_FOR_LIFECYCLE_REVIEW`` rather than
    certified/active. The V17 lifecycle/terminal authorities remain downstream.
    """
    blockers: list[str] = []

    if source_manifest.get("schema_version") != "WOW_HISTORICAL_SOURCE_MANIFEST_V1":
        _append_unique(blockers, "WNBA_SOURCE_MANIFEST_VERSION_INVALID")
    if source_manifest.get("can_execute") is not False:
        _append_unique(blockers, "WNBA_SOURCE_MANIFEST_EXECUTION_FORBIDDEN")

    source = _manifest_source(source_manifest)
    if source is None:
        _append_unique(blockers, "WNBA_SOURCE_NOT_REGISTERED")
    else:
        if str(source.get("evidence_domain") or "").upper() != "SPORTING":
            _append_unique(blockers, "WNBA_SOURCE_DOMAIN_NOT_SPORTING")
        if str(source.get("rights_state") or "").upper() != "V17_APPROVED":
            _append_unique(blockers, "WNBA_SOURCE_RIGHTS_NOT_APPROVED")
        if source.get("credential_required") is not False:
            _append_unique(blockers, "WNBA_SOURCE_UNEXPECTED_CREDENTIAL_REQUIREMENT")
        if str(source.get("license_id") or "").upper() != REQUIRED_LICENSE_ID:
            _append_unique(blockers, "WNBA_SOURCE_LICENSE_NOT_PINNED")
        if source.get("attribution_required") is not True:
            _append_unique(blockers, "WNBA_SOURCE_ATTRIBUTION_NOT_ENFORCED")
        if not str(source.get("license_url") or "").strip():
            _append_unique(blockers, "WNBA_SOURCE_LICENSE_URL_MISSING")
        if source.get("grants_model_capability") is not False:
            _append_unique(blockers, "WNBA_SOURCE_CANNOT_GRANT_MODEL_CAPABILITY")

    checked = _artifact_index(checked_artifacts, label="checked")
    replay = _artifact_index(replay_artifacts, label="replay")
    if set(checked) != EXPECTED_STATS:
        _append_unique(blockers, "WNBA_CHECKED_ROUTE_SET_INCOMPLETE")
    if set(replay) != EXPECTED_STATS:
        _append_unique(blockers, "WNBA_REPLAY_ROUTE_SET_INCOMPLETE")

    for item in checked.values():
        _validate_artifact_invariants(item, replay=False, blockers=blockers)
    for item in replay.values():
        _validate_artifact_invariants(item, replay=True, blockers=blockers)

    matched_routes: list[str] = []
    for stat in sorted(EXPECTED_STATS & set(checked) & set(replay)):
        checked_item = checked[stat]
        replay_item = replay[stat]
        if _comparable_artifact(checked_item) != _comparable_artifact(replay_item):
            _append_unique(blockers, f"WNBA_{stat}_REPLAY_MISMATCH")
            continue
        matched_routes.append(stat)

    replay_code_shas = sorted(
        {
            str(item.get("training_code_sha") or "").strip()
            for item in replay.values()
            if str(item.get("training_code_sha") or "").strip()
            and str(item.get("training_code_sha")) != "UNRESOLVED_TRAINING_CODE_SHA"
        }
    )
    if len(replay_code_shas) != 1:
        _append_unique(blockers, "WNBA_REPLAY_CODE_SHA_NOT_SINGLETON")

    ready = not blockers
    return {
        "sport": "WNBA",
        "source_provider": PROVIDER,
        "source_license_id": source.get("license_id") if source else None,
        "source_attribution_required": source.get("attribution_required") if source else None,
        "expected_routes": sorted(EXPECTED_STATS),
        "matched_routes": matched_routes,
        "replay_training_code_sha": replay_code_shas[0] if len(replay_code_shas) == 1 else None,
        "artifact_replay_match": len(matched_routes) == len(EXPECTED_STATS),
        "certification_replay_status": READY_FOR_LIFECYCLE_REVIEW if ready else CERTIFICATION_REPLAY_BLOCKED,
        "ready_for_lifecycle_review": ready,
        "blockers": blockers,
        "runtime_model_status": "MODEL_UNAVAILABLE",
        "probability_publishable": False,
        "can_execute": False,
    }
