"""
gate_engine/universal_agent/evidence_packet.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B0

Shared Immutable Evidence Packet — lane-agnostic frozen snapshot format
capturing all inputs needed for any future universal agent lane.

Design decisions (from Weather shadow pilot lessons):
- Frozen dataclass: immutable once constructed; no mutation possible.
- Lane is an extensible string constant set, NOT a closed Python enum.
  New lanes are added by adding a Lane class constant — no code changes elsewhere.
- build_test_packet() shares the same EvidencePacket type as production use,
  so test harnesses exercise the real validation path (Weather Step 14D lesson:
  mock path must share real validation, not a parallel hand-rolled check).
- All dict/list fields default to empty collections, never None.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional


# ── Lane constants ────────────────────────────────────────────────────────────
# Open string constant set — not a closed enum.
# Add new lanes by adding a class attribute; no schema or routing changes needed.

class Lane:
    """
    Known lane identifiers. Arbitrary strings are also accepted in EvidencePacket.lane —
    these constants are documentation + tab-completion helpers only.
    """
    KALSHI_WEATHER = "KALSHI_WEATHER"
    MLB_MONEYLINE  = "MLB_MONEYLINE"
    WNBA_PROPS     = "WNBA_PROPS"
    MLB_PROPS      = "MLB_PROPS"
    TENNIS         = "TENNIS"
    PLAYER_PROPS   = "PLAYER_PROPS"
    UNKNOWN        = "UNKNOWN"

    @classmethod
    def known(cls) -> frozenset[str]:
        """Return the set of currently defined lane constants."""
        return frozenset(
            v for k, v in vars(cls).items()
            if not k.startswith("_") and isinstance(v, str) and k not in ("known",)
        )


# ── Immutable evidence packet ─────────────────────────────────────────────────

@dataclass(frozen=True)
class EvidencePacket:
    """
    Immutable, lane-agnostic evidence snapshot passed to universal agents.

    Once constructed via build_evidence_packet() or build_test_packet(),
    this object cannot be mutated. All fields are set at construction time.

    Field groups:
      Identity          run_id, canonical_event_id, snapshot_id, lane
      Event/team/player event_name, event_date, player_*, team_*, opponent_*
      Source provenance source_timestamps, source_provenance
      Market snapshot   market_snapshot (lane-specific, opaque dict)
      Status evidence   injury_status_evidence (player_id → status record)
      Model inputs      deterministic_model_inputs (lane-specific structured inputs)
      Failures/conflicts source_failures, source_conflicts
      Metadata          created_at (ISO-8601 UTC, auto-set by constructor)
    """
    # ── Identity ──────────────────────────────────────────────────────────────
    run_id:             str          # Unique run identifier
    canonical_event_id: str          # Stable event identity across sources
    snapshot_id:        str          # Unique snapshot ID (UUID, auto-generated)
    lane:               str          # Lane.* constant or arbitrary extension string

    # ── Event / player / team identity ───────────────────────────────────────
    event_name:          Optional[str]    # Human-readable event name
    event_date:          Optional[str]    # ISO-8601 date, e.g. "2026-08-09"
    player_id:           Optional[str]
    player_name:         Optional[str]
    team_id:             Optional[str]
    team_name:           Optional[str]
    opponent_team_id:    Optional[str]
    opponent_team_name:  Optional[str]

    # ── Source provenance ─────────────────────────────────────────────────────
    source_timestamps:  dict   # source_key → ISO-8601 timestamp string
    source_provenance:  dict   # source_key → description/URL string

    # ── Market snapshot ───────────────────────────────────────────────────────
    market_snapshot:    dict   # Raw market data; structure is lane-specific

    # ── Injury / player status evidence ──────────────────────────────────────
    injury_status_evidence: dict   # player_id → status record dict

    # ── Deterministic model inputs ────────────────────────────────────────────
    deterministic_model_inputs: dict   # Lane-specific structured inputs

    # ── Source failures and conflicts ─────────────────────────────────────────
    source_failures:    tuple  # ({source, reason, ...}, ...) — immutable sequence
    source_conflicts:   tuple  # ({field, sources, values, ...}, ...) — immutable

    # ── Metadata ──────────────────────────────────────────────────────────────
    created_at:         str    # ISO-8601 UTC timestamp; set by build_evidence_packet()

    def __post_init__(self) -> None:
        for fname in ("run_id", "canonical_event_id", "snapshot_id", "lane"):
            val: Any = object.__getattribute__(self, fname)
            if not isinstance(val, str) or not val.strip():
                raise ValueError(
                    f"EvidencePacket.{fname} must be a non-empty string, got {val!r}"
                )

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation suitable for JSON serialization."""
        return {
            "run_id":             self.run_id,
            "canonical_event_id": self.canonical_event_id,
            "snapshot_id":        self.snapshot_id,
            "lane":               self.lane,
            "event_name":         self.event_name,
            "event_date":         self.event_date,
            "player_id":          self.player_id,
            "player_name":        self.player_name,
            "team_id":            self.team_id,
            "team_name":          self.team_name,
            "opponent_team_id":   self.opponent_team_id,
            "opponent_team_name": self.opponent_team_name,
            "source_timestamps":  self.source_timestamps,
            "source_provenance":  self.source_provenance,
            "market_snapshot":    self.market_snapshot,
            "injury_status_evidence":     self.injury_status_evidence,
            "deterministic_model_inputs": self.deterministic_model_inputs,
            "source_failures":    list(self.source_failures),
            "source_conflicts":   list(self.source_conflicts),
            "created_at":         self.created_at,
        }


# ── Constructors ──────────────────────────────────────────────────────────────

def build_evidence_packet(
    *,
    run_id: str,
    canonical_event_id: str,
    lane: str,
    snapshot_id: Optional[str] = None,
    event_name: Optional[str] = None,
    event_date: Optional[str] = None,
    player_id: Optional[str] = None,
    player_name: Optional[str] = None,
    team_id: Optional[str] = None,
    team_name: Optional[str] = None,
    opponent_team_id: Optional[str] = None,
    opponent_team_name: Optional[str] = None,
    source_timestamps: Optional[dict[str, str]] = None,
    source_provenance: Optional[dict[str, str]] = None,
    market_snapshot: Optional[dict[str, Any]] = None,
    injury_status_evidence: Optional[dict[str, Any]] = None,
    deterministic_model_inputs: Optional[dict[str, Any]] = None,
    source_failures: Optional[list[dict[str, Any]]] = None,
    source_conflicts: Optional[list[dict[str, Any]]] = None,
) -> EvidencePacket:
    """
    Primary constructor for EvidencePacket.

    Auto-generates snapshot_id (UUID4) and created_at (UTC ISO-8601) if not supplied.
    All dict/list fields default to empty collections — never None in the frozen object.
    Lists are converted to tuples for immutability.
    """
    return EvidencePacket(
        run_id=run_id,
        canonical_event_id=canonical_event_id,
        snapshot_id=snapshot_id or str(uuid.uuid4()),
        lane=lane,
        event_name=event_name,
        event_date=event_date,
        player_id=player_id,
        player_name=player_name,
        team_id=team_id,
        team_name=team_name,
        opponent_team_id=opponent_team_id,
        opponent_team_name=opponent_team_name,
        source_timestamps=dict(source_timestamps or {}),
        source_provenance=dict(source_provenance or {}),
        market_snapshot=dict(market_snapshot or {}),
        injury_status_evidence=dict(injury_status_evidence or {}),
        deterministic_model_inputs=dict(deterministic_model_inputs or {}),
        source_failures=tuple(source_failures or []),
        source_conflicts=tuple(source_conflicts or []),
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def build_test_packet(
    *,
    run_id: str = "test-run-001",
    canonical_event_id: str = "test-event-001",
    lane: str = Lane.PLAYER_PROPS,
    **overrides: Any,
) -> EvidencePacket:
    """
    Test-only constructor with safe, stable defaults.

    Uses the same EvidencePacket type and build_evidence_packet() path as
    production use — NOT a separate hand-rolled structure. This ensures tests
    exercise the real validation code (learned from Weather Step 14D: the
    mock path must share real validation, not a parallel check).

    Do not call from production code paths.
    """
    base: dict[str, Any] = dict(
        run_id=run_id,
        canonical_event_id=canonical_event_id,
        lane=lane,
        event_name="Test Event",
        event_date="2026-08-09",
        player_id="player-001",
        player_name="Test Player",
        team_id="team-001",
        team_name="Test Team",
        opponent_team_id="team-002",
        opponent_team_name="Opponent Team",
        source_timestamps={"primary": "2026-08-09T12:00:00+00:00"},
        source_provenance={"primary": "test-source-url"},
        market_snapshot={"line": 24.5, "over_odds": -115, "under_odds": -105},
        injury_status_evidence={},
        deterministic_model_inputs={"stat_key": "points", "game_window": 10},
        source_failures=[],
        source_conflicts=[],
    )
    base.update(overrides)
    return build_evidence_packet(**base)
