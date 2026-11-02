"""Fitted calibration artifact contract for V17 multisport team/event models.

The new sport adapters may produce a raw sporting probability, but official
publication requires calibration history. This module validates and applies a
pre-fitted Platt calibration artifact; it never derives calibration from market
prices and never creates execution authority.

Binary artifact shape (WNBA/NHL/TENNIS/MMA):
    artifact_type=BINARY_PLATT_CALIBRATOR
    sport, calibration_method, calibration_version, training_n
    platt_a, platt_b, residual_quantile_90
    brier_score, calibration_error, source_data_hash, split_hash
    fit_end, health_status=PASS, certification_status=PASS

Soccer uses MULTICLASS_PLATT_CALIBRATOR with one coefficient/residual record per
HOME/DRAW/AWAY outcome. Calibrated outcome probabilities are renormalized to 1.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from decimal import Decimal
from typing import Any, Iterable, Mapping

CAN_EXECUTE = False
MIN_CALIBRATION_N = 30
BINARY_ARTIFACT_TYPE = "BINARY_PLATT_CALIBRATOR"
MULTICLASS_ARTIFACT_TYPE = "MULTICLASS_PLATT_CALIBRATOR"


class CalibrationArtifactInvalid(ValueError):
    code = "MODEL_INPUTS_INSUFFICIENT"

    def __init__(self, blockers: Iterable[str]):
        self.blockers = tuple(dict.fromkeys(str(v) for v in blockers if v))
        super().__init__(",".join(self.blockers))


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _prob(value: Any) -> float | None:
    parsed = _num(value)
    return parsed if parsed is not None and 0.0 < parsed < 1.0 else None


def _aware(value: Any) -> datetime | None:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _hash64(value: Any) -> bool:
    token = str(value or "").strip().lower()
    return len(token) == 64 and all(ch in "0123456789abcdef" for ch in token)


def _logit(p: float) -> float:
    safe = min(max(float(p), 1e-6), 1.0 - 1e-6)
    return math.log(safe / (1.0 - safe))


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    ex = math.exp(x)
    return ex / (1.0 + ex)


def _platt(raw_probability: float, a: float, b: float) -> float:
    return min(max(_sigmoid(a * _logit(raw_probability) + b), 0.001), 0.999)


def _common_blockers(artifact: Mapping[str, Any], sport: str) -> list[str]:
    blockers: list[str] = []
    if str(artifact.get("sport") or "").upper() != sport.upper():
        blockers.append("CALIBRATION_ARTIFACT_SPORT_MISMATCH")
    if str(artifact.get("health_status") or "").upper() != "PASS":
        blockers.append("CALIBRATION_HEALTH_NOT_PASS")
    if str(artifact.get("certification_status") or "").upper() != "PASS":
        blockers.append("CALIBRATION_CERTIFICATION_NOT_PASS")
    training_n = _num(artifact.get("training_n"))
    if training_n is None or training_n < MIN_CALIBRATION_N:
        blockers.append("CALIBRATION_TRAINING_N_INSUFFICIENT")
    if not str(artifact.get("calibration_method") or "").strip():
        blockers.append("CALIBRATION_METHOD_MISSING")
    if not str(artifact.get("calibration_version") or "").strip():
        blockers.append("CALIBRATION_VERSION_MISSING")
    if not _hash64(artifact.get("source_data_hash")):
        blockers.append("CALIBRATION_SOURCE_DATA_HASH_INVALID")
    if not _hash64(artifact.get("split_hash")):
        blockers.append("CALIBRATION_SPLIT_HASH_INVALID")
    if _aware(artifact.get("fit_end")) is None:
        blockers.append("CALIBRATION_FIT_END_INVALID")
    brier = _num(artifact.get("brier_score"))
    if brier is None or not 0.0 <= brier <= 1.0:
        blockers.append("CALIBRATION_BRIER_INVALID")
    error = _num(artifact.get("calibration_error"))
    if error is None or not 0.0 <= error <= 1.0:
        blockers.append("CALIBRATION_ERROR_INVALID")
    return blockers


def validate_binary_artifact(artifact: Mapping[str, Any], sport: str) -> dict[str, Any]:
    blockers = _common_blockers(artifact, sport)
    if str(artifact.get("artifact_type") or "") != BINARY_ARTIFACT_TYPE:
        blockers.append("BINARY_CALIBRATION_ARTIFACT_TYPE_INVALID")
    a = _num(artifact.get("platt_a"))
    b = _num(artifact.get("platt_b"))
    residual = _num(artifact.get("residual_quantile_90"))
    if a is None or b is None:
        blockers.append("PLATT_COEFFICIENTS_INVALID")
    if residual is None or not 0.0 < residual < 1.0:
        blockers.append("CALIBRATION_RESIDUAL_QUANTILE_INVALID")
    if blockers:
        raise CalibrationArtifactInvalid(blockers)
    return dict(artifact)


def validate_multiclass_artifact(artifact: Mapping[str, Any], sport: str = "SOCCER") -> dict[str, Any]:
    blockers = _common_blockers(artifact, sport)
    if str(artifact.get("artifact_type") or "") != MULTICLASS_ARTIFACT_TYPE:
        blockers.append("MULTICLASS_CALIBRATION_ARTIFACT_TYPE_INVALID")
    outcomes = artifact.get("outcomes")
    if not isinstance(outcomes, Mapping):
        blockers.append("MULTICLASS_CALIBRATION_OUTCOMES_MISSING")
    else:
        for outcome in ("HOME", "DRAW", "AWAY"):
            row = outcomes.get(outcome)
            if not isinstance(row, Mapping):
                blockers.append(f"{outcome}_CALIBRATION_RECORD_MISSING")
                continue
            if _num(row.get("platt_a")) is None or _num(row.get("platt_b")) is None:
                blockers.append(f"{outcome}_PLATT_COEFFICIENTS_INVALID")
            residual = _num(row.get("residual_quantile_90"))
            if residual is None or not 0.0 < residual < 1.0:
                blockers.append(f"{outcome}_RESIDUAL_QUANTILE_INVALID")
    if blockers:
        raise CalibrationArtifactInvalid(blockers)
    return dict(artifact)


def apply_binary_artifact(raw_probability: float, artifact: Mapping[str, Any], sport: str) -> dict[str, Any]:
    valid = validate_binary_artifact(artifact, sport)
    calibrated = _platt(
        raw_probability,
        float(valid["platt_a"]),
        float(valid["platt_b"]),
    )
    return {
        "calibrated_probability": calibrated,
        "historical_residual_quantile_90": float(valid["residual_quantile_90"]),
        "calibration_method": str(valid["calibration_method"]),
        "calibration_version": str(valid["calibration_version"]),
        "calibration_training_n": int(valid["training_n"]),
        "calibration_brier_score": float(valid["brier_score"]),
        "calibration_error": float(valid["calibration_error"]),
        "calibration_source_data_hash": str(valid["source_data_hash"]),
        "calibration_split_hash": str(valid["split_hash"]),
        "calibration_fit_end": str(valid["fit_end"]),
        "calibration_health_status": "PASS",
    }


def apply_multiclass_artifact(raw: Mapping[str, float], artifact: Mapping[str, Any]) -> dict[str, Any]:
    valid = validate_multiclass_artifact(artifact, "SOCCER")
    outcomes = valid["outcomes"]
    transformed: dict[str, float] = {}
    for outcome in ("HOME", "DRAW", "AWAY"):
        row = outcomes[outcome]
        transformed[outcome] = _platt(
            float(raw[outcome]),
            float(row["platt_a"]),
            float(row["platt_b"]),
        )
    total = sum(transformed.values())
    calibrated = {key: value / total for key, value in transformed.items()}
    residuals = {
        key: float(outcomes[key]["residual_quantile_90"])
        for key in ("HOME", "DRAW", "AWAY")
    }
    return {
        "calibrated_outcomes": calibrated,
        "historical_residual_quantiles_90": residuals,
        "calibration_method": str(valid["calibration_method"]),
        "calibration_version": str(valid["calibration_version"]),
        "calibration_training_n": int(valid["training_n"]),
        "calibration_brier_score": float(valid["brier_score"]),
        "calibration_error": float(valid["calibration_error"]),
        "calibration_source_data_hash": str(valid["source_data_hash"]),
        "calibration_split_hash": str(valid["split_hash"]),
        "calibration_fit_end": str(valid["fit_end"]),
        "calibration_health_status": "PASS",
    }


_CANONICAL_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\b": "\\b",
    "\f": "\\f",
}


class CalibrationArtifactDigestError(ValueError):
    """Raised when a calibration payload does not match its declared digest."""

    code = "MODEL_OUTPUT_INVALID"


def _canonical_string(value: str) -> str:
    out = ['"']
    for char in value:
        escaped = _CANONICAL_ESCAPES.get(char)
        if escaped is not None:
            out.append(escaped)
        elif char < " ":
            out.append("\\u%04x" % ord(char))
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _canonical_number(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CalibrationArtifactDigestError("CALIBRATION_ARTIFACT_NUMBER_INVALID") from exc
    if not math.isfinite(number):
        raise CalibrationArtifactDigestError("CALIBRATION_ARTIFACT_NUMBER_INVALID")
    # Signed zero and integral floats keep the PostgreSQL jsonb numeric rendering
    # ("0.0", "240.0") so the database can recompute the same digest bytes.
    if number == 0.0:
        return "0.0"
    if number.is_integer() and abs(number) < 1e16:
        return f"{number:.1f}"
    text = repr(number)
    if "e" in text or "E" in text:
        # jsonb numeric rendering never uses exponent notation.
        text = format(Decimal(text), "f")
    return text


def _canonical_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _canonical_string(value)
    if isinstance(value, (int, float)):
        return _canonical_number(value)
    if isinstance(value, Mapping):
        for key in value:
            if not isinstance(key, str):
                raise CalibrationArtifactDigestError("CALIBRATION_ARTIFACT_KEY_INVALID")
        return "{" + ",".join(
            f"{_canonical_string(key)}:{_canonical_value(value[key])}"
            for key in sorted(value)
        ) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical_value(item) for item in value) + "]"
    # Preserve the historical `default=str` behaviour for non-JSON scalars
    # (for example numpy integers) as a canonical JSON string.
    return _canonical_string(str(value))


def canonical_calibration_json(payload: Any) -> str:
    """Return the canonical calibration-artifact serialization.

    Single source of truth shared with the PostgreSQL side: sorted keys, compact
    separators, UTF-8 text preserved, numeric scale preserved. The byte string
    produced here is what `public.wow_canonical_json_text(jsonb)` must reproduce.
    Accepts any JSON-shaped value so nested evidence rows (source rows, split
    rows, fold audits) can be bound by the same rules as the artifact itself.
    """
    return _canonical_value(payload)


def calibration_payload_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_calibration_json(payload).encode("utf-8")).hexdigest()


def normalize_payload_sha256(value: Any) -> str:
    token = str(value or "").strip().lower()
    if len(token) != 64 or any(ch not in "0123456789abcdef" for ch in token):
        raise CalibrationArtifactDigestError("CALIBRATION_ARTIFACT_SHA256_INVALID")
    return token


def verify_calibration_payload_digest(
    payload: Mapping[str, Any],
    *,
    declared_sha256: Any,
    certified_sha256: Any,
) -> str:
    """Cryptographically verify a calibration payload against its digest.

    The digest is recomputed from the payload content itself. A caller-supplied
    (or stored) digest is never trusted: it must equal the independently
    certified digest as well.
    """
    if not isinstance(payload, Mapping):
        raise CalibrationArtifactDigestError("CALIBRATION_ARTIFACT_PAYLOAD_INVALID")
    declared = normalize_payload_sha256(declared_sha256)
    certified = normalize_payload_sha256(certified_sha256)
    computed = calibration_payload_sha256(payload)
    if computed != declared or computed != certified or declared != certified:
        raise CalibrationArtifactDigestError("CALIBRATION_ARTIFACT_PAYLOAD_DIGEST_MISMATCH")
    return computed


def artifact_fingerprint(artifact: Mapping[str, Any]) -> str:
    return calibration_payload_sha256(dict(artifact))


__all__ = [
    "BINARY_ARTIFACT_TYPE",
    "CAN_EXECUTE",
    "CalibrationArtifactDigestError",
    "CalibrationArtifactInvalid",
    "MIN_CALIBRATION_N",
    "MULTICLASS_ARTIFACT_TYPE",
    "apply_binary_artifact",
    "apply_multiclass_artifact",
    "artifact_fingerprint",
    "calibration_payload_sha256",
    "canonical_calibration_json",
    "normalize_payload_sha256",
    "validate_binary_artifact",
    "validate_multiclass_artifact",
    "verify_calibration_payload_digest",
]
