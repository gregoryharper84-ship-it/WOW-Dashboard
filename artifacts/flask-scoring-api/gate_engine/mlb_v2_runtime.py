from __future__ import annotations

"""Canonical MLB V2 rolling probability artifact runtime.

The published point estimate is the model-native Platt probability.  Market odds
are deliberately not accepted by this module.  It is probability-publication
capable only; bet execution remains disabled unconditionally.
"""

import hashlib
import json
import math
import threading
from datetime import date
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from gate_engine.mlb_v2_features import FEATURE_NAMES

can_execute: bool = False
can_approve_bets: bool = False

_ARTIFACT_DIR = Path(__file__).resolve().parent / "mlb_v2_artifacts"
_MANIFEST = _ARTIFACT_DIR / "mlb_v2_manifest.json"
_MODEL = _ARTIFACT_DIR / "mlb_v2_base_model.joblib"
_CALIBRATOR = _ARTIFACT_DIR / "mlb_v2_platt_calibrator.joblib"
_SCHEMA = _ARTIFACT_DIR / "mlb_v2_feature_schema.json"
_STATE = _ARTIFACT_DIR / "mlb_v2_pregame_state.json.gz"

_lock = threading.Lock()
_bundle: dict[str, Any] | None = None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _load_bundle() -> dict[str, Any]:
    global _bundle
    if _bundle is not None:
        return _bundle
    with _lock:
        if _bundle is not None:
            return _bundle
        required = [_MANIFEST, _MODEL, _CALIBRATOR, _SCHEMA, _STATE]
        missing = [p.name for p in required if not p.exists()]
        if missing:
            raise RuntimeError(f"MLB_V2_ARTIFACT_MISSING:{','.join(missing)}")
        manifest = json.loads(_MANIFEST.read_text())
        schema = json.loads(_SCHEMA.read_text())
        if manifest.get("model_id") != "mlb-moneyline-v2-rolling-2026":
            raise RuntimeError("MLB_V2_MODEL_ID_MISMATCH")
        if manifest.get("schema_version") != schema.get("schema_version"):
            raise RuntimeError("MLB_V2_SCHEMA_VERSION_MISMATCH")
        if list(manifest.get("feature_names") or []) != FEATURE_NAMES:
            raise RuntimeError("MLB_V2_FEATURE_SCHEMA_MISMATCH")
        hashes = manifest.get("artifact_hashes") or {}
        for path in (_MODEL, _CALIBRATOR, _STATE, _SCHEMA):
            expected = hashes.get(path.name)
            if not expected:
                raise RuntimeError(f"MLB_V2_ARTIFACT_HASH_MISSING:{path.name}")
            actual = _sha256(path)
            if actual != expected:
                raise RuntimeError(f"MLB_V2_ARTIFACT_HASH_MISMATCH:{path.name}")
        model = joblib.load(_MODEL)
        calibrator = joblib.load(_CALIBRATOR)
        _bundle = {"manifest": manifest, "schema": schema, "model": model, "calibrator": calibrator}
        return _bundle


def artifact_health(as_of: date | None = None) -> dict[str, Any]:
    as_of = as_of or date.today()
    try:
        bundle = _load_bundle()
    except Exception as exc:
        return {
            "healthy": False,
            "probability_capability": "UNAVAILABLE",
            "blockers": [str(exc)],
            "can_execute": False,
        }
    manifest = bundle["manifest"]
    pub = manifest.get("publication_contract") or {}
    blockers: list[str] = []
    expires = pub.get("expires_at")
    if expires and as_of >= date.fromisoformat(str(expires)):
        blockers.append(f"MLB_V2_ARTIFACT_EXPIRED:{expires}")
    season = pub.get("valid_for_season")
    if season and int(season) != as_of.year:
        blockers.append(f"MLB_V2_ARTIFACT_SEASON_MISMATCH:artifact={season}:request={as_of.year}")
    return {
        "healthy": not blockers,
        "probability_capability": "AVAILABLE" if not blockers else "UNAVAILABLE",
        "model_id": manifest.get("model_id"),
        "schema_version": manifest.get("schema_version"),
        "expires_at": expires,
        "blockers": blockers,
        "can_execute": False,
    }


def _empirical_interval(manifest: dict[str, Any], p: float) -> tuple[float, float, dict[str, Any]]:
    bins = list(manifest.get("calibration_bins") or [])
    if not bins:
        return max(0.01, p - 0.10), min(0.99, p + 0.10), {"source": "FALLBACK_10PP"}
    selected = None
    for b in bins:
        if float(b["predicted_min"]) <= p <= float(b["predicted_max"]):
            selected = b
            break
    if selected is None:
        selected = min(bins, key=lambda b: abs(float(b["predicted_mean"]) - p))
    lo = float(selected["observed_wilson95_lower"])
    hi = float(selected["observed_wilson95_upper"])
    # The interval is a calibration-reliability interval, not an event-specific
    # confidence interval. Always include the point estimate itself.
    return max(0.01, min(lo, p)), min(0.99, max(hi, p)), {
        "source": "PLATT_CALIBRATION_BIN_WILSON95",
        "bin": selected,
        "interpretation": "CALIBRATION_RELIABILITY_INTERVAL_NOT_EVENT_LEVEL_CI",
    }


def score_home_probability(features: list[float] | tuple[float, ...], as_of: date | None = None) -> dict[str, Any]:
    as_of = as_of or date.today()
    health = artifact_health(as_of)
    if not health["healthy"]:
        return {
            "ok": False,
            "probability_publishable": False,
            "blockers": health["blockers"],
            "can_execute": False,
        }
    bundle = _load_bundle()
    manifest = bundle["manifest"]
    schema = bundle["schema"]
    blockers: list[str] = []
    warnings: list[str] = []

    if not isinstance(features, (list, tuple)):
        blockers.append("MLB_V2_FEATURE_VECTOR_MISSING")
        vals: list[float] = []
    else:
        vals = []
        try:
            vals = [float(v) for v in features]
        except (TypeError, ValueError):
            blockers.append("MLB_V2_FEATURE_VECTOR_NON_NUMERIC")
    if vals and len(vals) != len(FEATURE_NAMES):
        blockers.append(f"MLB_V2_FEATURE_COUNT_MISMATCH:{len(vals)}!={len(FEATURE_NAMES)}")
    if vals and any(not math.isfinite(v) for v in vals):
        blockers.append("MLB_V2_FEATURE_VECTOR_NONFINITE")

    hard_ood: list[str] = []
    soft_ood: list[str] = []
    support = list(schema.get("feature_support") or [])
    if vals and len(support) == len(vals):
        for i, (value, s) in enumerate(zip(vals, support)):
            name = str(s.get("name") or FEATURE_NAMES[i])
            if value < float(s["min"]) or value > float(s["max"]):
                hard_ood.append(name)
            if value < float(s["q005"]) or value > float(s["q995"]):
                soft_ood.append(name)
    elif vals:
        blockers.append("MLB_V2_FEATURE_SUPPORT_SCHEMA_MISSING")

    pub = manifest.get("publication_contract") or {}
    hard_limit = int(pub.get("hard_ood_feature_limit", 2))
    soft_watch = int(pub.get("soft_ood_watch_limit", 8))
    if len(hard_ood) > hard_limit:
        blockers.append(f"MLB_V2_HARD_OOD_FEATURES:{len(hard_ood)}>{hard_limit}:{','.join(hard_ood)}")
    if len(soft_ood) > soft_watch:
        warnings.append(f"MLB_V2_SOFT_DRIFT_WATCH:{len(soft_ood)}>{soft_watch}")

    if blockers:
        return {
            "ok": False,
            "probability_publishable": False,
            "model_id": manifest.get("model_id"),
            "blockers": blockers,
            "warnings": warnings,
            "drift": {"hard_ood_features": hard_ood, "soft_ood_features": soft_ood},
            "can_execute": False,
        }

    X = np.asarray([vals], dtype=float)
    raw = float(bundle["model"].predict_proba(X)[0, 1])
    calibrated = float(bundle["calibrator"].predict_proba(np.asarray([[raw]], dtype=float))[0, 1])
    if not (0.0 < raw < 1.0 and 0.0 < calibrated < 1.0):
        return {
            "ok": False,
            "probability_publishable": False,
            "blockers": ["MLB_V2_PROBABILITY_RANGE_BREACH"],
            "can_execute": False,
        }
    lo, hi, interval_meta = _empirical_interval(manifest, calibrated)
    return {
        "ok": True,
        "model_id": manifest.get("model_id"),
        "schema_version": manifest.get("schema_version"),
        "raw_pre_platt_home_probability": raw,
        "home_probability": calibrated,
        "away_probability": 1.0 - calibrated,
        "home_probability_lower_bound": lo,
        "home_probability_upper_bound": hi,
        "empirical_interval": interval_meta,
        "native_calibrated": True,
        "point_estimate_locked": True,
        "market_weight_in_point_probability": 0.0,
        "probability_publishable": True,
        "drift": {
            "hard_ood_features": hard_ood,
            "soft_ood_features": soft_ood,
            "status": "WATCH" if warnings else "PASS",
        },
        "warnings": warnings,
        "blockers": [],
        "can_execute": False,
        "can_approve_bets": False,
    }


def reset_artifact_cache_for_tests() -> None:
    global _bundle
    with _lock:
        _bundle = None
