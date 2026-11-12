"""Fail-closed, read-only V17 in-toto pregame receipt proof (research adoption).

A signature or caller-provided certification label is never model certification.
Only verified trusted RFC3161 time over the exact signed statement establishes
the externally attested pregame cutoff in this reference implementation.
No scorer, terminal reducer, shared card gate, or database is modified.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from typing import Any, Mapping

CAN_EXECUTE = False
AUTOMATIC_CERTIFICATION = False
PROBABILITY_PUBLISHABLE = False
CONTRACT = "WOW_V17_PREGAME_RECEIPT_V1"
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = (
    "https://github.com/gregoryharper84-ship-it/WOW-Dashboard/"
    "blob/main/docs/wow/governance/V17_PREGAME_ATTESTATION_V1.md"
)
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
MAX_BYTES = 1024 * 1024
MAX_QUOTE_AGE = timedelta(minutes=60)
MARKETS = {
    "NFL": {"POINT_SPREAD": "FULL_GAME_INCLUDING_OVERTIME"},
    "NBA": {"POINT_SPREAD": "FULL_GAME_INCLUDING_OVERTIME"},
    "WNBA": {"POINT_SPREAD": "FULL_GAME_INCLUDING_OVERTIME"},
    "NCAAF": {"POINT_SPREAD": "FULL_GAME_INCLUDING_OVERTIME"},
    "NCAAB": {"POINT_SPREAD": "FULL_GAME_INCLUDING_OVERTIME"},
    "NHL": {"PUCK_LINE": "FULL_GAME_INCLUDING_OVERTIME_SHOOTOUT"},
    "MLB": {"RUN_LINE": "FULL_GAME_INCLUDING_EXTRA_INNINGS"},
    "SOCCER": {"ASIAN_HANDICAP": "REGULATION"},
    "TENNIS": {
        "GAME_HANDICAP": "COMPLETED_MATCH_NO_RETIREMENT",
        "SET_HANDICAP": "COMPLETED_MATCH_NO_RETIREMENT",
    },
}
FIELDS = frozenset({
    "contract", "prediction_id", "sport", "official_event_id",
    "home_team", "away_team", "event_start_time_utc", "market_type",
    "settlement_basis", "selected_side", "signed_home_line",
    "quote_source_id", "quote_observed_at_utc", "frozen_at_utc",
    "model_artifact_sha256", "feature_snapshot_sha256",
    "quote_receipt_sha256", "p_cover", "p_push", "p_not_cover",
    "calibrated_lower_bound", "calibrated_upper_bound",
    "model_certification_status", "certification_reference_id",
    "terminal_authority", "probability_publishable", "can_execute",
})
HEX = re.compile(r"^[0-9a-f]{64}$")
DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
TIMESTAMP_LINE = re.compile(r"^Time stamp:\s*(.+)$", re.MULTILINE)


class ReceiptHold(ValueError):
    """Typed proof rejection; never interpreted as a prediction outcome."""


def _reject(code: str) -> None:
    raise ReceiptHold(code)


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _reject("RECEIPT_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def canonical_bytes(obj: Any) -> bytes:
    """Versioned Python canonical encoding; not advertised as RFC 8785."""
    try:
        return (json.dumps(obj, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    except (ValueError, TypeError, OverflowError):
        _reject("RECEIPT_JSON_NONCANONICAL")


def _load_json(raw: bytes) -> Any:
    if not raw or len(raw) > MAX_BYTES:
        _reject("RECEIPT_SIZE_INVALID")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_object_pairs,
                          parse_constant=lambda _x: _reject("RECEIPT_NONFINITE_JSON"))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        _reject("RECEIPT_JSON_INVALID")


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _utc(value: Any) -> datetime:
    if not _text(value) or not value.endswith("Z"):
        _reject("RECEIPT_TIME_NOT_UTC")
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except (ValueError, OverflowError):
        _reject("RECEIPT_TIME_INVALID")
    if result.utcoffset() != timedelta(0):
        _reject("RECEIPT_TIME_NOT_UTC")
    return result.astimezone(timezone.utc)


def _decimal(value: Any, *, bound: bool = False) -> Decimal:
    if not isinstance(value, str) or not DECIMAL.fullmatch(value):
        _reject("RECEIPT_DECIMAL_ENCODING_INVALID")
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        _reject("RECEIPT_DECIMAL_INVALID")
    if not parsed.is_finite() or abs(parsed) > (Decimal(1) if bound else Decimal(200)):
        _reject("RECEIPT_DECIMAL_RANGE_INVALID")
    if bound and parsed < 0:
        _reject("RECEIPT_PROBABILITY_NEGATIVE")
    return parsed


def validate_prediction(obj: Any) -> dict[str, Any]:
    if not isinstance(obj, dict) or set(obj) != FIELDS:
        _reject("RECEIPT_FIELDS_MISMATCH")
    if obj["contract"] != CONTRACT or obj["terminal_authority"] != TERMINAL_AUTHORITY:
        _reject("RECEIPT_GOVERNANCE_IDENTITY_INVALID")
    if obj["can_execute"] is not False:
        _reject("RECEIPT_EXECUTION_INVARIANT_VIOLATION")
    if type(obj["probability_publishable"]) is not bool:
        _reject("RECEIPT_PUBLICATION_CLAIM_INVALID")
    for field in ("prediction_id", "official_event_id", "home_team", "away_team",
                  "quote_source_id"):
        if not _text(obj[field]) or len(obj[field]) > 256:
            _reject("RECEIPT_IDENTITY_INVALID")
    if obj["home_team"] == obj["away_team"] or not isinstance(obj["selected_side"], str) or obj["selected_side"] not in {"HOME", "AWAY"}:
        _reject("RECEIPT_SIDE_OR_TEAMS_INVALID")
    sport = obj["sport"]
    market = obj["market_type"]
    if not isinstance(sport, str) or not isinstance(market, str) or sport not in MARKETS or MARKETS[sport].get(market) != obj["settlement_basis"]:
        _reject("RECEIPT_MARKET_SETTLEMENT_MISMATCH")
    _decimal(obj["signed_home_line"])
    for field in ("model_artifact_sha256", "feature_snapshot_sha256", "quote_receipt_sha256"):
        if not isinstance(obj[field], str) or not HEX.fullmatch(obj[field]):
            _reject("RECEIPT_REFERENCE_DIGEST_INVALID")
    quote = _utc(obj["quote_observed_at_utc"])
    frozen = _utc(obj["frozen_at_utc"])
    kickoff = _utc(obj["event_start_time_utc"])
    if not (quote <= frozen < kickoff and frozen - quote <= MAX_QUOTE_AGE):
        _reject("RECEIPT_PREGAME_ORDER_OR_FRESHNESS_INVALID")
    group = [obj[field] for field in ("p_cover", "p_push", "p_not_cover")]
    bounds = [obj[field] for field in ("calibrated_lower_bound", "calibrated_upper_bound")]
    all_null = all(value is None for value in group + bounds)
    if not all_null:
        if any(value is None for value in group + bounds):
            _reject("RECEIPT_PROBABILITY_PARTIAL")
        p = [_decimal(value, bound=True) for value in group]
        lo, hi = (_decimal(value, bound=True) for value in bounds)
        if abs(sum(p) - Decimal("1")) > Decimal("0.000000001") or not lo <= p[0] <= hi:
            _reject("RECEIPT_CALIBRATION_DISTRIBUTION_INVALID")
        if not (Decimal(0) < lo <= hi < Decimal(1)):
            _reject("RECEIPT_BOUNDS_NOT_INTERIOR")
    state = obj["model_certification_status"]
    ref = obj["certification_reference_id"]
    if not isinstance(state, str) or state not in {"UNQUALIFIED", "CERTIFIED_FITTED"}:
        _reject("RECEIPT_CERTIFICATION_LABEL_INVALID")
    if ref is not None and (not _text(ref) or len(ref) > 256):
        _reject("RECEIPT_CERTIFICATION_REFERENCE_INVALID")
    if state == "CERTIFIED_FITTED" and (all_null or ref is None):
        _reject("RECEIPT_CERTIFICATION_ASSERTION_INCOMPLETE")
    if obj["probability_publishable"] and (state != "CERTIFIED_FITTED" or all_null):
        _reject("RECEIPT_PUBLICATION_ASSERTION_INCOMPLETE")
    # The above *validates claims*, not their authority. No caller flag confers certification.
    return obj


def build_statement(prediction: Mapping[str, Any]) -> dict[str, Any]:
    payload = validate_prediction(dict(prediction))
    content = canonical_bytes(payload)
    return {
        "_type": STATEMENT_TYPE,
        "subject": [{"name": "frozen-prediction.json",
                     "digest": {"sha256": sha256(content).hexdigest()}}],
        "predicateType": PREDICATE_TYPE,
        "predicate": {"frozenPrediction": payload},
    }


def validate_statement(prediction_bytes: bytes, statement_bytes: bytes) -> dict[str, Any]:
    prediction = validate_prediction(_load_json(prediction_bytes))
    if canonical_bytes(prediction) != prediction_bytes:
        _reject("RECEIPT_FROZEN_BYTES_NONCANONICAL")
    statement = _load_json(statement_bytes)
    if not isinstance(statement, dict) or canonical_bytes(statement) != statement_bytes:
        _reject("RECEIPT_STATEMENT_BYTES_NONCANONICAL")
    if statement != build_statement(prediction):
        _reject("RECEIPT_STATEMENT_OR_DIGEST_MISMATCH")
    return prediction


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, text=True, capture_output=True,
                              check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        _reject("RECEIPT_CRYPTO_TOOL_UNAVAILABLE")


def _tsa_time(token: str, roots: str, statement: str, intermediates: str | None) -> datetime:
    # An RFC3161 response must authenticate the exact signed statement bytes.
    command = ["openssl", "ts", "-verify", "-data", statement, "-in", token,
               "-CAfile", roots]
    if intermediates:
        command += ["-untrusted", intermediates]
    if _run(command).returncode != 0:
        _reject("RECEIPT_TSA_SIGNATURE_OR_IMPRINT_INVALID")
    result = _run(["openssl", "ts", "-reply", "-in", token, "-text"])
    if result.returncode != 0:
        _reject("RECEIPT_TSA_TIME_UNREADABLE")
    matches = TIMESTAMP_LINE.findall(result.stdout)
    if len(matches) != 1:
        _reject("RECEIPT_TSA_TIME_UNREADABLE")
    value = matches[0].strip()
    for fmt in ("%b %d %H:%M:%S.%f %Y GMT", "%b %d %H:%M:%S %Y GMT"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    _reject("RECEIPT_TSA_TIME_UNREADABLE")


def verify(
    *, frozen: str, statement: str, bundle: str, signer: str, issuer: str,
    tsa_response: str | None = None, tsa_roots: str | None = None,
    tsa_intermediates: str | None = None, artifact_paths: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Cryptographic proof only. Never model certification or a terminal admission."""
    result: dict[str, Any] = {
        "status": "HELD", "reason_codes": [], "cryptographic_pregame_verified": False,
        "model_certification_verified": False, "probability_publishable": False,
        "card_admission_eligible": False, "can_execute": False,
    }
    try:
        frozen_bytes = Path(frozen).read_bytes()
        statement_bytes = Path(statement).read_bytes()
        prediction = validate_statement(frozen_bytes, statement_bytes)
        if not _text(signer) or not _text(issuer):
            _reject("RECEIPT_SIGNER_POLICY_NOT_PINNED")
        for field, path in (artifact_paths or {}).items():
            if field not in {"model_artifact_sha256", "feature_snapshot_sha256",
                             "quote_receipt_sha256"}:
                _reject("RECEIPT_ARTIFACT_NAME_INVALID")
            if sha256(Path(path).read_bytes()).hexdigest() != prediction[field]:
                _reject("RECEIPT_REFERENCED_ARTIFACT_HASH_MISMATCH")
        # Both independent verifiers consume the same held, already-validated
        # byte snapshot; a mutable source path cannot be swapped between calls.
        with TemporaryDirectory(prefix="wow-v17-proof-") as directory:
            frozen_statement = Path(directory) / "statement.intoto.json"
            frozen_statement.write_bytes(statement_bytes)
            cmd = ["cosign", "verify-blob", "--bundle", bundle,
                   "--certificate-identity", signer, "--certificate-oidc-issuer", issuer,
                   str(frozen_statement)]
            if _run(cmd).returncode != 0:
                _reject("RECEIPT_COSIGN_IDENTITY_OR_SIGNATURE_INVALID")
            if not tsa_response or not tsa_roots:
                _reject("RECEIPT_INDEPENDENT_PREGAME_TIME_PROOF_MISSING")
            issued = _tsa_time(tsa_response, tsa_roots, str(frozen_statement),
                               tsa_intermediates)
        frozen_at = _utc(prediction["frozen_at_utc"])
        kickoff = _utc(prediction["event_start_time_utc"])
        if not frozen_at <= issued < kickoff:
            _reject("RECEIPT_TRUSTED_TIME_NOT_PREGAME")
        result["status"] = "PREGAME_INTEGRITY_VERIFIED"
        result["cryptographic_pregame_verified"] = True
        result["trusted_timestamp_utc"] = issued.isoformat().replace("+00:00", "Z")
        result["prediction_id"] = prediction["prediction_id"]
        result["claimed_model_certification"] = prediction["model_certification_status"]
        result["referenced_artifacts_checked"] = sorted((artifact_paths or {}).keys())
    except (ReceiptHold, OSError, ValueError) as exc:
        result["reason_codes"].append(
            str(exc) if isinstance(exc, ReceiptHold) else "RECEIPT_FILE_READ_FAILED"
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="WOW V17 non-publishing pregame proof")
    commands = parser.add_subparsers(dest="action", required=True)
    create = commands.add_parser("build")
    create.add_argument("--prediction", required=True)
    create.add_argument("--frozen-out", required=True)
    create.add_argument("--statement-out", required=True)
    check = commands.add_parser("verify")
    for name in ("frozen", "statement", "bundle", "signer", "issuer"):
        check.add_argument("--" + name, required=True)
    for name in ("tsa-response", "tsa-roots", "tsa-intermediates",
                 "model-artifact", "feature-snapshot", "quote-receipt"):
        check.add_argument("--" + name)
    args = parser.parse_args()
    if args.action == "build":
        try:
            prediction = validate_prediction(_load_json(Path(args.prediction).read_bytes()))
            frozen = canonical_bytes(prediction)
            statement = canonical_bytes(build_statement(prediction))
            # Fail closed rather than overwrite a previously frozen receipt.
            with Path(args.frozen_out).open("xb") as f:
                f.write(frozen)
            with Path(args.statement_out).open("xb") as f:
                f.write(statement)
            output = {"status": "BUILT_NOT_SIGNED", "prediction_id": prediction["prediction_id"],
                      "statement_sha256": sha256(statement).hexdigest(),
                      "can_execute": False, "probability_publishable": False}
            success = True
        except (ReceiptHold, OSError, ValueError) as exc:
            output = {"status": "HELD", "reason_codes": [str(exc)], "can_execute": False}
            success = False
    else:
        artifact_paths = {
            target: getattr(args, option) for target, option in (
                ("model_artifact_sha256", "model_artifact"),
                ("feature_snapshot_sha256", "feature_snapshot"),
                ("quote_receipt_sha256", "quote_receipt"))
            if getattr(args, option)
        }
        output = verify(
            frozen=args.frozen, statement=args.statement, bundle=args.bundle,
            signer=args.signer, issuer=args.issuer, tsa_response=args.tsa_response,
            tsa_roots=args.tsa_roots, tsa_intermediates=args.tsa_intermediates,
            artifact_paths=artifact_paths,
        )
        success = output["cryptographic_pregame_verified"]
    print(json.dumps(output, sort_keys=True))
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
