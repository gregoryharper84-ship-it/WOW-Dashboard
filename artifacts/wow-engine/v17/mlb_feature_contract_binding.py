"""V17 MLB score-time feature-contract binding and consumption receipts.

This module is intentionally probability-neutral.  It validates that the
immutable HOME/AWAY forward feature snapshots presented to the already-certified
MLB specialist match the exact fitted MLB_V2D_CONTEXT_V1 vector before the
specialist runs.  It then records stage-scoped consumption evidence without
claiming that the specialist directly consumes every fitted baseline feature.

The underlying ``mlb_event_specialist_v16.score_prospective_event`` function is
not modified here: valid-input probability mathematics, simulation, calibration,
bounds and failure-regime weights remain bit-for-bit under that scorer.
"""
from __future__ import annotations

import hashlib
import json
import sys
from typing import Any, Callable, Mapping

from mlb_event_specialist_v16 import ProspectiveModelUnavailable
from v17.mlb_event_feature_contract import (
    FEATURE_ORDER,
    FEATURE_ORDER_SHA256,
    FEATURE_ORDER_SOURCE,
    FEATURE_SCHEMA_VERSION,
    FORWARD_SNAPSHOT_SOURCE,
    MODEL_ARTIFACT_VERSION,
    MODEL_FAMILY,
    feature_order_sha256,
    observed_feature_order_matches,
)

CAN_EXECUTE = False

# These are the only fitted-vector fields read numerically by the prospective
# specialist itself.  The complete 38-feature vector belongs to the upstream
# fitted baseline contract and must not be mislabeled as direct specialist use.
DIRECT_CONTEXT_FAILURE_FEATURE_IDS = (
    "opp_starter_era",
    "opp_starter_bb_rate",
    "opp_starter_prior_starts",
    "opp_starter_pitches_last3",
    "opp_bp_era",
    "opp_bp_bb_rate",
    "opp_bp_pitches_3d",
    "opp_errors_pg",
)


def _query_feature_rows(client: Any, shadow_event_id: str) -> list[dict[str, Any]]:
    try:
        result = (
            client.table("wow_mlb_forward_feature_snapshots")
            .select(
                "feature_snapshot_id,shadow_event_id,side,feature_names,feature_vector,"
                "source_snapshot_ids,source_urls,hydration_status,created_at"
            )
            .eq("shadow_event_id", shadow_event_id)
            .eq("hydration_status", "PASS")
            .execute()
        )
    except Exception as exc:
        raise ProspectiveModelUnavailable("feature_snapshot_contract_query_failed") from exc
    rows = getattr(result, "data", None) or []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _validated_feature_rows(client: Any, bridge_payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    shadow_event_id = str(bridge_payload.get("shadow_event_id") or "").strip()
    if not shadow_event_id:
        raise ProspectiveModelUnavailable("bridge_missing_score_identity")

    rows = _query_feature_rows(client, shadow_event_id)
    by_side: dict[str, list[dict[str, Any]]] = {"HOME": [], "AWAY": []}
    for row in rows:
        side = str(row.get("side") or "").upper().strip()
        if side in by_side:
            by_side[side].append(row)

    if any(len(by_side[side]) == 0 for side in ("HOME", "AWAY")):
        raise ProspectiveModelUnavailable("feature_snapshots_missing")
    # The underlying specialist historically builds a side->row dictionary.  If
    # more than one PASS row exists for a side, the exact score-time row would be
    # ambiguous.  Refuse to infer one rather than silently binding a different row.
    if any(len(by_side[side]) != 1 for side in ("HOME", "AWAY")):
        raise ProspectiveModelUnavailable("feature_snapshot_contract_ambiguous")

    bound: dict[str, dict[str, Any]] = {}
    for side in ("HOME", "AWAY"):
        row = by_side[side][0]
        names = tuple(str(value) for value in (row.get("feature_names") or ()))
        values = tuple(row.get("feature_vector") or ())
        if len(names) != len(values):
            raise ProspectiveModelUnavailable("feature_vector_mismatch")
        if not observed_feature_order_matches(names):
            raise ProspectiveModelUnavailable("feature_contract_mismatch")
        if feature_order_sha256(names) != FEATURE_ORDER_SHA256:
            raise ProspectiveModelUnavailable("feature_contract_digest_mismatch")
        try:
            numeric_values = tuple(float(value) for value in values)
        except (TypeError, ValueError) as exc:
            raise ProspectiveModelUnavailable("feature_vector_invalid") from exc
        if len(numeric_values) != len(FEATURE_ORDER):
            raise ProspectiveModelUnavailable("feature_vector_mismatch")
        bound[side] = {**row, "feature_names": names, "feature_vector": numeric_values}
    return bound


def _combined_provenance_id(feature_id: str, bound: Mapping[str, Mapping[str, Any]]) -> str:
    payload = {
        "feature_id": feature_id,
        "schema": FEATURE_SCHEMA_VERSION,
        "digest": FEATURE_ORDER_SHA256,
        "home_feature_snapshot_id": bound["HOME"].get("feature_snapshot_id"),
        "away_feature_snapshot_id": bound["AWAY"].get("feature_snapshot_id"),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _observations(bound: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    created = [str(bound[side].get("created_at") or "") for side in ("HOME", "AWAY")]
    observed_at = max(created)
    out: dict[str, dict[str, Any]] = {}
    for feature_id in FEATURE_ORDER:
        out[feature_id] = {
            "value_status": "AVAILABLE",
            "source": "wow_mlb_forward_feature_snapshots:HOME+AWAY",
            "source_timestamp": observed_at or None,
            "observed_at": observed_at or None,
            "provenance_id": _combined_provenance_id(feature_id, bound),
        }
    return out


def _binding_metadata(bound: Mapping[str, Mapping[str, Any]], bridge_payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": FEATURE_SCHEMA_VERSION,
        "feature_order_sha256": FEATURE_ORDER_SHA256,
        "feature_count": len(FEATURE_ORDER),
        "feature_ids": list(FEATURE_ORDER),
        "model_artifact_version": MODEL_ARTIFACT_VERSION,
        "model_family": MODEL_FAMILY,
        "feature_order_source": FEATURE_ORDER_SOURCE,
        "forward_snapshot_source": FORWARD_SNAPSHOT_SOURCE,
        "base_score_snapshot_id": bridge_payload.get("score_snapshot_id"),
        "feature_snapshot_ids_by_side": {
            side: bound[side].get("feature_snapshot_id") for side in ("HOME", "AWAY")
        },
        "binding_status": "BOUND_EXACT_CERTIFIED_FEATURE_ORDER",
        "consumption_stage": "UPSTREAM_FITTED_BASELINE",
        "direct_specialist_consumption_claimed": False,
        "can_execute": False,
    }


def _direct_consumption_metadata(bound: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "feature_ids": list(DIRECT_CONTEXT_FAILURE_FEATURE_IDS),
        "feature_count": len(DIRECT_CONTEXT_FAILURE_FEATURE_IDS),
        "consumption_stage": "PROSPECTIVE_CONTEXT_FAILURE_LAYER",
        "consumption_assertion": "DIRECT_NUMERICAL_READ_BY_SPECIALIST",
        "availability_status": "AVAILABLE",
        "feature_snapshot_ids_by_side": {
            side: bound[side].get("feature_snapshot_id") for side in ("HOME", "AWAY")
        },
        "can_execute": False,
    }


def score_prospective_event_with_feature_contract(
    req: Any,
    bridge_payload: dict[str, Any],
    client: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    """Validate score-time feature identity, run the unchanged scorer, add audit metadata."""
    import mlb_event_specialist_v16 as specialist

    original = getattr(specialist, "_v17_feature_contract_original_score_prospective_event", None)
    if not callable(original):
        current = getattr(specialist, "score_prospective_event", None)
        if current is score_prospective_event_with_feature_contract:
            raise RuntimeError("MLB_FEATURE_CONTRACT_ORIGINAL_SCORER_MISSING")
        original = current
    if not callable(original):
        raise RuntimeError("MLB_PROSPECTIVE_SCORER_NOT_CALLABLE")

    bound = _validated_feature_rows(client, bridge_payload)
    result = original(req, bridge_payload, client, **kwargs)
    if not isinstance(result, dict):
        return result

    out = dict(result)
    existing_observations = out.get("feature_observations")
    merged_observations = dict(existing_observations) if isinstance(existing_observations, Mapping) else {}
    merged_observations.update(_observations(bound))

    # Explicit direct consumption is intentionally only the eight fields proven
    # by the specialist code path, never the complete upstream fitted vector.
    out["consumed_feature_ids"] = list(DIRECT_CONTEXT_FAILURE_FEATURE_IDS)
    out["feature_observations"] = merged_observations
    out["upstream_fitted_baseline_feature_contract"] = _binding_metadata(bound, bridge_payload)
    out["direct_context_failure_consumption"] = _direct_consumption_metadata(bound)
    out["feature_contract_binding_status"] = "PASS"
    out["can_execute"] = False
    return out


def _layered_receipt_builder(original_builder: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    def build(*args: Any, **kwargs: Any) -> dict[str, Any]:
        receipt = dict(original_builder(*args, **kwargs))
        result = kwargs.get("result")
        if not isinstance(result, Mapping):
            return receipt
        upstream = result.get("upstream_fitted_baseline_feature_contract")
        direct = result.get("direct_context_failure_consumption")
        if not isinstance(upstream, Mapping) or not isinstance(direct, Mapping):
            return receipt

        # Make the stage distinction explicit in the receipt.  The existing
        # features_consumed field remains direct specialist consumption only.
        receipt["consumption_layers"] = {
            "upstream_fitted_baseline": dict(upstream),
            "direct_context_failure": dict(direct),
        }
        receipt["upstream_fitted_baseline_features_bound"] = len(
            upstream.get("feature_ids") or ()
        )
        receipt["direct_specialist_features_consumed"] = len(
            direct.get("feature_ids") or ()
        )
        receipt["can_execute"] = False

        core = dict(receipt)
        core.pop("feature_consumption_receipt_id", None)
        receipt["feature_consumption_receipt_id"] = hashlib.sha256(
            json.dumps(core, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        return receipt

    return build


def install_mlb_feature_contract_binding() -> bool:
    """Install the validation wrapper before the V17 MLB bridge resolves its scorer."""
    import mlb_event_specialist_v16 as specialist
    from v17 import team_event_bridge_runtime as bridge_runtime

    if getattr(specialist, "_v17_feature_contract_binding_installed", False):
        return True

    original = getattr(specialist, "score_prospective_event", None)
    if not callable(original):
        return False
    specialist._v17_feature_contract_original_score_prospective_event = original
    specialist.score_prospective_event = score_prospective_event_with_feature_contract
    specialist._v17_feature_contract_binding_installed = True

    # If the legacy standalone module is already imported, align its bound global
    # with the same wrapper. Future imports read the patched specialist attribute.
    standalone = sys.modules.get("mlb_event_prospective_runtime")
    if standalone is not None:
        standalone.score_prospective_event = score_prospective_event_with_feature_contract

    if not getattr(bridge_runtime, "_v17_mlb_layered_receipt_builder_installed", False):
        bridge_runtime.build_feature_consumption_receipt = _layered_receipt_builder(
            bridge_runtime.build_feature_consumption_receipt
        )
        bridge_runtime._v17_mlb_layered_receipt_builder_installed = True

    return True


__all__ = [
    "CAN_EXECUTE",
    "DIRECT_CONTEXT_FAILURE_FEATURE_IDS",
    "install_mlb_feature_contract_binding",
    "score_prospective_event_with_feature_contract",
]
