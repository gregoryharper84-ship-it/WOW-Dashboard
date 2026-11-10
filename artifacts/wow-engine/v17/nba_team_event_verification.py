"""Independent verification harness for one NBA team-event fitted artifact.

This module produces the receipt described by
`v17/engineering-fixes/NBA-INDEPENDENT-VERIFICATION-2026-11-10.md` and
`v17/engineering-fixes/nba_team_event_verification_receipt.schema.json`.

Properties:

- read-only: it only reads registry rows through a `VerificationSource`;
- recomputing: metrics are re-derived from the corpus, never copied from the
  rows being reviewed;
- fail-closed: any missing input is a FAIL check, never a partial PASS;
- non-authoritative: `CAN_EXECUTE` is False, `publishable_scope` is always
  "NONE", and a PASS removes exactly one blocker
  (`TEAM_EVENT_INDEPENDENT_VERIFICATION_NOT_PASS`). It never authorizes
  publication, ranking, or execution.

A receipt that cannot be constructed from real rows raises
`VerificationContextError` instead of emitting a receipt with placeholder
digests. Absence of evidence is not a digest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Protocol, Sequence

import numpy as np

from basketball_specialist_pipeline import (
    _walk_forward_raw,
    disjoint_calibration_and_holdout,
)
from basketball_team_event_specialist import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    MIN_HOLDOUT_ROWS,
    MIN_TRAIN_ROWS,
    MODEL_FAMILY,
    TrainingGame,
    _metrics,
    build_pregame_features,
    certification_decision,
    train_specialist,
)
from calibration import phase_b_platt

can_execute: bool = False
CAN_EXECUTE = False

SCHEMA_VERSION = "WOW_V17_TEAM_EVENT_INDEPENDENT_VERIFICATION_V1"
HARNESS_ID = "wow-v17-nba-team-event-verification"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
PUBLISHABLE_SCOPE = "NONE"

SPORT = "NBA"
LEAGUE_SCOPE = "NBA"
MAX_CORPUS_AGE_DAYS = 400
MIN_CALIBRATION_ROWS = 200
MAX_HOLDOUT_BRIER = 0.25
MAX_HOLDOUT_LOG_LOSS = math.log(2.0)
ALLOWED_CALIBRATION_STATUSES = frozenset({"PLATT_TIME_SPLIT_V1", "ISOTONIC_V1"})
REQUIRED_EVIDENCE_REFS = (
    "corpus_evidence_ref",
    "artifact_evidence_ref",
    "calibration_evidence_ref",
    "replay_evidence_ref",
    "independent_verification_evidence_ref",
)
CHECK_IDS = tuple(f"V{i}" for i in range(1, 13))
ATTESTED_REPLAY_STATUS = "RAW_CAPTURE_HASH_AND_EVENT_REPLAY_PASS"
ATTESTED_TIMESTAMP_STATUS = "VERIFIED"
_FLOAT_TOLERANCE = 1e-12


class VerificationContextError(RuntimeError):
    """Raised when a receipt cannot be built from real evidence."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(detail or code)


class VerificationSource(Protocol):
    """Read-only view of the governed rows this harness may consult."""

    def corpus_rows(self, sport: str) -> Sequence[Mapping[str, Any]]: ...

    def feature_rows(self, sport: str) -> Sequence[Mapping[str, Any]]: ...

    def artifact_row(self, artifact_id: str) -> Mapping[str, Any] | None: ...

    def calibrator_row(self, model_artifact_version: str) -> Mapping[str, Any] | None: ...

    def capability_row(self, sport: str) -> Mapping[str, Any] | None: ...

    def attestation(self) -> Mapping[str, Any]: ...

    def certification_row(self, model_artifact_version: str) -> Mapping[str, Any] | None: ...


@dataclass(frozen=True)
class VerificationRequest:
    """Everything the caller must supply that the registry cannot prove."""

    artifact_id: str
    controlling_specialist: str
    input_contract_version: str
    independent_verifier: str
    implementation_owner: str
    verification_owner: str
    evidence_refs: Mapping[str, str]
    sport: str = SPORT
    league_scope: str = LEAGUE_SCOPE
    certification_id_attested: str | None = None
    max_ece: float | None = None
    as_of: datetime | None = None


@dataclass
class _Check:
    check_id: str
    status: str = "PASS"
    observed: dict[str, Any] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)

    def fail(self, *blockers: str) -> "_Check":
        self.status = "FAIL"
        for blocker in blockers:
            if blocker and blocker not in self.blockers:
                self.blockers.append(blocker)
        return self

    def as_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "status": self.status,
            "observed": dict(self.observed),
            "blockers": list(self.blockers),
        }


def _sha256_canonical(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _is_hex64(value: Any) -> bool:
    token = str(value or "").strip().lower()
    return len(token) == 64 and all(ch in "0123456789abcdef" for ch in token)


def _aware(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _close(left: Any, right: Any) -> bool:
    try:
        lv, rv = float(left), float(right)
    except (TypeError, ValueError):
        return False
    if not (math.isfinite(lv) and math.isfinite(rv)):
        return False
    return abs(lv - rv) <= _FLOAT_TOLERANCE


def _resolve_code_sha(explicit: str | None) -> str:
    for candidate in (
        explicit,
        os.environ.get("WOW_CODE_SHA"),
        os.environ.get("GITHUB_SHA"),
    ):
        token = str(candidate or "").strip().lower()
        if 40 <= len(token) <= 64 and all(ch in "0123456789abcdef" for ch in token):
            return token
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - env dependent
        raise VerificationContextError("CODE_SHA_UNAVAILABLE", str(exc)) from exc
    token = (result.stdout or "").strip().lower()
    if result.returncode == 0 and 40 <= len(token) <= 64 and all(
        ch in "0123456789abcdef" for ch in token
    ):
        return token
    raise VerificationContextError("CODE_SHA_UNAVAILABLE", "supply code_sha explicitly")


def _training_games(rows: Iterable[Mapping[str, Any]], sport: str) -> list[TrainingGame]:
    games: list[TrainingGame] = []
    for row in rows:
        try:
            games.append(
                TrainingGame(
                    game_id=str(row["game_id"]),
                    sport=sport,
                    season=int(row["season"]),
                    game_date=date.fromisoformat(str(row["game_date"])[:10]),
                    home_team_id=str(row["home_team_id"]),
                    away_team_id=str(row["away_team_id"]),
                    home_score=int(row["home_score"]),
                    away_score=int(row["away_score"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise VerificationContextError(
                "CORPUS_ROW_INVALID", f"{row.get('game_id')}: {exc}"
            ) from exc
    if not games:
        raise VerificationContextError("CORPUS_EMPTY", "no settled corpus rows")
    return games


def _recompute(
    games: Sequence[TrainingGame],
    *,
    model_artifact_version: str,
) -> dict[str, Any]:
    """Rebuild features, artifact, calibration and holdout from the corpus."""
    features = build_pregame_features(games, SPORT)
    artifact = train_specialist(
        features, SPORT, model_artifact_version=model_artifact_version
    )
    calibration_train, holdout = disjoint_calibration_and_holdout(features, artifact)
    raw, outcomes, folds, timestamps = _walk_forward_raw(calibration_train)
    fit = phase_b_platt(raw, outcomes, folds, timestamps)
    holdout_raw = np.asarray(
        [artifact.raw_probability(row.values) for row in holdout], dtype=float
    )
    holdout_outcomes = np.asarray([row.outcome for row in holdout], dtype=float)
    brier, log_loss, accuracy = _metrics(holdout_raw, holdout_outcomes)
    return {
        "features": features,
        "artifact": artifact,
        "calibration_train": calibration_train,
        "holdout": holdout,
        "raw": raw,
        "calibration_n": len(raw),
        "calibration_status": (
            fit.result.calibration_status if fit.result is not None else None
        ),
        "platt_a": float(fit.coefficients.a),
        "platt_b": float(fit.coefficients.b),
        "calibration_brier": float(fit.metrics.brier),
        "calibration_log_loss": float(fit.metrics.log_loss),
        "calibration_ece": float(fit.metrics.ece),
        "calibration_bias": float(fit.metrics.calibration_bias),
        "holdout_brier": float(brier),
        "holdout_log_loss": float(log_loss),
        "holdout_accuracy": float(accuracy),
        "holdout_rows": len(holdout),
    }


def _persisted_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    payload = row.get("feature_payload")
    if isinstance(payload, Mapping):
        return dict(payload)
    return {}


def verify_nba_team_event(
    source: VerificationSource,
    request: VerificationRequest,
    *,
    verification_id: str | None = None,
    created_at: datetime | None = None,
    code_sha: str | None = None,
    environment: str = "UNSPECIFIED_LOCAL",
    replay_command: str | None = None,
) -> dict[str, Any]:
    """Run V1-V12 for one NBA artifact and return the verification receipt."""
    if str(request.sport).upper() != SPORT or str(request.league_scope).upper() != LEAGUE_SCOPE:
        raise VerificationContextError(
            "SPORT_NOT_SUPPORTED_BY_HARNESS", "this harness verifies NBA only"
        )

    resolved_code_sha = _resolve_code_sha(code_sha)
    now = (created_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    as_of = (request.as_of or now).astimezone(timezone.utc)

    corpus = list(source.corpus_rows(SPORT))
    games = _training_games(corpus, SPORT)

    artifact_row = source.artifact_row(request.artifact_id)
    if not isinstance(artifact_row, Mapping):
        raise VerificationContextError(
            "ARTIFACT_ROW_NOT_FOUND", str(request.artifact_id)
        )
    model_version = str(artifact_row.get("model_artifact_version") or "").strip()
    artifact_checksum = str(artifact_row.get("artifact_checksum") or "").strip().lower()
    if not model_version or not _is_hex64(artifact_checksum):
        raise VerificationContextError(
            "ARTIFACT_ROW_IDENTITY_INVALID",
            "model_artifact_version and 64-hex artifact_checksum are required",
        )

    calibrator_row = source.calibrator_row(model_version)
    if not isinstance(calibrator_row, Mapping):
        raise VerificationContextError("CALIBRATOR_ROW_NOT_FOUND", model_version)
    calibration_id = str(
        calibrator_row.get("calibrator_version")
        or calibrator_row.get("calibration_artifact_id")
        or ""
    ).strip()
    # The reducer compares the received calibration fingerprint against
    # certification.calibration_sha256, so a missing digest is a context error,
    # never an empty value filled in here.
    calibration_sha = str(
        calibrator_row.get("calibration_sha256")
        or calibrator_row.get("calibration_artifact_fingerprint")
        or calibrator_row.get("coefficients_sha256")
        or ""
    ).strip().lower()
    if not _is_hex64(calibration_sha):
        raise VerificationContextError(
            "CALIBRATION_DIGEST_MISSING",
            "calibrator row must carry a 64-hex calibration digest",
        )

    capability_row = source.capability_row(SPORT)
    certification_row = None
    resolver = getattr(source, "certification_row", None)
    if callable(resolver):
        certification_row = resolver(model_version)

    checks: dict[str, _Check] = {cid: _Check(cid) for cid in CHECK_IDS}
    limitations: list[str] = []
    metrics: dict[str, Any] | None = None

    # ---- V1 corpus provenance -------------------------------------------------
    missing_provenance: list[str] = []
    for row in corpus:
        bad = (
            not str(row.get("source_provider") or "").strip()
            or not str(row.get("source_endpoint") or "").strip()
            or _aware(row.get("source_retrieved_at")) is None
            or not _is_hex64(row.get("source_payload_sha256"))
        )
        if bad and len(missing_provenance) < 10:
            missing_provenance.append(str(row.get("game_id") or "UNKNOWN"))
    checks["V1"].observed = {
        "settled_rows": len(corpus),
        "rows_missing_provenance": len(missing_provenance),
        "sample_game_ids": missing_provenance,
    }
    if missing_provenance:
        checks["V1"].fail(*(f"CORPUS_PROVENANCE_INCOMPLETE:{gid}" for gid in missing_provenance))

    # ---- V2 corpus freshness --------------------------------------------------
    latest = max(game.game_date for game in games)
    age_days = (as_of.date() - latest).days
    checks["V2"].observed = {
        "latest_game_date": latest.isoformat(),
        "as_of_date": as_of.date().isoformat(),
        "age_days": age_days,
        "max_age_days": MAX_CORPUS_AGE_DAYS,
    }
    if age_days < 0:
        checks["V2"].fail("CORPUS_LATEST_GAME_IN_FUTURE")
    elif age_days > MAX_CORPUS_AGE_DAYS:
        checks["V2"].fail(f"CORPUS_STALE:{age_days}>{MAX_CORPUS_AGE_DAYS}")

    # ---- V3 pagination integrity ---------------------------------------------
    seen: set[str] = set()
    duplicates: list[str] = []
    for game in games:
        if game.game_id in seen and len(duplicates) < 10:
            duplicates.append(game.game_id)
        seen.add(game.game_id)
    checks["V3"].observed = {
        "rows": len(games),
        "unique_game_ids": len(seen),
        "duplicate_game_ids": duplicates,
    }
    if duplicates:
        checks["V3"].fail(*(f"CORPUS_DUPLICATE_GAME_ID:{gid}" for gid in duplicates))

    # ---- Recompute once (V4-V9) ----------------------------------------------
    recomputed: dict[str, Any] | None = None
    try:
        recomputed = _recompute(games, model_artifact_version=model_version)
    except Exception as exc:  # noqa: BLE001 - surfaced as a typed FAIL check
        blocker = f"RECOMPUTATION_FAILED:{type(exc).__name__}:{exc}"
        for cid in ("V4", "V5", "V6", "V7", "V8"):
            checks[cid].fail(blocker)

    if recomputed is not None:
        # ---- V4 feature determinism -----------------------------------------
        persisted = {str(row.get("game_id")): row for row in source.feature_rows(SPORT)}
        payload_mismatch: list[str] = []
        missing_rows: list[str] = []
        missing_features: list[str] = []
        for feature in recomputed["features"]:
            row = persisted.get(feature.game_id)
            if row is None:
                if len(missing_rows) < 10:
                    missing_rows.append(feature.game_id)
                continue
            payload = _persisted_payload(row)
            values = payload.get("features")
            if (
                str(payload.get("feature_schema_version") or "") != FEATURE_SCHEMA_VERSION
                or not isinstance(values, Mapping)
                or set(values) != set(FEATURE_NAMES)
            ):
                if len(missing_features) < 10:
                    missing_features.append(feature.game_id)
                continue
            recomputed_values = {
                name: float(value) for name, value in zip(FEATURE_NAMES, feature.values)
            }
            if any(
                not _close(values[name], recomputed_values[name]) for name in FEATURE_NAMES
            ):
                if len(payload_mismatch) < 10:
                    payload_mismatch.append(feature.game_id)
                continue
            if _sha256_canonical(payload) != str(
                row.get("feature_payload_sha256") or ""
            ).strip().lower():
                if len(payload_mismatch) < 10:
                    payload_mismatch.append(feature.game_id)
        checks["V4"].observed = {
            "recomputed_rows": len(recomputed["features"]),
            "persisted_rows": len(persisted),
            "payload_mismatches": payload_mismatch,
            "missing_persisted_rows": missing_rows,
            "schema_mismatches": missing_features,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "feature_names": list(FEATURE_NAMES),
        }
        if missing_rows:
            checks["V4"].fail(*(f"FEATURE_ROW_MISSING:{gid}" for gid in missing_rows))
        if missing_features:
            checks["V4"].fail(*(f"FEATURE_SCHEMA_MISMATCH:{gid}" for gid in missing_features))
        if payload_mismatch:
            checks["V4"].fail(
                *(f"FEATURE_PAYLOAD_MISMATCH:{gid}" for gid in payload_mismatch)
            )
        if len(persisted) != len(recomputed["features"]):
            checks["V4"].fail(
                f"FEATURE_ROW_COUNT_MISMATCH:{len(persisted)}!={len(recomputed['features'])}"
            )

        # ---- V5 artifact checksum -------------------------------------------
        payload = artifact_row.get("artifact_payload")
        payload_fields = (
            "sport",
            "model_family",
            "model_artifact_version",
            "feature_schema_version",
            "feature_names",
            "intercept",
            "coefficients",
            "means",
            "scales",
        )
        recomputed_checksum = ""
        payload_ok = isinstance(payload, Mapping) and all(
            name in payload for name in payload_fields
        )
        if payload_ok:
            recomputed_checksum = _sha256_canonical(
                {name: payload[name] for name in payload_fields}
            )
        refit_checksum = str(recomputed["artifact"].artifact_checksum)
        checks["V5"].observed = {
            "stored_artifact_checksum": artifact_checksum,
            "recomputed_from_payload": recomputed_checksum,
            "recomputed_from_corpus": refit_checksum,
            "payload_fields_present": payload_ok,
            "model_family": payload.get("model_family") if isinstance(payload, Mapping) else None,
        }
        if not payload_ok:
            checks["V5"].fail("ARTIFACT_PAYLOAD_FIELDS_MISSING")
        elif recomputed_checksum != artifact_checksum:
            checks["V5"].fail("ARTIFACT_CHECKSUM_MISMATCH")
        if refit_checksum != artifact_checksum:
            checks["V5"].fail("ARTIFACT_CHECKSUM_DOES_NOT_MATCH_CORPUS_REFIT")

        # ---- V6 holdout reproducibility -------------------------------------
        stored_brier = artifact_row.get("holdout_brier")
        stored_log_loss = artifact_row.get("holdout_log_loss")
        stored_accuracy = artifact_row.get("holdout_accuracy")
        checks["V6"].observed = {
            "stored_holdout_rows": artifact_row.get("holdout_row_count"),
            "recomputed_holdout_rows": recomputed["holdout_rows"],
            "stored_training_rows": artifact_row.get("training_row_count"),
            "recomputed_training_rows": recomputed["artifact"].training_row_count,
            "stored_brier": stored_brier,
            "recomputed_brier": recomputed["holdout_brier"],
            "stored_log_loss": stored_log_loss,
            "recomputed_log_loss": recomputed["holdout_log_loss"],
            "stored_accuracy": stored_accuracy,
            "recomputed_accuracy": recomputed["holdout_accuracy"],
            "train_end_date": str(artifact_row.get("train_end_date") or ""),
        }
        if not _close(stored_brier, recomputed["holdout_brier"]):
            checks["V6"].fail("HOLDOUT_BRIER_NOT_REPRODUCIBLE")
        if not _close(stored_log_loss, recomputed["holdout_log_loss"]):
            checks["V6"].fail("HOLDOUT_LOG_LOSS_NOT_REPRODUCIBLE")
        if not _close(stored_accuracy, recomputed["holdout_accuracy"]):
            checks["V6"].fail("HOLDOUT_ACCURACY_NOT_REPRODUCIBLE")
        if int(artifact_row.get("holdout_row_count") or -1) != recomputed["holdout_rows"]:
            checks["V6"].fail("HOLDOUT_ROW_COUNT_MISMATCH")
        if (
            int(artifact_row.get("training_row_count") or -1)
            != recomputed["artifact"].training_row_count
        ):
            checks["V6"].fail("TRAINING_ROW_COUNT_MISMATCH")
        max_calibration_date = max(
            row.game_date for row in recomputed["calibration_train"]
        )
        min_holdout_date = min(row.game_date for row in recomputed["holdout"])
        checks["V6"].observed["calibration_max_date"] = max_calibration_date.isoformat()
        checks["V6"].observed["holdout_min_date"] = min_holdout_date.isoformat()
        if not max_calibration_date < min_holdout_date:
            checks["V6"].fail("CALIBRATION_HOLDOUT_OVERLAP")

        # ---- V7 calibration reproducibility ---------------------------------
        coefficients = calibrator_row.get("coefficients")
        coefficients = coefficients if isinstance(coefficients, Mapping) else {}
        stored_a = coefficients.get("a")
        stored_b = coefficients.get("b")
        fit_end = _aware(calibrator_row.get("calibration_fit_end"))
        model_at = _aware(
            artifact_row.get("created_at")
            or artifact_row.get("certified_at")
            or artifact_row.get("fitted_at_utc")
        )
        status = recomputed["calibration_status"]
        checks["V7"].observed = {
            "stored_a": stored_a,
            "recomputed_a": recomputed["platt_a"],
            "stored_b": stored_b,
            "recomputed_b": recomputed["platt_b"],
            "stored_calibration_rows": calibrator_row.get("calibration_row_count"),
            "recomputed_calibration_rows": recomputed["calibration_n"],
            "stored_brier": calibrator_row.get("brier"),
            "recomputed_brier": recomputed["calibration_brier"],
            "stored_log_loss": calibrator_row.get("log_loss"),
            "recomputed_log_loss": recomputed["calibration_log_loss"],
            "stored_ece": calibrator_row.get("ece"),
            "recomputed_ece": recomputed["calibration_ece"],
            "stored_bias": calibrator_row.get("calibration_bias"),
            "recomputed_bias": recomputed["calibration_bias"],
            "calibration_method": calibrator_row.get("calibration_method"),
            "recomputed_status": status,
            "calibration_fit_end": fit_end.isoformat() if fit_end else None,
            "model_registered_at": model_at.isoformat() if model_at else None,
        }
        if not _close(stored_a, recomputed["platt_a"]) or not _close(
            stored_b, recomputed["platt_b"]
        ):
            checks["V7"].fail("PLATT_COEFFICIENTS_NOT_REPRODUCIBLE")
        if not _close(calibrator_row.get("brier"), recomputed["calibration_brier"]):
            checks["V7"].fail("CALIBRATION_BRIER_NOT_REPRODUCIBLE")
        if not _close(calibrator_row.get("log_loss"), recomputed["calibration_log_loss"]):
            checks["V7"].fail("CALIBRATION_LOG_LOSS_NOT_REPRODUCIBLE")
        if not _close(calibrator_row.get("ece"), recomputed["calibration_ece"]):
            checks["V7"].fail("CALIBRATION_ECE_NOT_REPRODUCIBLE")
        if not _close(
            calibrator_row.get("calibration_bias"), recomputed["calibration_bias"]
        ):
            checks["V7"].fail("CALIBRATION_BIAS_NOT_REPRODUCIBLE")
        if int(calibrator_row.get("calibration_row_count") or -1) != recomputed["calibration_n"]:
            checks["V7"].fail("CALIBRATION_ROW_COUNT_MISMATCH")
        if recomputed["calibration_n"] < MIN_CALIBRATION_ROWS:
            checks["V7"].fail(
                f"CALIBRATION_ROWS_BELOW_MINIMUM:{recomputed['calibration_n']}<{MIN_CALIBRATION_ROWS}"
            )
        if status not in ALLOWED_CALIBRATION_STATUSES:
            checks["V7"].fail(f"CALIBRATION_STATUS_NOT_ALLOWED:{status}")
        if fit_end is None:
            checks["V7"].fail("CALIBRATION_FIT_END_MISSING")
        elif model_at is None:
            checks["V7"].fail("MODEL_REGISTRATION_TIME_MISSING")
        elif fit_end > model_at:
            checks["V7"].fail("CALIBRATION_FIT_END_AFTER_MODEL_REGISTRATION")

        # ---- V8 quantified fit gates ----------------------------------------
        decision = certification_decision(
            recomputed["artifact"],
            sport=SPORT,
            calibration_rows=recomputed["calibration_n"],
            calibration_status=status,
            provenance_complete=True,
        )
        checks["V8"].observed = {
            "capability_status": decision.capability_status,
            "reason_code": decision.reason_code,
            "training_rows": recomputed["artifact"].training_row_count,
            "min_training_rows": MIN_TRAIN_ROWS,
            "holdout_rows": recomputed["holdout_rows"],
            "min_holdout_rows": MIN_HOLDOUT_ROWS,
            "holdout_brier": recomputed["holdout_brier"],
            "max_holdout_brier": MAX_HOLDOUT_BRIER,
            "holdout_log_loss": recomputed["holdout_log_loss"],
            "max_holdout_log_loss": MAX_HOLDOUT_LOG_LOSS,
        }
        if decision.capability_status != "CERTIFIED":
            checks["V8"].fail(f"FIT_GATE_NOT_MET:{decision.reason_code}")
        if recomputed["artifact"].training_row_count < MIN_TRAIN_ROWS:
            checks["V8"].fail("TRAINING_ROWS_BELOW_MINIMUM")
        if recomputed["holdout_rows"] < MIN_HOLDOUT_ROWS:
            checks["V8"].fail("HOLDOUT_ROWS_BELOW_MINIMUM")
        if not recomputed["holdout_brier"] < MAX_HOLDOUT_BRIER:
            checks["V8"].fail("HOLDOUT_BRIER_ABOVE_GATE")
        if not recomputed["holdout_log_loss"] < MAX_HOLDOUT_LOG_LOSS:
            checks["V8"].fail("HOLDOUT_LOG_LOSS_ABOVE_GATE")

        # ---- V9 calibration error recorded ----------------------------------
        ece = recomputed["calibration_ece"]
        checks["V9"].observed = {
            "ece": ece,
            "calibration_bias": recomputed["calibration_bias"],
            "max_ece": request.max_ece,
            "code_gate_exists": False,
        }
        if not math.isfinite(ece):
            checks["V9"].fail("CALIBRATION_ECE_NOT_COMPUTABLE")
        elif request.max_ece is not None and ece > float(request.max_ece):
            checks["V9"].fail(f"CALIBRATION_ECE_ABOVE_DECLARED_MAXIMUM:{ece}>{request.max_ece}")
        if request.max_ece is None:
            limitations.append(
                "V9: no ECE gate exists in certification_decision; the reported value is advisory"
            )
        else:
            limitations.append(
                f"V9: ECE threshold {request.max_ece} was imposed by the caller, not by repository code"
            )

        metrics = {
            "training_rows": int(recomputed["artifact"].training_row_count),
            "holdout_rows": int(recomputed["holdout_rows"]),
            "calibration_rows": int(recomputed["calibration_n"]),
            "holdout_brier": recomputed["holdout_brier"],
            "holdout_log_loss": recomputed["holdout_log_loss"],
            "holdout_accuracy": recomputed["holdout_accuracy"],
            "calibration_brier": recomputed["calibration_brier"],
            "calibration_error_ece": recomputed["calibration_ece"],
            "calibration_bias": recomputed["calibration_bias"],
            "platt_a": recomputed["platt_a"],
            "platt_b": recomputed["platt_b"],
            "calibration_fit_end": fit_end.isoformat() if fit_end else "",
        }

    # ---- V10 identity consistency --------------------------------------------
    capability = capability_row if isinstance(capability_row, Mapping) else {}
    versions = {
        "artifact": model_version,
        "calibrator": str(calibrator_row.get("model_artifact_version") or ""),
        "capability": str(capability.get("model_artifact_version") or ""),
    }
    checks["V10"].observed = {
        "versions": versions,
        "capability_status": capability.get("capability_status"),
        "artifact_sport": artifact_row.get("sport"),
        "artifact_model_family": artifact_row.get("model_family"),
        "artifact_feature_schema": artifact_row.get("feature_schema_version"),
        "certification_model_version": (
            certification_row.get("model_version") if isinstance(certification_row, Mapping) else None
        ),
        "certification_artifact_sha256": (
            certification_row.get("artifact_sha256") if isinstance(certification_row, Mapping) else None
        ),
        "certification_status": (
            certification_row.get("certification_status") if isinstance(certification_row, Mapping) else None
        ),
    }
    if not isinstance(capability_row, Mapping):
        checks["V10"].fail("CAPABILITY_ROW_NOT_FOUND")
    else:
        if str(capability.get("capability_status") or "") != "CERTIFIED":
            checks["V10"].fail(
                f"CAPABILITY_STATUS_NOT_CERTIFIED:{capability.get('capability_status')}"
            )
    for label, version in versions.items():
        if version != model_version:
            checks["V10"].fail(f"MODEL_VERSION_MISMATCH:{label}:{version or 'MISSING'}")
    if str(artifact_row.get("sport") or "").upper() != SPORT:
        checks["V10"].fail("ARTIFACT_SPORT_MISMATCH")
    if str(artifact_row.get("model_family") or "") != MODEL_FAMILY:
        checks["V10"].fail("ARTIFACT_MODEL_FAMILY_MISMATCH")
    if str(artifact_row.get("feature_schema_version") or "") != FEATURE_SCHEMA_VERSION:
        checks["V10"].fail("ARTIFACT_FEATURE_SCHEMA_MISMATCH")
    if isinstance(certification_row, Mapping):
        if str(certification_row.get("model_version") or "") != model_version:
            checks["V10"].fail("CERTIFICATION_MODEL_VERSION_MISMATCH")
        if str(certification_row.get("artifact_sha256") or "").lower() != artifact_checksum:
            checks["V10"].fail("CERTIFICATION_ARTIFACT_SHA256_MISMATCH")
        if str(certification_row.get("calibration_sha256") or "").lower() != calibration_sha:
            checks["V10"].fail("CERTIFICATION_CALIBRATION_SHA256_MISMATCH")

    # ---- V11 source attestation capability -----------------------------------
    attestation = dict(source.attestation() or {})
    source_attestation = {
        "source_replay_status": attestation.get("source_replay_status"),
        "timestamp_attestation": attestation.get("timestamp_attestation"),
        "dataset_id": attestation.get("dataset_id"),
    }
    checks["V11"].observed = dict(source_attestation)
    if source_attestation["source_replay_status"] != ATTESTED_REPLAY_STATUS:
        checks["V11"].fail(
            f"SOURCE_EVENT_REPLAY_NOT_ATTESTED:{source_attestation['source_replay_status']}"
        )
    if source_attestation["timestamp_attestation"] != ATTESTED_TIMESTAMP_STATUS:
        checks["V11"].fail(
            f"SOURCE_TIMESTAMP_NOT_ATTESTED:{source_attestation['timestamp_attestation']}"
        )
    if not str(source_attestation["dataset_id"] or "").strip():
        checks["V11"].fail("SOURCE_DATASET_ID_MISSING")

    # ---- V12 no substitution -------------------------------------------------
    substitution = {
        "market_features_used": attestation.get("market_features_used"),
        "market_probability_used_as_model": attestation.get(
            "market_probability_used_as_model"
        ),
        "generic_reasoning_used_as_model": attestation.get(
            "generic_reasoning_used_as_model"
        ),
    }
    checks["V12"].observed = dict(substitution)
    for name, value in substitution.items():
        if value is not False:
            checks["V12"].fail(f"MODEL_SUBSTITUTION_NOT_DISPROVEN:{name}")

    # ---- Verdict -------------------------------------------------------------
    independence_blockers: list[str] = []
    if not str(request.implementation_owner or "").strip():
        independence_blockers.append("IMPLEMENTATION_OWNER_MISSING")
    if not str(request.verification_owner or "").strip():
        independence_blockers.append("VERIFICATION_OWNER_MISSING")
    if request.implementation_owner and request.implementation_owner == request.verification_owner:
        independence_blockers.append("VERIFICATION_INDEPENDENCE_UNPROVEN")
    if not str(request.independent_verifier or "").strip():
        independence_blockers.append("INDEPENDENT_VERIFIER_MISSING")

    evidence_refs = {
        key: str(request.evidence_refs.get(key) or "").strip() for key in REQUIRED_EVIDENCE_REFS
    }
    evidence_blockers = [
        f"EVIDENCE_REF_MISSING:{key.upper()}"
        for key, value in evidence_refs.items()
        if not value
    ]

    failed_checks = [cid for cid, check in checks.items() if check.status == "FAIL"]
    not_applicable = [cid for cid, check in checks.items() if check.status == "NOT_APPLICABLE"]
    covered = {
        cid for cid in not_applicable if any(entry.startswith(f"{cid}:") for entry in limitations)
    }
    uncovered = [cid for cid in not_applicable if cid not in covered]

    verdict_blockers: list[str] = []
    verdict_blockers.extend(f"CHECK_FAILED:{cid}" for cid in failed_checks)
    verdict_blockers.extend(independence_blockers)
    verdict_blockers.extend(evidence_blockers)
    verdict_blockers.extend(f"NOT_APPLICABLE_UNEXPLAINED:{cid}" for cid in uncovered)
    if metrics is None:
        verdict_blockers.append("METRICS_UNAVAILABLE")

    passed = not verdict_blockers
    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "verification_id": verification_id or str(uuid.uuid4()),
        "sport": SPORT,
        "league_scope": LEAGUE_SCOPE,
        "controlling_specialist": request.controlling_specialist,
        "model_family": MODEL_FAMILY,
        "model_version": model_version,
        "model_artifact_id": str(artifact_row.get("artifact_id") or request.artifact_id),
        "model_artifact_sha256": artifact_checksum,
        "calibration_artifact_id": calibration_id,
        "calibration_sha256": calibration_sha,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "input_contract_version": request.input_contract_version,
        "certification_id_attested": request.certification_id_attested,
        "independent_verifier": request.independent_verifier,
        "implementation_owner": request.implementation_owner,
        "verification_owner": request.verification_owner,
        "independent_verification_status": "PASS" if passed else "FAIL",
        "method": {
            "harness": HARNESS_ID,
            "code_sha": resolved_code_sha,
            "environment": environment,
            "run_started_at_utc": now.isoformat(),
            "run_completed_at_utc": datetime.now(timezone.utc).isoformat(),
            **({"replay_command": replay_command} if replay_command else {}),
        },
        "checks": [checks[cid].as_dict() for cid in CHECK_IDS],
        "source_attestation": source_attestation,
        "evidence_refs": evidence_refs,
        "limitations": limitations,
        "verdict": {
            "publishable_scope": PUBLISHABLE_SCOPE,
            "certification_eligible": passed,
            "blockers": sorted(dict.fromkeys(verdict_blockers)),
        },
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
        "created_at_utc": now.isoformat(),
    }
    if metrics is not None:
        receipt["metrics"] = metrics
    return receipt


class MappingVerificationSource:
    """Offline source backed by one already-materialized bundle."""

    def __init__(self, bundle: Mapping[str, Any]):
        self._bundle = bundle

    def corpus_rows(self, sport: str) -> Sequence[Mapping[str, Any]]:
        return list(self._bundle.get("corpus") or [])

    def feature_rows(self, sport: str) -> Sequence[Mapping[str, Any]]:
        return list(self._bundle.get("feature_rows") or [])

    def artifact_row(self, artifact_id: str) -> Mapping[str, Any] | None:
        row = self._bundle.get("artifact")
        return row if isinstance(row, Mapping) else None

    def calibrator_row(self, model_artifact_version: str) -> Mapping[str, Any] | None:
        row = self._bundle.get("calibrator")
        return row if isinstance(row, Mapping) else None

    def capability_row(self, sport: str) -> Mapping[str, Any] | None:
        row = self._bundle.get("capability")
        return row if isinstance(row, Mapping) else None

    def attestation(self) -> Mapping[str, Any]:
        row = self._bundle.get("attestation")
        return row if isinstance(row, Mapping) else {}

    def certification_row(self, model_artifact_version: str) -> Mapping[str, Any] | None:
        row = self._bundle.get("certification")
        return row if isinstance(row, Mapping) else None


class SupabaseVerificationSource:
    """Read-only Supabase source. Never issues a write.

    The caller owns credential handling and passes an already-constructed
    client; this class only performs `.select(...)` reads.
    """

    CORPUS_COLUMNS = (
        "game_id,season,game_date,home_team_id,away_team_id,home_score,away_score,"
        "source_provider,source_endpoint,source_retrieved_at,source_payload_sha256,settled"
    )
    FEATURE_COLUMNS = (
        "game_id,as_of,feature_schema_version,home_games_prior,away_games_prior,"
        "feature_payload,feature_payload_sha256"
    )

    def __init__(self, client: Any, *, attestation: Mapping[str, Any] | None = None):
        self._client = client
        self._attestation = dict(attestation or {})

    def _paged(self, table: str, columns: str, **filters: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            query = self._client.table(table).select(columns)
            for key, value in filters.items():
                query = query.eq(key, value)
            batch = query.range(offset, offset + 999).execute().data or []
            rows.extend(batch)
            if len(batch) < 1000:
                return rows
            offset += 1000

    def corpus_rows(self, sport: str) -> Sequence[Mapping[str, Any]]:
        return self._paged("wow_nba_training_games", self.CORPUS_COLUMNS, settled=True)

    def feature_rows(self, sport: str) -> Sequence[Mapping[str, Any]]:
        return self._paged("wow_nba_pregame_feature_rows", self.FEATURE_COLUMNS)

    def artifact_row(self, artifact_id: str) -> Mapping[str, Any] | None:
        rows = (
            self._client.table("wow_basketball_team_event_model_artifacts")
            .select("*")
            .eq("artifact_id", artifact_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        return rows[0] if rows else None

    def calibrator_row(self, model_artifact_version: str) -> Mapping[str, Any] | None:
        rows = (
            self._client.table("wow_basketball_team_event_calibrators")
            .select("*")
            .eq("model_artifact_version", model_artifact_version)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
            .data
            or []
        )
        return rows[0] if rows else None

    def capability_row(self, sport: str) -> Mapping[str, Any] | None:
        rows = (
            self._client.table("wow_basketball_team_event_certification")
            .select("*")
            .eq("sport", sport)
            .limit(1)
            .execute()
            .data
            or []
        )
        return rows[0] if rows else None

    def attestation(self) -> Mapping[str, Any]:
        return dict(self._attestation)

    def certification_row(self, model_artifact_version: str) -> Mapping[str, Any] | None:
        rows = (
            self._client.table("wow_team_event_specialist_certifications")
            .select("model_version,artifact_sha256,calibration_sha256,certification_status")
            .eq("model_version", model_artifact_version)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
            .data
            or []
        )
        return rows[0] if rows else None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Emit a V17 independent verification receipt for one NBA artifact (read-only)."
    )
    parser.add_argument("--source-json", required=True, help="materialized bundle file")
    parser.add_argument("--artifact-id", required=True)
    parser.add_argument("--controlling-specialist", required=True)
    parser.add_argument("--input-contract-version", required=True)
    parser.add_argument("--independent-verifier", required=True)
    parser.add_argument("--implementation-owner", required=True)
    parser.add_argument("--verification-owner", required=True)
    parser.add_argument("--evidence-ref", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--max-ece", type=float, default=None)
    parser.add_argument("--code-sha", default=None)
    parser.add_argument("--environment", default="UNSPECIFIED_LOCAL")
    parser.add_argument("--output", default="-")
    args = parser.parse_args(argv)

    refs: dict[str, str] = {}
    for entry in args.evidence_ref:
        if "=" not in entry:
            parser.error(f"--evidence-ref expects KEY=VALUE, got {entry!r}")
        key, _, value = entry.partition("=")
        refs[key.strip()] = value.strip()

    with open(args.source_json, "r", encoding="utf-8") as handle:
        bundle = json.load(handle)

    request = VerificationRequest(
        artifact_id=args.artifact_id,
        controlling_specialist=args.controlling_specialist,
        input_contract_version=args.input_contract_version,
        independent_verifier=args.independent_verifier,
        implementation_owner=args.implementation_owner,
        verification_owner=args.verification_owner,
        evidence_refs=refs,
        max_ece=args.max_ece,
    )
    receipt = verify_nba_team_event(
        MappingVerificationSource(bundle),
        request,
        code_sha=args.code_sha,
        environment=args.environment,
        replay_command=" ".join(sys.argv[1:]),
    )
    rendered = json.dumps(receipt, indent=2, sort_keys=True)
    if args.output == "-":
        print(rendered)
    else:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    return 0 if receipt["independent_verification_status"] == "PASS" else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
