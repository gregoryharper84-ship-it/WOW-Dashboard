"""Offline, exact-identity V17 paired shadow replay. No fitted inference or promotion.

Input evidence is caller-supplied, not authenticated by this module. Even a
favorable result is descriptive, never a certified probability or model change.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import log, isfinite
import re
from typing import Any, Mapping, Sequence

from v17.universal_sports_intelligence_research import SPORT_DOMAINS

SCHEMA = "WOW_V17_PAIRED_SHADOW_REPLAY_V1"
ID_FIELDS = ("sport", "event_id", "market", "period", "selection", "line", "direction")
MAX_ROWS = 10000
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _timestamp(x: Any) -> datetime | None:
    if not isinstance(x, str):
        return None
    try:
        t = datetime.fromisoformat(x.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.utcoffset() is None:
        return None
    return t.astimezone(timezone.utc)


def _prob(x: Any) -> bool:
    return type(x) in (int, float) and isfinite(x) and 0 < x < 1


def _identity(x: Any) -> tuple | None:
    if not isinstance(x, Mapping):
        return None
    if not isinstance(x.get("sport"), str) or x["sport"] not in SPORT_DOMAINS:
        return None
    values = [x.get(k) for k in ID_FIELDS]
    if any(type(v) not in (str, int, float) or not str(v).strip() for v in values):
        return None
    if type(x.get("line")) not in (int, float, str):
        return None
    if isinstance(x.get("line"), (int, float)) and not isfinite(x["line"]):
        return None
    return tuple(values)


def _score(p: float, y: int) -> tuple[float, float]:
    return (p-y)**2, -(y*log(p) + (1-y)*log(1-p))


def compare_paired_shadow_forecasts(
    trials: Sequence[Mapping[str, Any]], *, minimum_n: int = 30
) -> dict[str, Any]:
    """Reconcile exact incumbent/challenger/settlement triples in holdout only.

    No input can be promoted to trusted model feature or official probability.
    This does NOT attest that claimed holdout was independent or predictions
    were authentically recorded before an event.
    """
    if not isinstance(trials, (list, tuple)) or len(trials) > MAX_ROWS:
        raise ValueError("PAIR_REPLAY_INPUT_INVALID")
    if type(minimum_n) is not int or minimum_n < 30:
        raise ValueError("PAIR_REPLAY_MINIMUM_N_INVALID")
    results = []
    seen: set[tuple] = set()
    metrics: list[tuple[float, float, float, float]] = []
    for index, record in enumerate(trials):
        reason = "REPLAY_CANDIDATE_UNVERIFIED"
        identity = None
        if not isinstance(record, Mapping):
            reason = "REPLAY_RECORD_INVALID"
        else:
            champion, challenger, settled = (
                record.get("incumbent"), record.get("challenger"), record.get("settlement")
            )
            identity = _identity(champion)
            if not identity or _identity(challenger) != identity or _identity(settled) != identity:
                reason = "REPLAY_EXACT_IDENTITY_MISMATCH"
            elif identity in seen:
                reason = "REPLAY_DUPLICATE_THESIS"
            else:
                seen.add(identity)
                start = _timestamp(champion.get("event_start_utc"))
                ci = _timestamp(champion.get("forecast_asof_utc"))
                cc = _timestamp(challenger.get("forecast_asof_utc"))
                si = _timestamp(settled.get("settled_at_utc"))
                ai = _timestamp(champion.get("latest_feature_available_utc"))
                ac = _timestamp(challenger.get("latest_feature_available_utc"))
                if (not all((start, ci, cc, si, ai, ac))
                    or not (ai <= ci < start < si and ac <= cc < start)
                    or start != _timestamp(challenger.get("event_start_utc"))
                    or start != _timestamp(settled.get("event_start_utc"))):
                    reason = "REPLAY_TIME_LEAKAGE_OR_MISMATCH"
                elif any(z.get("partition") != "HOLDOUT" for z in (champion, challenger, settled)):
                    reason = "REPLAY_NOT_HOLDOUT"
                elif not all(isinstance(z.get("immutable_prediction_id"), str)
                             and z["immutable_prediction_id"].strip() for z in (champion, challenger)):
                    reason = "REPLAY_MISSING_IMMUTABLE_RECEIPT"
                elif champion["immutable_prediction_id"] == challenger["immutable_prediction_id"]:
                    reason = "REPLAY_FORECAST_RECEIPTS_NOT_INDEPENDENT"
                elif any(not isinstance(z.get("model_artifact_id"), str)
                         or not z["model_artifact_id"].strip() for z in (champion, challenger)):
                    reason = "REPLAY_MODEL_PROVENANCE_MISSING"
                elif champion["model_artifact_id"] == challenger["model_artifact_id"]:
                    reason = "REPLAY_MODEL_ARTIFACT_NOT_DISTINCT"
                # A separate registry row, artifact label or training-set hash
                # is NOT evidence of a distinct fitted model. Archived NFL
                # versions have shared identical payload checksums.
                elif any(not isinstance(z.get("artifact_checksum"), str)
                         or not SHA256.fullmatch(z["artifact_checksum"])
                         for z in (champion, challenger)):
                    reason = "REPLAY_FITTED_CHECKSUM_MISSING_OR_INVALID"
                elif champion["artifact_checksum"] == challenger["artifact_checksum"]:
                    reason = "REPLAY_FITTED_ARTIFACT_BYTES_NOT_DISTINCT"
                elif any(not _timestamp(z.get("training_cutoff_utc"))
                         or _timestamp(z["training_cutoff_utc"]) > _timestamp(z["forecast_asof_utc"])
                         for z in (champion, challenger)):
                    reason = "REPLAY_TRAINING_CUTOFF_UNVERIFIED"
                elif not all(_prob(z.get("calibrated_probability"))
                             and type(z.get("calibrated_lower_bound")) in (int, float)
                             and isfinite(z["calibrated_lower_bound"])
                             and 0 <= z["calibrated_lower_bound"] <= z["calibrated_probability"]
                             for z in (champion, challenger)):
                    reason = "REPLAY_PROBABILITY_PACKAGE_INVALID"
                elif type(settled.get("result")) is not int or settled["result"] not in (0, 1):
                    reason = "REPLAY_SETTLEMENT_INVALID"
                elif settled.get("result_status") != "FINAL" or settled.get("void_or_push") is not False:
                    reason = "REPLAY_UNSETTLED_OR_VOID"
                else:
                    y = settled["result"]
                    p1, p2 = champion["calibrated_probability"], challenger["calibrated_probability"]
                    b1, l1 = _score(p1, y)
                    b2, l2 = _score(p2, y)
                    metrics.append((b1, l1, b2, l2))
        results.append({
            "row_index": index, "status": reason,
            "exact_thesis": list(identity) if identity else None,
            "input_provenance_attested": False,
            "model_probability_publishable": False, "can_execute": False,
        })
    n = len(metrics)
    def mean(idx: int) -> float | None:
        return sum(x[idx] for x in metrics) / n if n else None
    ib, il, cb, cl = (mean(i) for i in range(4))
    return {
        "schema": SCHEMA,
        "status": ("DESCRIPTIVE_SHADOW_METRICS_NOT_ATTESTED" if n >= minimum_n
                   else "NOT_ENOUGH_EVIDENCE"),
        "received": len(trials), "eligible_unattested": n,
        "held": len(trials)-n, "rows": results,
        "incumbent_brier_unattested": ib, "challenger_brier_unattested": cb,
        "incumbent_log_loss_unattested": il, "challenger_log_loss_unattested": cl,
        "brier_delta_challenger_minus_incumbent": cb-ib if n else None,
        "log_loss_delta_challenger_minus_incumbent": cl-il if n else None,
        "input_provenance_attested": False, "holdout_independently_attested": False,
        "statistically_significant_superiority": None,
        "feature_ablation_completed": False,
        "governed_model_activation_authorized": False,
        "model_consumption_receipts": [], "probability_publishable": False,
        "can_execute": False,
    }
