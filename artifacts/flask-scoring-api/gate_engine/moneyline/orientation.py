"""
Typed participant-orientation contract for moneyline scoring.

Missing, malformed, or conflicting orientation data is unresolved.  No caller
may infer HOME from an unresolved result.

Resolution priority (highest to lowest):
  1. Explicit marker fields: home_away, is_home, participant_side, side_marker
     — any recognized value in _HOME_MARKERS / _AWAY_MARKERS on either the
     row or the enrichment dict.
  2. home_team / away_team derivation — when the caller supplies both
     ``home_team`` AND ``team`` (or ``away_team`` AND ``team``) as non-empty
     strings, orientation is derived by exact case-insensitive comparison of
     the candidate ``team`` against those names.  Both names must be present
     for derivation to fire; a partial supply (only home_team, or only
     away_team) is treated as absent.  A derivation conflict (team matches
     neither, or matches both) stays UNRESOLVED.

Any HOME/AWAY conflict between the two layers (explicit markers vs.
home_team/away_team derivation, or within a layer) makes the result
AMBIGUOUS rather than allowing order to hide the conflict.

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


def _derive_from_team_names(
    row: dict[str, Any],
    enrichment: dict[str, Any],
) -> tuple[str, ParticipantOrientation] | None:
    """
    Derive HOME/AWAY from home_team / away_team fields when the caller does
    not supply an explicit marker field.

    Returns a (source_label, orientation) tuple when derivation succeeds, or
    None when the required fields are absent or the match is ambiguous.

    Rules
    -----
    - Both ``home_team`` and ``away_team`` must be non-empty strings (either
      on the row or in enrichment) for derivation to attempt.  A partial
      supply (one name only) produces None — no derivation.
    - ``team`` must be a non-empty string on the row.
    - Comparison is case-insensitive exact match.
    - If ``team`` matches home_team → HOME.
    - If ``team`` matches away_team → AWAY.
    - If ``team`` matches both, or neither → None (ambiguous / no match).
    - The first non-empty value found wins (row checked before enrichment).
    """
    def _get(field: str) -> str:
        for payload in (row, enrichment):
            v = payload.get(field) if isinstance(payload, dict) else None
            if v and isinstance(v, str) and v.strip():
                return v.strip()
        return ""

    team = _get("team")
    home_team = _get("home_team")
    away_team = _get("away_team")

    if not team or not home_team or not away_team:
        return None

    team_norm = team.lower()
    is_home = team_norm == home_team.lower()
    is_away = team_norm == away_team.lower()

    if is_home and not is_away:
        return ("row.home_team", ParticipantOrientation.HOME)
    if is_away and not is_home:
        return ("row.away_team", ParticipantOrientation.AWAY)
    # matches both (identical names) or neither — cannot resolve cleanly
    return None


def resolve_participant_orientation(
    row: dict[str, Any],
    enrichment: dict[str, Any] | None = None,
) -> OrientationResolution:
    """
    Resolve HOME/AWAY only from explicit, mutually consistent markers.

    Resolution priority:
      1. Explicit marker fields (home_away, is_home, participant_side,
         side_marker) on row then enrichment.
      2. home_team / away_team name derivation (see module docstring).

    An unrecognized value or any HOME/AWAY conflict across all sources makes
    the result UNRESOLVED rather than allowing field order to hide ambiguity.
    """
    observations: list[tuple[str, ParticipantOrientation]] = []
    invalid_values: list[str] = []
    enrichment = enrichment or {}

    # Layer 1: explicit marker fields
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

    # Layer 2: home_team / away_team name derivation (only when no explicit
    # marker was found and no invalid values are blocking)
    derived: tuple[str, ParticipantOrientation] | None = None
    if not observations and not invalid_values:
        derived = _derive_from_team_names(row, enrichment)
        if derived is not None:
            observations.append(derived)

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