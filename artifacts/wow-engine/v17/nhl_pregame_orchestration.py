"""Server-owned NHL pregame orchestration: collect, attest, persist, then gate.

This is the single entry point that turns two independent official
starting-goalie receipts into a pregame evidence-gate result. It:

1. requires two distinct adapters whose source kind can confirm a starter (a
   Gamecenter participation feed can never be registered as one);
2. collects each receipt through the caller-supplied server-owned adapter;
3. signs every collected receipt under the server-held attestation key;
4. persists every collected receipt immutably *before* the gate runs;
5. confirms both starters with ``v17.nhl_starter_goalie_confirmation``, which
   derives authority and independence from the server-owned trusted-provider
   registry rather than from receipt claims;
6. overlays the confirmed goalie fields onto the caller's evidence and calls
   ``v17.nhl_pregame_evidence_boundary.validate_nhl_pregame_evidence``.

A caller-supplied ``goalie_status='CONFIRMED'`` is always overwritten. Nothing
here scores a probability, certifies an artifact, ranks a pick or authorizes
execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Callable, Mapping, Sequence

from v17.nhl_candidate_preflight import NHLCandidatePreflightError
from v17.nhl_pregame_evidence_boundary import validate_nhl_pregame_evidence
from v17.nhl_starter_goalie_confirmation import (
    ATTESTATION_FIELD,
    REQUIRED_RECEIPTS,
    TrustedProvider,
    attest_receipt,
    confirm_starting_goalies,
)

CAN_EXECUTE = False
RECEIPT_TABLE = "wow_nhl_pregame_starter_receipts"
RECEIPT_KEY_PREFIX = "nhl-goalie-receipt:"

# Source kinds that can confirm a starter. Participation and projection kinds
# are evidence about a roster, never proof of the starter.
CONFIRMING_SOURCE_KINDS = frozenset(
    {
        "STARTING_GOALIE_DESIGNATION",
        "STARTING_LINEUP",
        "OFFICIAL_ROSTER_DESIGNATION",
    }
)
NON_CONFIRMING_SOURCE_KINDS = frozenset(
    {
        "GAMECENTER_BOXSCORE_PARTICIPATION",
        "PROJECTED_STARTER",
        "LAST_START_INFERENCE",
    }
)


class NHLPregameOrchestrationError(ValueError):
    """Programmer/configuration error, never a missing-evidence verdict."""


@dataclass(frozen=True)
class StarterReceiptAdapter:
    """One server-owned official starting-goalie source.

    ``collect(identity, as_of_utc)`` returns a raw receipt mapping with at least
    ``source_snapshot_id``, ``source_payload_sha256``, ``as_of``,
    ``official_event_id``, ``event_start_time_utc``, ``home_team_id``,
    ``away_team_id``, ``home`` and ``away`` (each ``goalie_id`` and
    ``starting_status``).

    ``provider`` and ``provider_family`` are stamped onto the receipt here, but
    they only become authority when the confirmation step finds them in the
    server-owned ``trusted_providers`` registry. Registration therefore cannot
    grant authority; it can only exclude source kinds that never confirm.
    """

    provider: str
    provider_family: str
    source_kind: str
    collect: Callable[[Mapping[str, Any], datetime], Mapping[str, Any]]

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise NHLPregameOrchestrationError("NHL_PREGAME_ADAPTER_PROVIDER_MISSING")
        if not isinstance(self.provider_family, str) or not self.provider_family.strip():
            raise NHLPregameOrchestrationError("NHL_PREGAME_ADAPTER_PROVIDER_MISSING")
        if self.source_kind in NON_CONFIRMING_SOURCE_KINDS:
            raise NHLPregameOrchestrationError(
                "NHL_PREGAME_ADAPTER_NOT_A_STARTER_SOURCE:"
                f"{self.source_kind}"
            )
        if self.source_kind not in CONFIRMING_SOURCE_KINDS:
            raise NHLPregameOrchestrationError(
                f"NHL_PREGAME_ADAPTER_SOURCE_KIND_UNSUPPORTED:{self.source_kind}"
            )
        if not callable(self.collect):
            raise NHLPregameOrchestrationError("NHL_PREGAME_ADAPTER_COLLECT_NOT_CALLABLE")


def receipt_identity_key(receipt: Mapping[str, Any]) -> str:
    """Deterministic immutable key for one collected source receipt."""
    raw = "|".join(
        (
            str(receipt.get("official_event_id") or ""),
            str(receipt.get("source_id") or ""),
            str(receipt.get("source_snapshot_id") or ""),
            str(receipt.get("source_payload_sha256") or "").lower(),
        )
    )
    return RECEIPT_KEY_PREFIX + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _goalie_id(receipt: Mapping[str, Any], side: str) -> str:
    row = receipt.get(side)
    if not isinstance(row, Mapping):
        return ""
    return str(row.get("goalie_id") or "")


def _receipt_row(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "receipt_identity_key": receipt_identity_key(receipt),
        "official_event_id": str(receipt.get("official_event_id") or ""),
        "provider": str(receipt.get("source_id") or ""),
        "source_kind": str(receipt.get("source_kind") or ""),
        "source_snapshot_id": str(receipt.get("source_snapshot_id") or ""),
        "source_payload_sha256": str(receipt.get("source_payload_sha256") or "").lower(),
        "observed_at": receipt.get("as_of"),
        "event_start_time_utc": receipt.get("event_start_time_utc"),
        "home_team": receipt.get("home_team_id"),
        "away_team": receipt.get("away_team_id"),
        "home_goalie_id": _goalie_id(receipt, "home"),
        "away_goalie_id": _goalie_id(receipt, "away"),
        "starting_status": (
            (receipt.get("home") or {}).get("starting_status")
            if isinstance(receipt.get("home"), Mapping)
            else None
        ),
        "collection_status": "COLLECTED",
        "blockers": [],
        "raw_receipt": json.loads(
            json.dumps(dict(receipt), sort_keys=True, separators=(",", ":"), default=str)
        ),
        "prediction_authority": False,
        "can_execute": False,
    }


def persist_starter_receipts(
    db: Any, receipts: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Append collected receipts without overwriting an existing source capture."""
    rows = [_receipt_row(receipt) for receipt in receipts]
    if not rows:
        return {
            "status": "NO_RECEIPTS",
            "persisted": 0,
            "table": RECEIPT_TABLE,
            "receipt_identity_keys": [],
            "can_execute": False,
        }
    db.table(RECEIPT_TABLE).upsert(
        rows, on_conflict="receipt_identity_key", ignore_duplicates=True
    ).execute()
    return {
        "status": "PERSISTED",
        "persisted": len(rows),
        "table": RECEIPT_TABLE,
        "receipt_identity_keys": [row["receipt_identity_key"] for row in rows],
        "can_execute": False,
    }


def _held(
    code: str,
    blockers: Sequence[str],
    *,
    identity: Mapping[str, Any],
    persistence: Mapping[str, Any] | None = None,
    confirmation: Mapping[str, Any] | None = None,
    gate: Mapping[str, Any] | None = None,
    gate_invoked: bool = False,
) -> dict[str, Any]:
    return {
        "status": "HELD",
        "code": code,
        "sport": "NHL",
        "official_event_id": identity.get("event_id"),
        "blockers": sorted({code, *(str(value) for value in blockers if value)}),
        "receipt_persistence": dict(persistence) if persistence else None,
        "goalie_confirmation": dict(confirmation) if confirmation else None,
        "pregame_gate": dict(gate) if gate else None,
        "gate_invoked": gate_invoked,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def orchestrate_nhl_pregame_gate(
    *,
    db: Any,
    identity: Mapping[str, Any],
    evidence: Mapping[str, Any],
    adapters: Sequence[StarterReceiptAdapter],
    as_of_utc: datetime,
    trusted_providers: Mapping[str, TrustedProvider],
    attestation_key: bytes,
    resolved_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect, attest, persist and gate one NHL pregame event."""
    if as_of_utc.tzinfo is None:
        raise NHLPregameOrchestrationError("as_of_utc must be timezone-aware")
    now = as_of_utc.astimezone(timezone.utc)
    usable = [row for row in adapters if isinstance(row, StarterReceiptAdapter)]
    providers = {(row.provider, row.provider_family) for row in usable}
    source_ids = {row.provider for row in usable}
    if (
        len(usable) < REQUIRED_RECEIPTS
        or len(providers) < REQUIRED_RECEIPTS
        or len(source_ids) < REQUIRED_RECEIPTS
    ):
        return _held(
            "NHL_PREGAME_ORCHESTRATION_ADAPTERS_INSUFFICIENT",
            ["NHL_PREGAME_ORCHESTRATION_ADAPTERS_INSUFFICIENT"],
            identity=identity,
        )

    blockers: list[str] = []
    collected: list[dict[str, Any]] = []
    for adapter in usable:
        try:
            raw = adapter.collect(identity, now)
        except Exception as exc:  # noqa: BLE001 - transport failure is a typed hold
            blockers.append(
                f"NHL_PREGAME_STARTER_SOURCE_UNOBTAINABLE:{adapter.provider}"
                f":{getattr(exc, 'code', type(exc).__name__.upper())}"
            )
            continue
        if not isinstance(raw, Mapping):
            blockers.append(f"NHL_PREGAME_STARTER_SOURCE_INVALID:{adapter.provider}")
            continue
        # A receipt may never impersonate a different source or provider family.
        if raw.get("source_id") not in (None, adapter.provider) or raw.get(
            "provider_family"
        ) not in (None, adapter.provider_family):
            blockers.append(f"NHL_PREGAME_RECEIPT_PROVIDER_MISMATCH:{adapter.provider}")
            continue
        receipt = {
            **dict(raw),
            "source_authority": "OFFICIAL",
            "source_id": adapter.provider,
            "provider_family": adapter.provider_family,
            "source_kind": adapter.source_kind,
            "official_event_id": str(
                raw.get("official_event_id") or identity.get("event_id") or ""
            ),
        }
        try:
            receipt[ATTESTATION_FIELD] = attest_receipt(receipt, attestation_key)
        except Exception as exc:  # noqa: BLE001 - an unsigned receipt cannot gate
            blockers.append(
                "NHL_PREGAME_RECEIPT_UNATTESTED:"
                f"{adapter.provider}:{getattr(exc, 'code', type(exc).__name__.upper())}"
            )
            continue
        collected.append(receipt)

    try:
        persistence = persist_starter_receipts(db, collected)
    except Exception as exc:  # noqa: BLE001 - an unpersisted receipt cannot gate
        return _held(
            "NHL_PREGAME_RECEIPT_PERSISTENCE_FAILED",
            [
                "NHL_PREGAME_RECEIPT_PERSISTENCE_FAILED",
                f"NHL_PREGAME_RECEIPT_PERSISTENCE_ERROR:{type(exc).__name__.upper()}",
                *blockers,
            ],
            identity=identity,
        )

    try:
        confirmation = confirm_starting_goalies(
            collected,
            now=now,
            trusted_providers=trusted_providers,
            attestation_key=attestation_key,
        )
        confirmed = True
    except NHLCandidatePreflightError as exc:
        # Authority, independence and agreement are decided by the confirmation
        # contract; its typed code is preserved rather than collapsed.
        confirmation = {
            "status": "HELD",
            "code": str(getattr(exc, "code", "") or "NHL_STARTER_RECEIPTS_INCOMPLETE"),
            "blockers": [
                str(getattr(exc, "code", "") or "NHL_STARTER_RECEIPTS_INCOMPLETE")
            ],
            "can_execute": False,
            "probability_publishable": False,
        }
        confirmed = False

    gated_evidence = dict(evidence)
    if confirmed and confirmation.get("goalie_status") == "CONFIRMED":
        gated_evidence["goalie_status"] = confirmation["goalie_status"]
        gated_evidence["goalie_status_observed_at"] = confirmation["verified_at"]
        gated_evidence["goalie_identity_snapshot_id"] = confirmation[
            "goalie_identity_snapshot_id"
        ]
    else:
        # Never let a caller's own CONFIRMED claim survive a failed confirmation.
        gated_evidence["goalie_status"] = "UNVERIFIED"
        gated_evidence["goalie_status_observed_at"] = None
        gated_evidence["goalie_identity_snapshot_id"] = None

    gate = validate_nhl_pregame_evidence(
        gated_evidence,
        as_of_utc=now,
        resolved_identity=(
            identity if resolved_identity is None else resolved_identity
        ),
        goalie_confirmation=confirmation,
    )
    confirmation_blockers = list(confirmation.get("blockers") or [])
    gate_blockers = list(gate.get("blockers") or [])
    if gate.get("status") == "INPUT_EVIDENCE_PASS_NOT_CERTIFICATION":
        return {
            "status": "PREGAME_EVIDENCE_COMPLETE_NOT_CERTIFICATION",
            "code": "NHL_PREGAME_GATE_INPUTS_COMPLETE",
            "sport": "NHL",
            "official_event_id": identity.get("event_id"),
            "blockers": sorted(set([*blockers, *confirmation_blockers])),
            "receipt_persistence": persistence,
            "goalie_confirmation": confirmation,
            "pregame_gate": gate,
            "gate_invoked": True,
            "fitted_specialist_invoked": False,
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        }
    return _held(
        "NHL_PREGAME_GATE_NOT_PASSED",
        [*blockers, *confirmation_blockers, *gate_blockers],
        identity=identity,
        persistence=persistence,
        confirmation=confirmation,
        gate=gate,
        gate_invoked=True,
    )


__all__ = [
    "CAN_EXECUTE",
    "CONFIRMING_SOURCE_KINDS",
    "NON_CONFIRMING_SOURCE_KINDS",
    "NHLPregameOrchestrationError",
    "RECEIPT_TABLE",
    "StarterReceiptAdapter",
    "orchestrate_nhl_pregame_gate",
    "persist_starter_receipts",
    "receipt_identity_key",
]
