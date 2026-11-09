"""V17 pregame player-availability gate.

Roster membership is not game availability. A player can remain on an active
roster while the club has already ruled him out of a specific game, so a
roster-status check alone will admit an unavailable participant to probability
scoring. This gate reads official availability evidence separately from
identity and lets the pipeline terminate a ruled-out participant before any
probability is computed.

Contract properties:

- no probability, identity, line, or calibration value is computed or changed;
- ``UNVERIFIED`` is reported as such and is never silently treated as
  available, and an unrecognized designation never counts as clearance;
- a ruled-out participant produces the typed blocker ``PLAYER_RULED_OUT``;
- ``can_execute`` remains false.

The gate is deliberately evidence-shape tolerant. Automatic hydration supplies
``role_status["availability"]``; legacy provider payloads may only carry a
flat designation key, and those are read as designation evidence too.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

CAN_EXECUTE = False

AVAILABILITY_AVAILABLE = "AVAILABLE"
AVAILABILITY_UNAVAILABLE = "UNAVAILABLE"
AVAILABILITY_UNVERIFIED = "UNVERIFIED"

PLAYER_RULED_OUT = "PLAYER_RULED_OUT"
PLAYER_AVAILABILITY_UNVERIFIED = "PLAYER_AVAILABILITY_UNVERIFIED"
# Provisional official participation (an MLB probable starter) is neither
# unverified nor confirmed: it needs its final pregame recheck before the row
# may enter the Verified Pool.
PLAYER_AVAILABILITY_RECHECK_REQUIRED = "PLAYER_AVAILABILITY_RECHECK_REQUIRED"

# Official designations that remove a player from game participation. Doubtful
# and Questionable do not appear here: they are playable designations that must
# be reported as a risk flag, not converted into an availability block.
UNAVAILABLE_DESIGNATIONS = frozenset(
    {
        "OUT",
        "IR",
        "INJ",
        "INJURED_RESERVE",
        "RESERVE_INJURED",
        "PUP",
        "PHYSICALLY_UNABLE_TO_PERFORM",
        "NFI",
        "NON_FOOTBALL_INJURY",
        "RESERVE_NON_FOOTBALL_INJURY",
        "SUSPENDED",
        "SUSPENSION",
        "INACTIVE",
        "COMMISSIONER_EXEMPT",
        "EXEMPT",
        "NOT_WITH_TEAM",
        "DID_NOT_PARTICIPATE",
        "DNP",
    }
)

RISK_DESIGNATIONS = frozenset({"DOUBTFUL", "QUESTIONABLE", "GAME_TIME_DECISION"})

PLAYABLE_DESIGNATIONS = frozenset(
    {
        "ACTIVE",
        "AVAILABLE",
        "AVAILABLE_NO_DESIGNATION",
        "PROBABLE",
        "FULL_PARTICIPATION",
    }
) | RISK_DESIGNATIONS

# Participation basis is a different dimension from an injury designation. An
# official probable-starter listing or a posted batting order says the club has
# named the player for this game; it does not claim the player is injury-free.
PLAYABLE_BASES = frozenset(
    {
        "PROBABLE_STARTER",
        "CONFIRMED_STARTER",
        "POSTED_BATTING_ORDER",
        "FRESH_OFFICIAL_REPORT_UNLISTED",
        "FRESH_OFFICIAL_REPORT_CLEARED",
    }
)

# Bases that are official but provisional: they require the final pregame
# refresh to recheck the same official source before the row is trusted.
PROVISIONAL_BASES = frozenset({"PROBABLE_STARTER"})

# Flat keys older provider payloads use for the same fact. ``status`` is
# deliberately absent: every hydration provider uses ``status`` for roster
# identity, and reading it here would recreate the original defect.
LEGACY_DESIGNATION_KEYS = (
    "availability_designation",
    "availability_status",
    "injury_designation",
    "injury_status",
    "game_designation",
    "game_status",
    "participation_status",
)

RETRIEVED = "RETRIEVED"
RETRIEVAL_FAILED = "RETRIEVAL_FAILED"


@dataclass(frozen=True)
class AvailabilityDecision:
    status: str
    designation: str | None
    source: str | None
    captured_at: str | None
    evidence_status: str
    risk: bool
    blocker: str | None
    reason: str
    basis: str | None = None
    confirmation: str | None = None
    fresh: bool | None = None
    report_complete: bool | None = None
    provenance: Mapping[str, Any] | None = None
    requires_revalidation: bool = False
    can_execute: bool = CAN_EXECUTE

    @property
    def available(self) -> bool | None:
        """True / False / None for available / ruled out / unverified."""
        if self.status == AVAILABILITY_AVAILABLE:
            return True
        if self.status == AVAILABILITY_UNAVAILABLE:
            return False
        return None

    def receipt(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "designation": self.designation,
            "source": self.source,
            "captured_at": self.captured_at,
            "evidence_status": self.evidence_status,
            "risk_designation": self.risk,
            "blocker": self.blocker,
            "reason": self.reason,
            "basis": self.basis,
            "confirmation": self.confirmation,
            "fresh": self.fresh,
            "report_complete": self.report_complete,
            "requires_revalidation": self.requires_revalidation,
            "provenance": dict(self.provenance or {}),
            "can_execute": CAN_EXECUTE,
        }


def normalize_designation(value: Any) -> str:
    """Normalize one designation token to the comparison vocabulary."""
    if value is None:
        return ""
    if isinstance(value, Mapping):
        for key in ("designation", "abbreviation", "name", "description", "status", "type"):
            nested = value.get(key)
            if nested is None:
                continue
            token = normalize_designation(nested)
            if token:
                return token
        return ""
    text = str(value).strip().upper()
    if not text:
        return ""
    for char in (" ", "-", "/", "."):
        text = text.replace(char, "_")
    while "__" in text:
        text = text.replace("__", "_")
    return text.strip("_")


def _flat_designation(role: Mapping[str, Any]) -> tuple[str, str | None]:
    for key in LEGACY_DESIGNATION_KEYS:
        if key in role:
            token = normalize_designation(role.get(key))
            if token:
                return token, key
    return "", None


def _decision(
    *,
    status: str,
    designation: str | None,
    source: str | None,
    captured_at: str | None,
    evidence_status: str,
    risk: bool,
    blocker: str | None,
    reason: str,
    basis: str | None = None,
    confirmation: str | None = None,
    fresh: bool | None = None,
    report_complete: bool | None = None,
    provenance: Mapping[str, Any] | None = None,
    requires_revalidation: bool = False,
) -> AvailabilityDecision:
    return AvailabilityDecision(
        status=status,
        designation=designation,
        source=source,
        captured_at=captured_at,
        evidence_status=evidence_status,
        risk=risk,
        blocker=blocker,
        reason=reason,
        basis=basis,
        confirmation=confirmation,
        fresh=fresh,
        report_complete=report_complete,
        provenance=provenance,
        requires_revalidation=requires_revalidation,
    )


def participation_evidence(
    *,
    designation: Any = None,
    basis: Any = None,
    source: str,
    captured_at: Any,
    evidence_status: str = RETRIEVED,
    confirmation: Any = None,
    fresh: bool | None = None,
    report_complete: bool | None = None,
    athlete_id: Any = None,
    event_id: Any = None,
    team: Any = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build the canonical participation-evidence block a provider emits.

    Providers differ in vocabulary (NFL injury reports, MLB probable pitchers
    and posted batting orders, WNBA injury reports). They must not differ in
    shape, because a shape mismatch reads downstream as unverified availability.
    """
    token = normalize_designation(designation)
    basis_token = normalize_designation(basis)
    out: dict[str, Any] = {
        "status": token or basis_token or AVAILABILITY_UNVERIFIED,
        "basis": basis_token or None,
        "confirmation": normalize_designation(confirmation) or None,
        "source": str(source or "").strip() or None,
        "captured_at": str(captured_at or "").strip() or None,
        "evidence_status": str(evidence_status or RETRIEVED).strip().upper(),
        "fresh": fresh,
        "report_complete": report_complete,
        "athlete_id": str(athlete_id or "").strip() or None,
        "event_id": str(event_id or "").strip() or None,
        "team": str(team or "").strip() or None,
        **extra,
        "can_execute": CAN_EXECUTE,
    }
    if token:
        out["designation"] = token
    return out


def evaluate_player_availability(role_status: Any) -> AvailabilityDecision:
    """Classify official availability from role evidence.

    A recognized unavailable designation is decisive even when the evidence
    block is otherwise malformed: ruling a player out is never less safe than
    a hold.
    """
    role: Mapping[str, Any] = role_status if isinstance(role_status, Mapping) else {}
    availability = role.get("availability")

    if isinstance(availability, Mapping):
        designation = normalize_designation(availability.get("designation"))
        status_token = normalize_designation(availability.get("status"))
        if not designation and status_token in (UNAVAILABLE_DESIGNATIONS | PLAYABLE_DESIGNATIONS):
            # Backward compatibility for providers that put the designation in
            # ``status``. Role tokens such as PROBABLE_STARTER are handled by
            # ``basis`` and never read as injury designations.
            designation = status_token
        basis = normalize_designation(availability.get("basis")) or None
        source = str(availability.get("source") or "").strip() or None
        captured_at = str(availability.get("captured_at") or "").strip() or None
        evidence_status = str(availability.get("evidence_status") or RETRIEVED).strip().upper()
        confirmation = normalize_designation(availability.get("confirmation")) or None
        fresh = availability.get("fresh")
        report_complete = availability.get("report_complete")
        provenance = {
            "athlete_id": str(availability.get("athlete_id") or "").strip() or None,
            "event_id": str(availability.get("event_id") or "").strip() or None,
            "team": str(availability.get("team") or "").strip() or None,
        }
        shared = {
            "source": source,
            "captured_at": captured_at,
            "evidence_status": evidence_status,
            "basis": basis,
            "confirmation": confirmation,
            "fresh": fresh,
            "report_complete": report_complete,
            "provenance": provenance,
        }
        if designation in UNAVAILABLE_DESIGNATIONS:
            return _decision(
                **shared,
                status=AVAILABILITY_UNAVAILABLE,
                designation=designation,
                risk=False,
                blocker=PLAYER_RULED_OUT,
                reason=f"OFFICIAL_DESIGNATION_{designation}",
            )
        if evidence_status != RETRIEVED:
            return _decision(
                **shared,
                status=AVAILABILITY_UNVERIFIED,
                designation=designation or None,
                risk=False,
                blocker=PLAYER_AVAILABILITY_UNVERIFIED,
                reason=f"AVAILABILITY_EVIDENCE_{evidence_status}",
            )
        if fresh is False:
            return _decision(
                **shared,
                status=AVAILABILITY_UNVERIFIED,
                designation=designation or None,
                risk=False,
                blocker=PLAYER_AVAILABILITY_UNVERIFIED,
                reason="AVAILABILITY_EVIDENCE_STALE",
            )
        if report_complete is False:
            return _decision(
                **shared,
                status=AVAILABILITY_UNVERIFIED,
                designation=designation or None,
                risk=False,
                blocker=PLAYER_AVAILABILITY_UNVERIFIED,
                reason="AVAILABILITY_REPORT_INCOMPLETE",
            )
        if designation in PLAYABLE_DESIGNATIONS or basis in PLAYABLE_BASES:
            # Clearance is only as trustworthy as its provenance. A played
            # designation without the official source, a capture time, and the
            # athlete/event/team it was captured for cannot be verified later,
            # so it is not clearance.
            missing = [
                field
                for field, value in (
                    ("source", source),
                    ("captured_at", captured_at),
                    ("athlete_id", provenance["athlete_id"]),
                    ("event_id", provenance["event_id"]),
                    ("team", provenance["team"]),
                )
                if not value
            ]
            if fresh is not True:
                missing.append("fresh")
            if missing:
                return _decision(
                    **{**shared, "provenance": {**provenance, "missing": missing}},
                    status=AVAILABILITY_UNVERIFIED,
                    designation=designation or None,
                    risk=False,
                    blocker=PLAYER_AVAILABILITY_UNVERIFIED,
                    reason="AVAILABILITY_PROVENANCE_INCOMPLETE",
                )
        if designation in PLAYABLE_DESIGNATIONS:
            return _decision(
                **shared,
                status=AVAILABILITY_AVAILABLE,
                designation=designation,
                risk=designation in RISK_DESIGNATIONS,
                blocker=None,
                reason=f"OFFICIAL_DESIGNATION_{designation}",
            )
        if designation:
            return _decision(
                **shared,
                status=AVAILABILITY_UNVERIFIED,
                designation=designation,
                risk=False,
                blocker=PLAYER_AVAILABILITY_UNVERIFIED,
                reason=f"UNRECOGNIZED_DESIGNATION_{designation}",
            )
        if basis in PLAYABLE_BASES:
            return _decision(
                **shared,
                status=AVAILABILITY_AVAILABLE,
                designation=None,
                # Provisional official bases still need the final pregame
                # refresh to recheck the same source, so they carry risk.
                risk=basis in PROVISIONAL_BASES,
                requires_revalidation=basis in PROVISIONAL_BASES,
                blocker=None,
                reason=f"OFFICIAL_BASIS_{basis}",
            )
        return _decision(
            **shared,
            status=AVAILABILITY_UNVERIFIED,
            designation=None,
            risk=False,
            blocker=PLAYER_AVAILABILITY_UNVERIFIED,
            reason="AVAILABILITY_DESIGNATION_MISSING",
        )

    flat_designation, flat_source = _flat_designation(role)
    if flat_designation in UNAVAILABLE_DESIGNATIONS:
        return _decision(
            status=AVAILABILITY_UNAVAILABLE,
            designation=flat_designation,
            source=flat_source,
            captured_at=None,
            evidence_status=RETRIEVED,
            risk=False,
            blocker=PLAYER_RULED_OUT,
            reason=f"OFFICIAL_DESIGNATION_{flat_designation}",
        )

    # Roster identity alone is not participation evidence. ``ACTIVE_CURRENT_
    # ESPN_ROSTER`` and similar roster facts land here and are reported as
    # unverified availability rather than silently cleared.
    return _decision(
        status=AVAILABILITY_UNVERIFIED,
        designation=flat_designation or None,
        source=flat_source,
        captured_at=None,
        evidence_status="NOT_SUPPLIED",
        risk=False,
        blocker=PLAYER_AVAILABILITY_UNVERIFIED,
        reason="AVAILABILITY_EVIDENCE_NOT_SUPPLIED",
    )


__all__ = [
    "AVAILABILITY_AVAILABLE",
    "AVAILABILITY_UNAVAILABLE",
    "AVAILABILITY_UNVERIFIED",
    "AvailabilityDecision",
    "CAN_EXECUTE",
    "LEGACY_DESIGNATION_KEYS",
    "PLAYER_AVAILABILITY_RECHECK_REQUIRED",
    "PLAYER_AVAILABILITY_UNVERIFIED",
    "PLAYER_RULED_OUT",
    "PLAYABLE_BASES",
    "PLAYABLE_DESIGNATIONS",
    "PROVISIONAL_BASES",
    "RISK_DESIGNATIONS",
    "UNAVAILABLE_DESIGNATIONS",
    "evaluate_player_availability",
    "normalize_designation",
    "participation_evidence",
]
