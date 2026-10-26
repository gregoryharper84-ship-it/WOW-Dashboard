"""Strict NHL historical total-goals feature -> official regime label bridge.

Inputs are already verified read-only retrospective D1/official-source receipts.
Never infer missing labels or turn reconstructed prior-game features into
immutable pregame snapshots. NO serving model or approved probability.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

from v17.nhl_total_goals_challenger import TotalRow
from v17.nhl_total_goals_regime_mixture_research import RegimeResearchRow, FEATURE_COUNT

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
ACCEPTED_RECEIPT = "NHL_TOTALS_RETROSPECTIVE_SOURCE_LABELS_RECONCILED"


class NHLTotalsModelBridgeError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def _utc(value: Any) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.utcoffset() is None:
            raise ValueError("timezone missing")
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise NHLTotalsModelBridgeError("NHL_TOTALS_BRIDGE_TIME_INVALID", str(value)) from exc


def _sha(value: Any) -> str:
    value = str(value or "").lower()
    if len(value) != 64 or any(x not in "0123456789abcdef" for x in value):
        raise NHLTotalsModelBridgeError("NHL_TOTALS_BRIDGE_SOURCE_HASH_INVALID", "sha256 required")
    return value


def build_regime_model_research_rows(
    total_rows: Sequence[TotalRow], joined_evidence: Mapping[str, Any],
) -> tuple[list[RegimeResearchRow], dict[str, Any]]:
    """Join every eligible historical total feature to exact verified regime."""
    if not isinstance(joined_evidence, Mapping) or (
        joined_evidence.get("status") != ACCEPTED_RECEIPT
        or joined_evidence.get("historical_reconstruction") is not True
        or joined_evidence.get("archived_pregame_snapshot") is not False
        or joined_evidence.get("probability_publishable") is not False
        or joined_evidence.get("can_execute") is not False
        or joined_evidence.get("model_probability") is not None
        or joined_evidence.get("calibrated_lower_bound") is not None
    ):
        raise NHLTotalsModelBridgeError("NHL_TOTALS_BRIDGE_RECEIPT_INVALID", "unsafe join lineage")
    labels = joined_evidence.get("labels")
    if (not isinstance(labels, list) or not labels
        or type(joined_evidence.get("count")) is not int
        or joined_evidence["count"] != len(labels)):
        raise NHLTotalsModelBridgeError("NHL_TOTALS_BRIDGE_LABELS_INVALID", "incomplete labels")
    by_id = {}
    for label in labels:
        if not isinstance(label, Mapping):
            raise NHLTotalsModelBridgeError("NHL_TOTALS_BRIDGE_LABELS_INVALID", "untyped label")
        key = str(label.get("official_event_id") or "")
        if len(key) != 10 or not key.isdecimal() or key in by_id:
            raise NHLTotalsModelBridgeError("NHL_TOTALS_BRIDGE_LABEL_DUPLICATE", key)
        if (label.get("historical_reconstruction") is not True
            or label.get("archived_pregame_snapshot") is not False
            or label.get("probability_publishable") is not False
            or label.get("can_execute") is not False):
            raise NHLTotalsModelBridgeError("NHL_TOTALS_BRIDGE_LABEL_UNVERIFIED", key)
        _sha(label.get("d1_source_payload_sha256"))
        _sha(label.get("official_schedule_game_sha256"))
        by_id[key] = label
    if not total_rows:
        raise NHLTotalsModelBridgeError("NHL_TOTALS_BRIDGE_FEATURES_EMPTY", "no prior-game rows")
    result = []
    seen = set()
    for row in total_rows:
        if not isinstance(row, TotalRow) or row.historically_reconstructed is not True:
            raise NHLTotalsModelBridgeError("NHL_TOTALS_BRIDGE_FEATURE_UNVERIFIED", "wrong source")
        key = row.event_id
        if key in seen:
            raise NHLTotalsModelBridgeError("NHL_TOTALS_BRIDGE_DUPLICATE_FEATURE", key)
        seen.add(key)
        label = by_id.get(key)
        if label is None:
            raise NHLTotalsModelBridgeError("NHL_TOTALS_BRIDGE_MISSING_REGIME", key)
        try:
            expected_season = row.season_start_year * 10000 + row.season_start_year + 1
            if (int(label["season_id"]) != expected_season
                or int(label["final_total_goals"]) != row.total_goals
                or _sha(row.source_hash) != _sha(label["d1_source_payload_sha256"])
                or _utc(row.start_utc) != _utc(label["event_start_utc"])):
                raise NHLTotalsModelBridgeError("NHL_TOTALS_BRIDGE_IDENTITY_CONFLICT", key)
            if _utc(row.reconstructed_as_of) >= _utc(row.start_utc):
                raise NHLTotalsModelBridgeError("NHL_TOTALS_BRIDGE_LEAKAGE", key)
            if len(row.features) != FEATURE_COUNT:
                raise NHLTotalsModelBridgeError("NHL_TOTALS_BRIDGE_FEATURE_WIDTH", key)
            regime = str(label["final_period_type"])
            if regime not in ("REG", "OT", "SO") or (
                regime != "REG" and row.total_goals % 2 != 1
            ):
                raise NHLTotalsModelBridgeError("NHL_TOTALS_BRIDGE_REGIME_CONFLICT", key)
            regime_hash = _sha(label["official_schedule_game_sha256"])
        except NHLTotalsModelBridgeError:
            raise
        except (KeyError, ValueError, TypeError) as exc:
            raise NHLTotalsModelBridgeError("NHL_TOTALS_BRIDGE_ROW_INVALID", key) from exc
        result.append(RegimeResearchRow(
            event_id=key, season_start_year=row.season_start_year,
            start_utc=_utc(row.start_utc).isoformat(),
            feature_as_of=_utc(row.reconstructed_as_of).isoformat(),
            features=tuple(float(x) for x in row.features),
            total_goals=row.total_goals, terminal_regime=regime,
            official_source_sha256=_sha(row.source_hash),
            outcome_regime_sha256=regime_hash,
            historical_reconstruction=True,
            archived_pregame_snapshot=False,
        ))
    result.sort(key=lambda row: (row.start_utc, row.event_id))
    provenance = [
        (r.event_id, r.start_utc, r.official_source_sha256, r.outcome_regime_sha256)
        for r in result
    ]
    joined_sha = sha256(json.dumps(provenance, sort_keys=True).encode()).hexdigest()
    return result, {
        "status": "NHL_TOTALS_REGIME_FIT_INPUTS_RECONCILED_RESEARCH_ONLY",
        "source_labels_total": len(labels),
        "eligible_feature_rows": len(result),
        "source_only_ineligible_games": len(labels)-len(result),
        "joined_dataset_sha256": joined_sha,
        "feature_provenance": "RETROSPECTIVE_PRIOR_GAME_RECONSTRUCTION_ONLY",
        "immutable_pregame_snapshots": False,
        "independent_forward_cohort": False,
        "forward_2025_consulted_in_prior_model_research": True,
        "calibrated_lower_bound": None,
        "probability_publishable": False,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }
