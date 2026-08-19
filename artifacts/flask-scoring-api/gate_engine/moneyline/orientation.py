"""
Typed participant-orientation contract for moneyline scoring.

Missing, malformed, or conflicting orientation data is unresolved.  No caller
may infer HOME from an unresolved result.

can_execute=False unconditional.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

can_execute: bool = False


class ParticipantOrientation(str, Enum):
    HOME = "HOME"
    AWAY = "AWAY"
    UNRESOLVED = "UNRESOLVED"


class OrientationFailureReason(str, Enum):
    MISSING = "MISSING_ORIENTATION"
    MALFORMED = "MALFORMED_ORIENTATION"
    AMBIGUOUS = "AMBIGUOUS_ORIENTATION"


@dataclass(frozen=True)
class OrientationResolution:
    orientation: ParticipantOrientation
    reason: OrientationFailureReason | None
    source_fields: tuple[str, ...]
    invalid_values: tuple[str, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.orientation in (
            ParticipantOrientation.HOME,
            ParticipantOrientation.AWAY,
        )

    @property
    def is_home(self) -> bool | None:
        if self.orientation == ParticipantOrientation.HOME:
            return True
        if self.orientation == ParticipantOrientation.AWAY:
            return False
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "orientation": self.orientation.value,
            "resolved": self.resolved,
            "is_home": self.is_home,
            "reason": self.reason.value if self.reason else None,
            "source_fields": list(self.source_fields),
            "invalid_values": list(self.invalid_values),
            "can_execute": False,
        }


class ParticipantOrientationContractError(ValueError):
    """Typed direct-call error for an unresolved moneyline orientation."""

    def __init__(self, resolution: OrientationResolution):
        self.resolution = resolution
        super().__init__(orientation_blocker(resolution))


_HOME_MARKERS = frozenset({"HOME", "TRUE", "1", "YES", "VS", "VS.", "H"})
_AWAY_MARKERS = frozenset({"AWAY", "FALSE", "0", "NO", "@"})
_ORIENTATION_FIELDS = ("home_away", "is_home", "participant_side", "side_marker")


def resolve_participant_orientation(
    row: dict[str, Any],
    enrichment: dict[str, Any] | None = None,
) -> OrientationResolution:
    """
    Resolve HOME/AWAY only from explicit, mutually consistent markers.

    Every supplied orientation field is considered.  An unrecognized value or
    a HOME/AWAY conflict makes the result unresolved rather than allowing field
    order to hide ambiguity.
    """
    observations: list[tuple[str, ParticipantOrientation]] = []
    invalid_values: list[str] = []
    enrichment = enrichment or {}

    for scope, payload in (("row", row), ("enrichment", enrichment)):
        if not isinstance(payload, dict):
            continue
        for field in _ORIENTATION_FIELDS:
            if field not in payload or payload.get(field) is None:
                continue
            raw = payload.get(field)
            normalized = str(raw).strip().upper()
            source = f"{scope}.{field}"
            if normalized in _HOME_MARKERS:
                observations.append((source, ParticipantOrientation.HOME))
            elif normalized in _AWAY_MARKERS:
                observations.append((source, ParticipantOrientation.AWAY))
            else:
                invalid_values.append(f"{source}={raw!r}")

    source_fields = tuple(source for source, _ in observations)

    if invalid_values:
        return OrientationResolution(
            orientation=ParticipantOrientation.UNRESOLVED,
            reason=OrientationFailureReason.MALFORMED,
            source_fields=source_fields,
            invalid_values=tuple(invalid_values),
        )

    if not observations:
        return OrientationResolution(
            orientation=ParticipantOrientation.UNRESOLVED,
            reason=OrientationFailureReason.MISSING,
            source_fields=(),
        )

    orientations = {orientation for _, orientation in observations}
    if len(orientations) != 1:
        return OrientationResolution(
            orientation=ParticipantOrientation.UNRESOLVED,
            reason=OrientationFailureReason.AMBIGUOUS,
            source_fields=source_fields,
        )

    return OrientationResolution(
        orientation=next(iter(orientations)),
        reason=None,
        source_fields=source_fields,
    )


def orientation_blocker(resolution: OrientationResolution) -> str:
    reason = resolution.reason.value if resolution.reason else "UNKNOWN"
    sources = ",".join(resolution.source_fields) or "none"
    invalid = ",".join(resolution.invalid_values) or "none"
    return (
        "PARTICIPANT_ORIENTATION_UNRESOLVED:"
        f"reason={reason}:sources={sources}:invalid={invalid}"
    )