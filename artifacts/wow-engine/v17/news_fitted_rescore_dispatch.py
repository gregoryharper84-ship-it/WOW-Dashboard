"""Dispatch a news-triggered fitted specialist rescore request.

Consumes ``NEWS_RESEARCH_RESCORE_REQUESTED`` research handoffs only. Builds a
permitted feature snapshot delta from independently reviewed material facts,
invokes an injectable controlling-specialist scorer, and freezes an immutable
receipt. Never sets can_execute=true, never invents probability when the scorer
is unavailable, and never mutates a prior pregame receipt in place.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

CAN_EXECUTE = False
SCHEMA = "wow.v17.news_fitted_rescore_receipt.v1"
ScoreFn = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def _utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc)
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None or dt.utcoffset() is None:
        return None
    return dt.astimezone(timezone.utc)


def _hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _prop_target(candidate: Mapping[str, Any]) -> dict[str, Any] | None:
    """Extract the exact prop target when this candidate is a proposition row."""
    evidence = candidate.get("market_evidence")
    if isinstance(evidence, list):
        evidence = next((row for row in evidence if isinstance(row, Mapping)), None)
    if not isinstance(evidence, Mapping):
        return None
    participant = str(evidence.get("description") or evidence.get("participant") or "").strip()
    market_key = str(evidence.get("market_key") or evidence.get("stat_type") or "").strip()
    direction = str(evidence.get("outcome_name") or evidence.get("direction") or "").strip().upper()
    line = evidence.get("point", evidence.get("line"))
    if not participant and not market_key and line is None and not direction:
        return None
    target = {
        "participant": participant or None,
        "market_key": market_key or None,
        "direction": direction or None,
        "line": float(line) if isinstance(line, (int, float)) and not isinstance(line, bool) else None,
    }
    if all(value is None for value in target.values()):
        return None
    return target


def build_news_feature_snapshot(
    baseline_features: Mapping[str, Any] | None,
    news_context: list[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Build a permitted feature snapshot including material reviewed news facts."""
    baseline = dict(baseline_features or {})
    material = []
    for row in news_context or []:
        if not isinstance(row, Mapping):
            continue
        if row.get("material_change") is not True:
            continue
        material.append({
            "evidence_kind": row.get("evidence_kind"),
            "subject_id": row.get("subject_id"),
            "fact_value": row.get("fact_value"),
            "source_class": row.get("source_class"),
            "source_group": row.get("source_group"),
            "source_url": row.get("source_url"),
            "independent_review_ref": row.get("independent_review_ref"),
            "publisher_published_at_utc": row.get("publisher_published_at_utc"),
            "independently_reviewed_at_utc": row.get("independently_reviewed_at_utc"),
        })
    material = sorted(
        material,
        key=lambda r: (
            str(r.get("evidence_kind") or ""),
            str(r.get("subject_id") or ""),
            str(r.get("source_group") or ""),
            str(r.get("source_url") or ""),
        ),
    )
    snapshot = {
        "schema_version": "wow.v17.news_permitted_feature_snapshot.v1",
        "baseline_features": baseline,
        "material_news_facts": material,
        "news_fact_count": len(material),
        "prediction_authority": False,
        "can_execute": False,
    }
    snapshot["feature_snapshot_hash"] = _hash(snapshot)
    return snapshot


def _missing_fitted_scorer(_request: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "code": "MODEL_UNAVAILABLE",
        "blockers": ["NEWS_FITTED_SCORER_NOT_BOUND"],
        "calibrated_probability": None,
        "calibrated_lower_bound": None,
        "model_version": None,
        "prediction_authority": False,
        "can_execute": False,
    }


def dispatch_news_rescore(
    candidate: Mapping[str, Any],
    *,
    score_fn: ScoreFn | None = None,
    observed_at: datetime | None = None,
    prior_feature_snapshot: Mapping[str, Any] | None = None,
    baseline_features: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Attempt one news-triggered controlling-specialist rescore for a candidate.

    ``model_rescore_verified`` becomes true only when a genuine scorer response
    is returned with a changed permitted feature hash and no invented authority.
    """
    now = observed_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("NEWS_RESCORE_TIME_MUST_BE_AWARE")
    now = now.astimezone(timezone.utc)

    handoff = candidate.get("news_research_handoff")
    handoff = handoff if isinstance(handoff, Mapping) else {}
    event_start = _utc(
        candidate.get("canonical_identity_event_start_utc")
        or candidate.get("event_start_utc")
        or candidate.get("commence_time")
        or handoff.get("event_start_utc")
    )
    official_event_id = str(candidate.get("official_event_id") or "").strip()
    provider_event_id = str(candidate.get("provider_event_id") or "").strip()
    sport_key = str(candidate.get("sport_key") or "").strip()
    route = str(
        candidate.get("controlling_specialist_route")
        or candidate.get("route")
        or ""
    ).strip()

    receipt: dict[str, Any] = {
        "schema_version": SCHEMA,
        "sport_key": sport_key or None,
        "official_event_id": official_event_id or None,
        "provider_event_id": provider_event_id or None,
        "controlling_specialist_route": route or None,
        "research_status": handoff.get("research_status"),
        "research_handoff_sha256": handoff.get("receipt_sha256"),
        "source_receipt_sha256": handoff.get("source_receipt_sha256"),
        "observed_at_utc": now.isoformat(),
        "event_start_utc": event_start.isoformat() if event_start else None,
        "before_feature_snapshot_hash": None,
        "after_feature_snapshot_hash": None,
        "feature_snapshot_changed": False,
        "fitted_model_version": None,
        "scoring_response_code": None,
        "calibrated_probability": None,
        "calibrated_lower_bound": None,
        "scoring_attempted": False,
        "model_rescore_verified": False,
        "prediction_authority": False,
        "can_execute": False,
        "dispatch_status": "NEWS_RESCORE_NOT_ATTEMPTED",
        "blockers": [],
    }

    def finish(status: str, *, blockers: list[str] | None = None) -> dict[str, Any]:
        out = dict(receipt)
        out["dispatch_status"] = status
        if blockers:
            out["blockers"] = list(blockers)
        out["receipt_sha256"] = _hash(out)
        return out

    if candidate.get("official_event_identity_verified") is not True:
        return finish("NEWS_RESCORE_IDENTITY_UNVERIFIED", blockers=["OFFICIAL_EVENT_IDENTITY_UNVERIFIED"])
    if (
        candidate.get("canonical_identity_status") == "PROVIDER_EVENT_ID_UNVERIFIED"
        or (
            provider_event_id
            and provider_event_id == official_event_id
            and candidate.get("official_event_identity_verified") is not True
        )
    ):
        return finish("NEWS_RESCORE_FORGED_OR_PROVIDER_ID", blockers=["PROVIDER_ID_NOT_OFFICIAL"])
    if not official_event_id or not sport_key or event_start is None:
        return finish("NEWS_RESCORE_IDENTITY_INCOMPLETE", blockers=["EVENT_IDENTITY_INCOMPLETE"])
    if now >= event_start:
        return finish("NEWS_RESCORE_EVENT_ALREADY_STARTED", blockers=["EVENT_ALREADY_STARTED"])
    if handoff.get("research_status") != "NEWS_RESEARCH_RESCORE_REQUESTED":
        return finish(
            "NEWS_RESCORE_NOT_REQUESTED",
            blockers=["RESEARCH_RESCORE_NOT_REQUESTED"],
        )
    if handoff.get("research_rescore_requested") is not True:
        return finish(
            "NEWS_RESCORE_NOT_REQUESTED",
            blockers=["RESEARCH_RESCORE_FLAG_FALSE"],
        )
    if handoff.get("can_execute") is not False or handoff.get("prediction_authority") is not False:
        return finish("NEWS_RESCORE_AUTHORITY_INVALID", blockers=["HANDOFF_AUTHORITY_INVALID"])
    if candidate.get("can_execute") is not False:
        return finish("NEWS_RESCORE_CANDIDATE_AUTHORITY_INVALID", blockers=["CANDIDATE_CAN_EXECUTE_NOT_FALSE"])

    news_context = handoff.get("news_context")
    if not isinstance(news_context, list) or not any(
        isinstance(row, Mapping) and row.get("material_change") is True for row in news_context
    ):
        return finish("NEWS_RESCORE_NO_MATERIAL_FACTS", blockers=["MATERIAL_NEWS_FACTS_MISSING"])

    prior = prior_feature_snapshot if isinstance(prior_feature_snapshot, Mapping) else {
        "schema_version": "wow.v17.news_permitted_feature_snapshot.v1",
        "baseline_features": dict(baseline_features or {}),
        "material_news_facts": [],
        "news_fact_count": 0,
        "prediction_authority": False,
        "can_execute": False,
    }
    if "feature_snapshot_hash" not in prior:
        prior = dict(prior)
        prior["feature_snapshot_hash"] = _hash(prior)
    after = build_news_feature_snapshot(baseline_features or prior.get("baseline_features"), news_context)
    receipt["before_feature_snapshot_hash"] = prior.get("feature_snapshot_hash")
    receipt["after_feature_snapshot_hash"] = after["feature_snapshot_hash"]
    receipt["feature_snapshot_changed"] = (
        receipt["before_feature_snapshot_hash"] != receipt["after_feature_snapshot_hash"]
    )
    if not receipt["feature_snapshot_changed"]:
        return finish("NEWS_RESCORE_FEATURE_UNCHANGED", blockers=["FEATURE_SNAPSHOT_UNCHANGED"])

    prop_target = _prop_target(candidate)
    request = {
        "sport_key": sport_key,
        "official_event_id": official_event_id,
        "provider_event_id": provider_event_id or None,
        "event_start_utc": event_start.isoformat(),
        "home_team": candidate.get("home_team"),
        "away_team": candidate.get("away_team"),
        "controlling_specialist_route": route or None,
        "prop_target": prop_target,
        "feature_snapshot": after,
        "feature_snapshot_hash": after["feature_snapshot_hash"],
        "prior_feature_snapshot_hash": prior.get("feature_snapshot_hash"),
        "news_research_handoff_sha256": handoff.get("receipt_sha256"),
        "source_receipt_sha256": handoff.get("source_receipt_sha256"),
        "material_news_facts": after["material_news_facts"],
        "prediction_authority": False,
        "can_execute": False,
    }
    if prop_target is not None:
        receipt["prop_target"] = prop_target
    scorer = score_fn or _missing_fitted_scorer
    receipt["scoring_attempted"] = True
    try:
        response = scorer(request)
    except Exception as exc:  # noqa: BLE001 - preserve typed scorer failure
        receipt["scoring_response_code"] = "MODEL_SCORER_FAILED"
        return finish(
            "NEWS_RESCORE_SCORER_FAILED",
            blockers=["MODEL_SCORER_FAILED", type(exc).__name__],
        )

    if not isinstance(response, Mapping):
        receipt["scoring_response_code"] = "MODEL_OUTPUT_INVALID"
        return finish("NEWS_RESCORE_OUTPUT_INVALID", blockers=["MODEL_OUTPUT_INVALID"])

    code = str(response.get("code") or response.get("status") or "").strip() or None
    receipt["scoring_response_code"] = code
    receipt["fitted_model_version"] = (
        str(response.get("model_version") or "").strip() or None
    )
    if response.get("can_execute") is True or response.get("prediction_authority") is True:
        return finish(
            "NEWS_RESCORE_AUTHORITY_REJECTED",
            blockers=["SCORER_CLAIMED_EXECUTION_OR_AUTHORITY"],
        )

    if code in {None, ""} and response.get("ok") is not True:
        return finish("NEWS_RESCORE_OUTPUT_INVALID", blockers=["MODEL_OUTPUT_INVALID"])

    if code == "MODEL_UNAVAILABLE" or response.get("ok") is False and code == "MODEL_UNAVAILABLE":
        return finish("NEWS_RESCORE_MODEL_UNAVAILABLE", blockers=["MODEL_UNAVAILABLE"])
    if code == "MODEL_INPUTS_INSUFFICIENT":
        return finish("NEWS_RESCORE_INPUTS_INSUFFICIENT", blockers=["MODEL_INPUTS_INSUFFICIENT"])
    if code in {"MODEL_SCORER_FAILED", "MODEL_OUTPUT_INVALID"}:
        return finish(f"NEWS_RESCORE_{code}", blockers=[code])

    # Accept only an explicit successful scoring completion.
    success = response.get("ok") is True or code in {
        "SCORED",
        "MODEL_SCORED",
        "FITTED_SCORE_COMPLETE",
        "NEWS_FITTED_RESCORE_COMPLETE",
    }
    if not success:
        return finish(
            "NEWS_RESCORE_NOT_VERIFIED",
            blockers=[code or "SCORER_RESPONSE_NOT_SUCCESS"],
        )
    if not receipt["fitted_model_version"]:
        return finish("NEWS_RESCORE_MODEL_VERSION_MISSING", blockers=["MODEL_VERSION_MISSING"])

    calibrated = response.get("calibrated_probability")
    lower = response.get("calibrated_lower_bound")
    if calibrated is not None:
        if not isinstance(calibrated, (int, float)) or isinstance(calibrated, bool) or not 0 < float(calibrated) < 1:
            return finish("NEWS_RESCORE_PROBABILITY_INVALID", blockers=["CALIBRATED_PROBABILITY_INVALID"])
        receipt["calibrated_probability"] = float(calibrated)
    if lower is not None:
        if not isinstance(lower, (int, float)) or isinstance(lower, bool) or not 0 < float(lower) < 1:
            return finish("NEWS_RESCORE_LOWER_BOUND_INVALID", blockers=["CALIBRATED_LOWER_BOUND_INVALID"])
        receipt["calibrated_lower_bound"] = float(lower)

    response_feature_hash = response.get("feature_snapshot_hash")
    if (
        not isinstance(response_feature_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", response_feature_hash)
    ):
        return finish(
            "NEWS_RESCORE_FEATURE_HASH_MISSING",
            blockers=["SCORER_FEATURE_HASH_MISSING"],
        )
    if response_feature_hash != after["feature_snapshot_hash"]:
        return finish(
            "NEWS_RESCORE_FEATURE_HASH_MISMATCH",
            blockers=["SCORER_FEATURE_HASH_MISMATCH"],
        )

    receipt["model_rescore_verified"] = True
    receipt["scoring_response_digest"] = _hash({
        "code": code,
        "model_version": receipt["fitted_model_version"],
        "feature_snapshot_hash": response_feature_hash,
        "prop_target": prop_target,
        "calibrated_probability": receipt["calibrated_probability"],
        "calibrated_lower_bound": receipt["calibrated_lower_bound"],
    })
    return finish("NEWS_RESCORE_VERIFIED")


def dispatch_news_rescores_for_handoff(
    payload: dict[str, Any],
    *,
    score_fn: ScoreFn | None = None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Walk enriched Multiscout candidates and attach rescore receipts where requested."""
    out = dict(payload)
    handoff = out.get("model_handoff")
    if not isinstance(handoff, dict):
        out["news_fitted_rescore_summary"] = {
            "schema_version": "wow.v17.news_fitted_rescore_summary.v1",
            "candidates_considered": 0,
            "rescore_verified": 0,
            "status_counts": {},
            "model_rescore_verified": False,
            "prediction_authority": False,
            "can_execute": False,
        }
        return out

    updated = dict(handoff)
    status_counts: dict[str, int] = {}
    considered = verified = 0
    for lane in ("team_event_candidates", "prop_candidates"):
        rows = handoff.get(lane)
        if not isinstance(rows, list):
            continue
        new_rows = []
        for row in rows:
            if not isinstance(row, dict):
                new_rows.append(row)
                continue
            item = dict(row)
            research = item.get("news_research_handoff")
            if not (
                isinstance(research, Mapping)
                and research.get("research_rescore_requested") is True
            ):
                new_rows.append(item)
                continue
            considered += 1
            receipt = dispatch_news_rescore(
                item,
                score_fn=score_fn,
                observed_at=observed_at,
                baseline_features=item.get("baseline_features")
                if isinstance(item.get("baseline_features"), dict)
                else {},
            )
            item["news_fitted_rescore_receipt"] = receipt
            item["news_model_rescore_verified"] = receipt.get("model_rescore_verified") is True
            if receipt.get("model_rescore_verified") is True:
                verified += 1
            status = str(receipt.get("dispatch_status") or "UNKNOWN")
            status_counts[status] = status_counts.get(status, 0) + 1
            new_rows.append(item)
        updated[lane] = new_rows

    out["model_handoff"] = updated
    out["news_fitted_rescore_summary"] = {
        "schema_version": "wow.v17.news_fitted_rescore_summary.v1",
        "candidates_considered": considered,
        "rescore_verified": verified,
        "status_counts": status_counts,
        "model_rescore_verified": verified > 0,
        "prediction_authority": False,
        "can_execute": False,
    }
    out["can_execute"] = False
    return out


__all__ = [
    "CAN_EXECUTE",
    "SCHEMA",
    "build_news_feature_snapshot",
    "dispatch_news_rescore",
    "dispatch_news_rescores_for_handoff",
]
