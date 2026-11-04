"""Read-only V17 team/event certification intake and catalog integrity.

A repository JSON file is review evidence, never a trusted certification
decision. Only a service-role registry read through resolve_certification can
move a staged file to READY_FOR_GOVERNANCE_PROMOTION. This module never
modifies the governed catalog, registers a scorer, or publishes probability.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from v17.fitted_team_event_certification import (
    FittedTeamEventCertification,
    TeamEventCertificationError,
    parse_certification,
    resolve_certification,
)
from v17.team_event_capability_manifest import (
    CERTIFIED_TEAM_EVENT_SPORTS,
    DECLARED_TEAM_EVENT_SPECIALIST_IDENTITIES,
    EXPECTED_TEAM_EVENT_SPORTS,
    MLB_GAME_WIN_PROBABILITY_EXPERT,
    normalize_team_event_sport,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
RECEIPT_DIRECTORY = Path(__file__).resolve().parent / "certification" / "team_event"
MAX_RECEIPT_BYTES = 262144


class CertificationIntakeError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key, value in pairs:
        if key in data:
            raise CertificationIntakeError("TEAM_EVENT_RECEIPT_DUPLICATE_JSON_KEY", key)
        data[key] = value
    return data


def _declared_specialist_error(cert: FittedTeamEventCertification) -> str | None:
    declared = DECLARED_TEAM_EVENT_SPECIALIST_IDENTITIES.get(cert.sport)
    if declared and declared != cert.controlling_specialist:
        return "TEAM_EVENT_RECEIPT_SPECIALIST_MISMATCH"
    return None


def load_receipts(directory: str | Path | None = None) -> dict[str, FittedTeamEventCertification]:
    """Validate *every* committed candidate file; never skip a bad one.

    Successful parsing establishes only syntax and internal consistency. The
    strings PASS/CERTIFIED and SHA-256 fields are assertions, not proof of an
    authorized verifier or of the artifact bytes referenced by those hashes.
    """
    root = Path(directory) if directory is not None else RECEIPT_DIRECTORY
    if root.is_symlink():
        raise CertificationIntakeError("TEAM_EVENT_RECEIPT_DIRECTORY_INVALID", str(root))
    if not root.exists():
        return {}
    if not root.is_dir():
        raise CertificationIntakeError("TEAM_EVENT_RECEIPT_DIRECTORY_INVALID", str(root))
    found: dict[str, FittedTeamEventCertification] = {}
    seen_ids: set[str] = set()
    for path in sorted(root.iterdir()):
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            raise CertificationIntakeError("TEAM_EVENT_RECEIPT_PATH_INVALID", path.name)
        try:
            if path.stat().st_size > MAX_RECEIPT_BYTES:
                raise CertificationIntakeError("TEAM_EVENT_RECEIPT_TOO_LARGE", path.name)
            payload = json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=_reject_duplicate_json_keys,
            )
        except CertificationIntakeError:
            raise
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise CertificationIntakeError("TEAM_EVENT_RECEIPT_UNREADABLE", path.name) from exc
        try:
            cert = parse_certification(payload)
        except TeamEventCertificationError as exc:
            raise CertificationIntakeError(exc.code, path.name) from exc
        mismatch = _declared_specialist_error(cert)
        if mismatch:
            raise CertificationIntakeError(mismatch, path.name)
        if cert.sport in found or cert.certification_id in seen_ids:
            raise CertificationIntakeError("TEAM_EVENT_RECEIPT_DUPLICATE", path.name)
        if datetime.fromisoformat(cert.certified_at) > datetime.now(timezone.utc):
            raise CertificationIntakeError("TEAM_EVENT_RECEIPT_FUTURE_CERTIFICATION", path.name)
        found[cert.sport] = cert
        seen_ids.add(cert.certification_id)
    return found


def _registry_proof(
    receipt: FittedTeamEventCertification,
    registry_client: Any | None,
) -> str | None:
    """Missing trusted proof always blocks promotion.

    The client must be the server's authorized service-role client; callers may
    not provide request/Scout/market JSON as registry authority. This check
    compares the complete immutable certification identity, not just labels.
    Independent artifact-byte and replay verification remain upstream duties
    of the trusted certification service and QA.
    """
    if registry_client is None:
        return "TEAM_EVENT_TRUSTED_REGISTRY_PROOF_REQUIRED"
    try:
        trusted = resolve_certification(
            registry_client, sport=receipt.sport, league_scope=receipt.league_scope
        )
    except TeamEventCertificationError as exc:
        return exc.code
    except Exception:
        return "TEAM_EVENT_TRUSTED_REGISTRY_UNAVAILABLE"
    if trusted != receipt:
        return "TEAM_EVENT_RECEIPT_REGISTRY_MISMATCH"
    return None


def _result(sport: str, status: str, blockers: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "sport": sport,
        "status": status,
        "blockers": list(dict.fromkeys(blockers)),
        "probability_publishable": False,
        "rank_eligible": False,
        "catalog_mutated": False,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def intake(
    sport: str,
    *,
    directory: str | Path | None = None,
    registry_client: Any | None = None,
) -> dict[str, Any]:
    """Inspect one sport; never change certification or terminal authority."""
    normalized = normalize_team_event_sport(sport)
    if normalized not in EXPECTED_TEAM_EVENT_SPORTS:
        return _result(normalized, "BLOCKED", ["TEAM_EVENT_CERTIFICATION_SPORT_UNSUPPORTED"])
    try:
        receipts = load_receipts(directory)
    except CertificationIntakeError as exc:
        return _result(normalized, "BLOCKED", [exc.code])
    listed = CERTIFIED_TEAM_EVENT_SPORTS.get(normalized)
    receipt = receipts.get(normalized)
    if normalized == "MLB" and listed == MLB_GAME_WIN_PROBABILITY_EXPERT and not receipt:
        return _result(normalized, "CERTIFIED_LEGACY")
    if receipt is None:
        return _result(normalized, "BLOCKED", [
            "TEAM_EVENT_LEDGER_ENTRY_WITHOUT_RECEIPT"
            if listed else "TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED"
        ])
    if listed and listed != receipt.controlling_specialist:
        return _result(normalized, "BLOCKED", ["TEAM_EVENT_LEDGER_SPECIALIST_MISMATCH"])
    proof_error = _registry_proof(receipt, registry_client)
    if proof_error:
        return _result(normalized, "BLOCKED", [proof_error])
    return _result(
        normalized,
        "CERTIFIED_CATALOG_BACKED" if listed else "READY_FOR_GOVERNANCE_PROMOTION",
    )


def catalog_integrity(
    *, directory: str | Path | None = None, registry_client: Any | None = None
) -> dict[str, Any]:
    """Fail for invalid receipts or unauthorized static catalog entries.

    Only MLB's frozen historical ledger entry is grandfathered. A new catalog
    entry requires a matching staged receipt AND trusted registry proof.
    """
    blockers: list[str] = []
    try:
        receipts = load_receipts(directory)
    except CertificationIntakeError as exc:
        return {
            "status": "FAIL", "blockers": [exc.code],
            "receipt_count": None, "can_execute": False,
        }
    if CERTIFIED_TEAM_EVENT_SPORTS.get("MLB") != MLB_GAME_WIN_PROBABILITY_EXPERT:
        blockers.append("TEAM_EVENT_LEGACY_MLB_LEDGER_CHANGED")
    for sport, specialist in CERTIFIED_TEAM_EVENT_SPORTS.items():
        if sport == "MLB" and sport not in receipts:
            continue
        if sport not in EXPECTED_TEAM_EVENT_SPORTS:
            blockers.append("TEAM_EVENT_CATALOG_UNKNOWN_SPORT:" + sport)
            continue
        receipt = receipts.get(sport)
        if receipt is None:
            blockers.append("TEAM_EVENT_LEDGER_ENTRY_WITHOUT_RECEIPT:" + sport)
            continue
        if specialist != receipt.controlling_specialist:
            blockers.append("TEAM_EVENT_LEDGER_SPECIALIST_MISMATCH:" + sport)
        proof = _registry_proof(receipt, registry_client)
        if proof:
            blockers.append(proof + ":" + sport)
    return {
        "status": "FAIL" if blockers else "PASS",
        "blockers": blockers,
        "receipt_count": len(receipts),
        "can_execute": False,
    }


def pending_promotions(
    *, directory: str | Path | None = None, registry_client: Any | None = None
) -> list[str]:
    """List staged sports independently proved by the trusted registry.

    A bare JSON receipt must not appear as governance-ready.
    """
    receipts = load_receipts(directory)
    return sorted(
        sport for sport, receipt in receipts.items()
        if sport not in CERTIFIED_TEAM_EVENT_SPORTS
        and _registry_proof(receipt, registry_client) is None
    )


__all__ = [
    "CAN_EXECUTE", "PROBABILITY_PUBLISHABLE", "TERMINAL_AUTHORITY",
    "CertificationIntakeError", "load_receipts", "intake",
    "catalog_integrity", "pending_promotions",
]
