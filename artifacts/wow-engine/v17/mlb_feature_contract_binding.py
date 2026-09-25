"""Fail-closed MLB score-time feature-contract binding and layered receipts.

This module is Class-B contract/audit infrastructure only.  It validates that the
HOME/AWAY forward feature snapshots presented to the already-certified MLB
prospective specialist match the exact immutable ``MLB_V2D_CONTEXT_V1`` feature
order before the scorer is invoked.  It does not alter fitted coefficients,
probability math, calibration, bounds, ranking, or publication thresholds.

The production prospective specialist consumes the persisted fitted baseline
score plus eight context/failure inputs directly.  Receipts therefore distinguish
all 38 features bound upstream to the fitted baseline contract from the eight
features directly consumed in the prospective failure/context layer.  No layer
may claim direct consumption of all 38 merely because the snapshots are present.

``can_execute`` is false on every emitted structure.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Callable, Mapping

import mlb_event_specialist_v16 as specialist
from mlb_event_specialist_v16 import ProspectiveModelUnavailable
from v17.mlb_event_feature_contract import (
    FEATURE_ORDER,
    FEATURE_ORDER_SHA256,
    FEATURE_SCHEMA_VERSION,
    feature_order_sha256,
)

CAN_EXECUTE = False
_STATE_KEY = "_wow_v17_mlb_feature_contract_binding_installed"
_ORIGINAL_SCORER_KEY = "_v17_feature_contract_original_score_prospective_event"
_ORIGINAL_RECEIPT_KEY = "_v17_feature_contract_original_receipt_builder"

# These are the only fitted-snapshot values read directly by the existing
# prospective specialist's starter/bullpen/defense failure functions.
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


def _rows(call: Any) -> list[dict[str, Any]]:
    result = call.execute()
    return [dict(row) for row in (getattr(result, "data", None) or [])]


def _validated_feature_rows(client: Any, bridge_payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Load exactly one PASS HOME/AWAY snapshot and bind it to the certified vector."""
    shadow_event_id = str(bridge_payload.get("shadow_event_id") or "").strip()
    if not shadow_event_id:
        raise ProspectiveModelUnavailable("bridge_missing_score_identity")

    try:
        rows = _rows(
            client.table("wow_mlb_forward_feature_snapshots")
            .select(
                "feature_snapshot_id,shadow_event_id,side,feature_names,feature_vector,"
                "source_snapshot_ids,source_urls,hydration_status,created_at"
            )
            .eq("shadow_event_id", shadow_event_id)
            .eq("hydration_status", "PASS")
        )
    except Exception as exc:
        raise ProspectiveModelUnavailable("feature_snapshot_contract_query_failed") from exc

    by_side: dict[str, list[dict[str, Any]]] = {"HOME": [], "AWAY": []}
    for row in rows:
        side = str(row.get("side") or "").upper().strip()
        if side in by_side:
            by_side[side].append(row)

    if any(len(by_side[side]) != 1 for side in ("HOME", "AWAY")):
        raise ProspectiveModelUnavailable("feature_snapshot_contract_ambiguous")

    bound = {side: by_side[side][0] for side in ("HOME", "AWAY")}
    for row in bound.values():
        names = tuple(str(value) for value in (row.get("feature_names") or ()))
        if names != FEATURE_ORDER:
            raise ProspectiveModelUnavailable("feature_contract_mismatch")
        if feature_order_sha256(names) != FEATURE_ORDER_SHA256:
            raise ProspectiveModelUnavailable("feature_contract_digest_mismatch")

        values = row.get("feature_vector") or ()
        if len(values) != len(FEATURE_ORDER):
            raise ProspectiveModelUnavailable("feature_vector_invalid")
        try:
            parsed = [float(value) for value in values]
        except (TypeError, ValueError) as exc:
            raise ProspectiveModelUnavailable("feature_vector_invalid") from exc
        if any(not math.isfinite(value) for value in parsed):
            raise ProspectiveModelUnavailable("feature_vector_invalid")
        row["feature_names"] = FEATURE_ORDER
        row["feature_vector"] = parsed

    return bound


def _feature_observations(
    bound: Mapping[str, Mapping[str, Any]],
    bridge_payload: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    home = bound["HOME"]
    away = bound["AWAY"]
    created = sorted(
        str(value)
        for value in (home.get("created_at"), away.get("created_at"))
        if str(value or "").strip()
    )
    observed_at = created[-1] if created else None
    provenance_seed = json.dumps(
        {
            "shadow_event_id": str(bridge_payload.get("shadow_event_id") or ""),
            "home_feature_snapshot_id": str(home.get("feature_snapshot_id") or ""),
            "away_feature_snapshot_id": str(away.get("feature_snapshot_id") or ""),
            "home_source_snapshot_ids": list(home.get("source_snapshot_ids") or ()),
            "away_source_snapshot_ids": list(away.get("source_snapshot_ids") or ()),
            "feature_order_sha256": FEATURE_ORDER_SHA256,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    pair_provenance_id = hashlib.sha256(provenance_seed.encode("utf-8")).hexdigest()
    return {
        feature_id: {
            "value_status": "AVAILABLE",
            "freshness_status": "OBSERVED_SCORE_TIME_SNAPSHOT",
            "source": "wow_mlb_forward_feature_snapshots:HOME+AWAY",
            "source_timestamp": observed_at,
            "observed_at": observed_at,
            "provenance_id": f"{pair_provenance_id}:{feature_id}",
        }
        for feature_id in FEATURE_ORDER
    }


def _original_scorer() -> Callable[..., dict[str, Any]]:
    original = getattr(specialist, _ORIGINAL_SCORER_KEY, None)
    if callable(original):
        return original
    current = getattr(specialist, "score_prospective_event", None)
    if callable(current) and current is not score_prospective_event_with_feature_contract:
        return current
    raise RuntimeError("MLB_PROSPECTIVE_SCORER_NOT_CALLABLE")


def score_prospective_event_with_feature_contract(
    req: Any,
    bridge_payload: dict[str, Any],
    client: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    """Validate the exact snapshot contract, invoke the incumbent scorer unchanged, then annotate."""
    bound = _validated_feature_rows(client, bridge_payload)
    original = _original_scorer()
    result = original(req=req, bridge_payload=bridge_payload, client=client, **kwargs)
    if not isinstance(result, dict):
        return result

    out = dict(result)
    out["consumed_feature_ids"] = list(DIRECT_CONTEXT_FAILURE_FEATURE_IDS)
    out["feature_observations"] = _feature_observations(bound, bridge_payload)
    out["upstream_fitted_baseline_feature_contract"] = {
        "schema_version": FEATURE_SCHEMA_VERSION,
        "feature_ids": list(FEATURE_ORDER),
        "feature_count": len(FEATURE_ORDER),
        "feature_order_sha256": FEATURE_ORDER_SHA256,
        "home_feature_snapshot_id": str(bound["HOME"].get("feature_snapshot_id") or ""),
        "away_feature_snapshot_id": str(bound["AWAY"].get("feature_snapshot_id") or ""),
        "direct_specialist_consumption_claimed": False,
        "can_execute": False,
    }
    out["direct_context_failure_consumption"] = {
        "feature_ids": list(DIRECT_CONTEXT_FAILURE_FEATURE_IDS),
        "feature_count": len(DIRECT_CONTEXT_FAILURE_FEATURE_IDS),
        "source": "mlb_event_specialist_v16:starter+bullpen+defense_failure_probability",
        "can_execute": False,
    }
    out["can_execute"] = False
    return out


def _layered_receipt_builder(base_builder: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Decorate the generic receipt only when the scorer emitted explicit MLB layer metadata."""
    def _builder(*args: Any, **kwargs: Any) -> dict[str, Any]:
        base = dict(base_builder(*args, **kwargs))
        result = kwargs.get("result")
        if not isinstance(result, Mapping):
            return base
        upstream = result.get("upstream_fitted_baseline_feature_contract")
        direct = result.get("direct_context_failure_consumption")
        if not isinstance(upstream, Mapping) or not isinstance(direct, Mapping):
            return base

        upstream_ids = [str(value) for value in (upstream.get("feature_ids") or ())]
        direct_ids = [str(value) for value in (direct.get("feature_ids") or ())]
        layers = {
            "upstream_fitted_baseline": {**dict(upstream), "can_execute": False},
            "direct_context_failure": {**dict(direct), "can_execute": False},
        }
        receipt_seed = json.dumps(
            {
                "base_receipt_id": base.get("feature_consumption_receipt_id"),
                "layers": layers,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        base.update(
            {
                "feature_consumption_receipt_id": hashlib.sha256(receipt_seed.encode("utf-8")).hexdigest(),
                "features_consumed": len(direct_ids),
                "upstream_fitted_baseline_features_bound": len(upstream_ids),
                "direct_specialist_features_consumed": len(direct_ids),
                "consumption_layers": layers,
                "can_execute": False,
            }
        )
        return base

    return _builder


def install_mlb_feature_contract_binding() -> bool:
    """Install the contract wrapper after interactive I/O wrappers, idempotently."""
    if getattr(specialist, _STATE_KEY, False):
        return True

    current = getattr(specialist, "score_prospective_event", None)
    if not callable(current):
        return False
    setattr(specialist, _ORIGINAL_SCORER_KEY, current)
    specialist.score_prospective_event = score_prospective_event_with_feature_contract

    # The standalone route imports the scorer by value.  If it is already loaded,
    # update that local binding too; if it loads later it will import the wrapped
    # specialist function automatically.
    try:
        import mlb_event_prospective_runtime as prospective_runtime

        prospective_runtime.score_prospective_event = score_prospective_event_with_feature_contract
    except ImportError:
        pass

    # Generic team/event receipts remain generic for every other sport.  The
    # decorator adds MLB layers only when this scorer emitted both layer objects.
    try:
        import v17.team_event_bridge_runtime as bridge_runtime

        original_builder = getattr(bridge_runtime, _ORIGINAL_RECEIPT_KEY, None)
        if not callable(original_builder):
            original_builder = bridge_runtime.build_feature_consumption_receipt
            setattr(bridge_runtime, _ORIGINAL_RECEIPT_KEY, original_builder)
        bridge_runtime.build_feature_consumption_receipt = _layered_receipt_builder(original_builder)
    except ImportError:
        pass

    setattr(specialist, _STATE_KEY, True)
    return True


__all__ = [
    "CAN_EXECUTE",
    "DIRECT_CONTEXT_FAILURE_FEATURE_IDS",
    "install_mlb_feature_contract_binding",
    "score_prospective_event_with_feature_contract",
]
