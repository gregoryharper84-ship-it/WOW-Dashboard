"""NHL Skater Shots on Goal -- Phase 1 (PR 1): canonical identity + historical
settled-game skater box-score ingestion.

Scope, strictly: canonical NHL game/player/team identity resolution, SOG stat
normalization (global + source-scoped aliases), immutable player x game SOG
records with provenance/timestamps, dressed-and-0 vs. scratch/DNP
distinction, and leakage-safe pregame snapshot primitives (raw historical
selection only -- no rolling rates, no opponent suppression, no role/PP-unit
features; those are PR 2).

This module creates NO model capability. It does not register a controlling
specialist, a fitted artifact, a capability-manifest entry, or a scorer.
Historical rows produced here are RESEARCH EVIDENCE ONLY: `NHL_PUBLIC_WEB_API`
remains `certification_source_review_required=True` in
`v17/model_source_entitlements.py`; ingesting, testing, and persisting these
rows does not itself certify the source for training.

can_execute=false unconditionally. PROBABILITY_PUBLISHABLE=False.

See NHL_SKATER_SOG_PHASE1_DATA_IDENTITY_DESIGN.md for the full design this
module implements (Sections A, B, C.1-3/9/11, E, F).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
RESEARCH_EVIDENCE_ONLY = True

SOURCE_ID = "NHL_PUBLIC_WEB_API"  # canonical; reconciles historical_source_manifest_v1.json's
                                  # legacy "NHL_PUBLIC_API" label onto the same source already
                                  # used by nhl_candidate_pipeline.py / model_source_entitlements.py.
STAT_TYPE = "SHOTS_ON_GOAL"
FEATURE_SCHEMA_VERSION = "NHL_SOG_IDENTITY_INGESTION_V1"

PARTICIPATION_DRESSED_PLAYED = "DRESSED_PLAYED"
PARTICIPATION_SCRATCHED_DNP = "SCRATCHED_DNP"

VALID_POSITIONS = {"C", "LW", "RW", "D"}

# Global canonical stat aliases. "Shots" is deliberately excluded -- it is
# ambiguous with shot attempts/Corsi and must never be a universal mapping
# (design doc Section A). A platform found to use bare "Shots" for SOG gets a
# source-scoped entry in SOURCE_SCOPED_STAT_ALIASES instead.
GLOBAL_STAT_ALIASES: dict[str, str] = {
    "shots on goal": STAT_TYPE,
    "sog": STAT_TYPE,
    "player shots on goal": STAT_TYPE,
    "shots_on_goal": STAT_TYPE,
}

# Source-scoped aliases: {platform: {normalized_raw_label: canonical_stat_type}}.
# Empty by default -- a platform-specific "Shots" -> SHOTS_ON_GOAL mapping is
# added here only once independently verified for that platform, never
# assumed.
SOURCE_SCOPED_STAT_ALIASES: dict[str, dict[str, str]] = {}


class NHLSogIngestionError(RuntimeError):
    """Typed internal ingestion error. These are module-local diagnostic
    codes for parsing/identity invariants, not V17 canonical failure-taxonomy
    codes -- no canonical code is registered or altered by this module."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_payload(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def _aware(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise NHLSogIngestionError("NHL_SOG_EVENT_TIME_INVALID", str(value))
    return parsed.astimezone(timezone.utc)


def provider_season_id(value: Any) -> str:
    """Canonical join-key form, e.g. 20262027. Verbatim from the source, never re-derived."""
    text = str(value or "").strip()
    if not text.isdigit() or len(text) != 8:
        raise NHLSogIngestionError("NHL_SOG_SEASON_ID_INVALID", str(value))
    return text


def season_label(provider_season_id_value: str) -> str:
    """Display-only form derived from provider_season_id, e.g. "2026-2027". Never a join key."""
    sid = provider_season_id(provider_season_id_value)
    return f"{sid[:4]}-{sid[4:]}"


def resolve_stat_alias(raw_label: Any, *, platform: str | None = None) -> str:
    """Resolve a raw source stat label to the canonical stat_type.

    Global aliases apply regardless of platform. A source-scoped alias
    applies only for its declared platform. Anything unresolved is a typed
    blocker, never a best-guess coercion.
    """
    normalized = str(raw_label or "").strip().lower()
    if not normalized:
        raise NHLSogIngestionError("PROP_STAT_ALIAS_UNRECOGNIZED", "empty stat label")
    if normalized in GLOBAL_STAT_ALIASES:
        return GLOBAL_STAT_ALIASES[normalized]
    if platform:
        scoped = SOURCE_SCOPED_STAT_ALIASES.get(str(platform).strip().upper(), {})
        if normalized in scoped:
            return scoped[normalized]
    raise NHLSogIngestionError("PROP_STAT_ALIAS_UNRECOGNIZED", f"unrecognized stat label: {raw_label!r}")


@dataclass(frozen=True)
class TeamIdentity:
    team_id: str  # stable canonical/provider numeric team identifier
    team_abbreviation: str  # display/normalization evidence only, never a join key


@dataclass(frozen=True)
class SkaterGameSogRecord:
    """One immutable player x game Shots on Goal observation.

    This is the settled historical fact, provenance-tagged. It is NOT a
    pregame feature -- see build_pregame_snapshot for the leakage-safe
    selection primitive that consumes strictly-prior records like this one.
    """

    canonical_game_id: str
    provider_game_id: str
    provider_season_id: str
    season_label: str
    game_start_time: str
    home_team: TeamIdentity
    away_team: TeamIdentity
    player_id: str
    player_name: str
    position: str
    team_id: str  # this skater's team for this specific game (trade-safe)
    stat_type: str
    participation_status: str  # DRESSED_PLAYED | SCRATCHED_DNP
    actual_value: int | None  # SOG; None iff participation_status == SCRATCHED_DNP
    toi: str | None  # preserved verbatim if present in the official box score; no derived rate
    source: str
    source_uri: str
    source_retrieved_at: str
    source_payload_sha256: str
    effective_at: str  # this game's settlement instant (game_start_time is the pregame anchor;
                        # effective_at marks when this fact became true/observable, i.e. post-game)
    feature_schema_version: str = FEATURE_SCHEMA_VERSION
    can_execute: bool = False
    research_evidence_only: bool = True

    @property
    def canonical_key(self) -> tuple[str, str, str]:
        """Primary key for dedup/idempotent merge: (game, player, stat)."""
        return (self.canonical_game_id, self.player_id, self.stat_type)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["home_team"] = asdict(self.home_team)
        d["away_team"] = asdict(self.away_team)
        return d


@dataclass(frozen=True)
class _ParsedSkaterStat:
    """Internal intermediate result: fields extracted from one raw stat
    entry, before the caller attaches game/team/provenance context."""

    player_id: str
    player_name: str
    position: str
    actual_value: int | None
    toi: str | None


@dataclass(frozen=True)
class UnresolvedSkaterEntry:
    """A skater entry that could not be resolved to a valid record. Never
    fabricated as a zero or silently dropped without a reason."""

    reason_code: str
    reason_detail: str
    raw_entry: Mapping[str, Any]


@dataclass(frozen=True)
class GameIngestionResult:
    canonical_game_id: str
    records: tuple[SkaterGameSogRecord, ...]
    unresolved: tuple[UnresolvedSkaterEntry, ...]
    source_payload_sha256: str
    postponed: bool = False


def _team_identity(raw_team: Mapping[str, Any]) -> TeamIdentity:
    team_id = str(raw_team.get("id") if raw_team.get("id") is not None else "").strip()
    abbrev = str(raw_team.get("abbrev") or "").strip().upper()
    if not team_id or not abbrev:
        raise NHLSogIngestionError("NHL_SOG_TEAM_IDENTITY_INCOMPLETE", json.dumps(dict(raw_team), default=str))
    return TeamIdentity(team_id=team_id, team_abbreviation=abbrev)


def _parse_skater_stat_entry(
    raw_entry: Mapping[str, Any],
    *,
    participation_status: str,
) -> "_ParsedSkaterStat | UnresolvedSkaterEntry":
    player_id = str(raw_entry.get("playerId") if raw_entry.get("playerId") is not None else "").strip()
    name_field = raw_entry.get("name")
    player_name = ""
    if isinstance(name_field, Mapping):
        player_name = str(name_field.get("default") or "").strip()
    elif isinstance(name_field, str):
        player_name = name_field.strip()
    position = str(raw_entry.get("position") or "").strip().upper()

    if not player_id:
        return UnresolvedSkaterEntry("NHL_SOG_PLAYER_ID_MISSING", "playerId missing or blank", raw_entry)
    if position and position not in VALID_POSITIONS:
        # Goalies (position "G") and any unrecognized position are out of
        # scope for this skater-only vertical -- excluded, not fabricated.
        return UnresolvedSkaterEntry("NHL_SOG_POSITION_UNSUPPORTED", position, raw_entry)

    if participation_status == PARTICIPATION_SCRATCHED_DNP:
        actual_value: int | None = None
    else:
        raw_sog = raw_entry.get("sog")
        if raw_sog is None:
            return UnresolvedSkaterEntry("NHL_SOG_VALUE_MISSING", "sog missing for dressed skater", raw_entry)
        if isinstance(raw_sog, bool) or not isinstance(raw_sog, (int, float)) or raw_sog < 0:
            return UnresolvedSkaterEntry("NHL_SOG_VALUE_INVALID", str(raw_sog), raw_entry)
        actual_value = int(raw_sog)

    toi_raw = raw_entry.get("toi")
    toi = str(toi_raw).strip() if toi_raw not in (None, "") else None

    return _ParsedSkaterStat(
        player_id=player_id,
        player_name=player_name,
        position=position or "UNKNOWN",
        actual_value=actual_value,
        toi=toi,
    )


def ingest_settled_game_skater_sog(
    boxscore_raw: Mapping[str, Any],
    *,
    scratches_raw: Sequence[Mapping[str, Any]] | None = None,
    source_uri: str,
    retrieved_at: str | None = None,
) -> GameIngestionResult:
    """Ingest one settled NHL game's skater box score into immutable
    player x game SOG records.

    Expected raw shape (documented contract for this PR; a live-endpoint
    translator into this shape is a separate, later integration detail):

        {
          "id": "<canonical/provider game id>",
          "season": "<8-digit provider_season_id>",
          "gameState": "OFF" | "FINAL" | "PPD" | ...,
          "startTimeUTC": "<ISO-8601>",
          "homeTeam": {"id": <int>, "abbrev": "TOR"},
          "awayTeam": {"id": <int>, "abbrev": "BOS"},
          "playerByGameStats": {
            "homeTeam": {"forwards": [...], "defense": [...]},
            "awayTeam": {"forwards": [...], "defense": [...]}
          }
        }

    Each stat entry: {"playerId": <int>, "name": {"default": "..."},
    "position": "C"|"LW"|"RW"|"D", "sog": <int>, "toi": "19:32"}.

    `scratches_raw`, if given, is a separate list of
    {"teamId": <int>, "playerId": <int>} entries representing confirmed
    DNP/scratches for this game -- distinct source from the boxscore
    (design doc Section C.9), never inferred from a skater's absence alone
    (an absent skater with no corroborating scratch record is unresolved,
    not assumed scratched and not assumed to have played).
    """
    retrieved_at = retrieved_at or _utcnow_iso()
    payload_hash = _hash_payload(boxscore_raw)

    game_state = str(boxscore_raw.get("gameState") or "").strip().upper()
    if game_state in {"PPD", "PPD.", "POSTPONED"}:
        game_id = str(boxscore_raw.get("id") or "").strip()
        return GameIngestionResult(
            canonical_game_id=game_id,
            records=(),
            unresolved=(),
            source_payload_sha256=payload_hash,
            postponed=True,
        )
    if game_state not in {"OFF", "FINAL"}:
        raise NHLSogIngestionError("NHL_SOG_GAME_NOT_SETTLED", game_state or "MISSING")

    game_id = str(boxscore_raw.get("id") or "").strip()
    start = str(boxscore_raw.get("startTimeUTC") or "").strip()
    raw_season = boxscore_raw.get("season")
    home_raw = boxscore_raw.get("homeTeam")
    away_raw = boxscore_raw.get("awayTeam")
    stats_raw = boxscore_raw.get("playerByGameStats")

    if not game_id or not start or raw_season is None or not isinstance(home_raw, Mapping) or not isinstance(away_raw, Mapping) or not isinstance(stats_raw, Mapping):
        raise NHLSogIngestionError("NHL_SOG_BOXSCORE_SCHEMA_INVALID", "missing required top-level game/team/stat fields")

    start_utc = _aware(start)
    sid = provider_season_id(raw_season)
    slabel = season_label(sid)
    home_team = _team_identity(home_raw)
    away_team = _team_identity(away_raw)

    home_stats = stats_raw.get("homeTeam")
    away_stats = stats_raw.get("awayTeam")
    if not isinstance(home_stats, Mapping) or not isinstance(away_stats, Mapping):
        raise NHLSogIngestionError("NHL_SOG_BOXSCORE_SCHEMA_INVALID", "playerByGameStats missing home/away team blocks")

    scratch_ids: set[tuple[str, str]] = set()  # (team_id, player_id)
    for entry in scratches_raw or ():
        team_id = str(entry.get("teamId") if entry.get("teamId") is not None else "").strip()
        player_id = str(entry.get("playerId") if entry.get("playerId") is not None else "").strip()
        if team_id and player_id:
            scratch_ids.add((team_id, player_id))

    records: list[SkaterGameSogRecord] = []
    unresolved: list[UnresolvedSkaterEntry] = []
    seen_dressed_ids: set[str] = set()
    conflicted_ids: set[str] = set()

    for team, stats_block in ((home_team, home_stats), (away_team, away_stats)):
        entries: list[Mapping[str, Any]] = []
        for group in ("forwards", "defense"):
            block = stats_block.get(group)
            if isinstance(block, list):
                entries.extend(e for e in block if isinstance(e, Mapping))
        for raw_entry in entries:
            parsed = _parse_skater_stat_entry(raw_entry, participation_status=PARTICIPATION_DRESSED_PLAYED)
            if isinstance(parsed, UnresolvedSkaterEntry):
                unresolved.append(parsed)
                continue
            if (team.team_id, parsed.player_id) in scratch_ids:
                # A player cannot be both dressed-with-stats and scratched for
                # the same game -- that is a source data conflict, never
                # silently resolved by picking one side.
                unresolved.append(
                    UnresolvedSkaterEntry(
                        "NHL_SOG_PARTICIPATION_CONFLICT",
                        f"player {parsed.player_id} appears both dressed and scratched",
                        raw_entry,
                    )
                )
                conflicted_ids.add(parsed.player_id)
                continue
            seen_dressed_ids.add(parsed.player_id)
            records.append(
                SkaterGameSogRecord(
                    canonical_game_id=game_id,
                    provider_game_id=game_id,
                    provider_season_id=sid,
                    season_label=slabel,
                    game_start_time=start_utc.isoformat(),
                    home_team=home_team,
                    away_team=away_team,
                    player_id=parsed.player_id,
                    player_name=parsed.player_name,
                    position=parsed.position,
                    team_id=team.team_id,
                    stat_type=STAT_TYPE,
                    participation_status=PARTICIPATION_DRESSED_PLAYED,
                    actual_value=parsed.actual_value,
                    toi=parsed.toi,
                    source=SOURCE_ID,
                    source_uri=source_uri,
                    source_retrieved_at=retrieved_at,
                    source_payload_sha256=payload_hash,
                    effective_at=start_utc.isoformat(),
                )
            )

    for team_id, player_id in scratch_ids:
        if player_id in seen_dressed_ids or player_id in conflicted_ids:
            continue  # dressed-and-recorded, or already reported as a participation conflict above
        team = home_team if team_id == home_team.team_id else away_team if team_id == away_team.team_id else None
        if team is None:
            unresolved.append(
                UnresolvedSkaterEntry("NHL_SOG_SCRATCH_TEAM_UNRESOLVED", team_id, {"teamId": team_id, "playerId": player_id})
            )
            continue
        records.append(
            SkaterGameSogRecord(
                canonical_game_id=game_id,
                provider_game_id=game_id,
                provider_season_id=sid,
                season_label=slabel,
                game_start_time=start_utc.isoformat(),
                home_team=home_team,
                away_team=away_team,
                player_id=player_id,
                player_name="",
                position="UNKNOWN",
                team_id=team_id,
                stat_type=STAT_TYPE,
                participation_status=PARTICIPATION_SCRATCHED_DNP,
                actual_value=None,
                toi=None,
                source=SOURCE_ID,
                source_uri=source_uri,
                source_retrieved_at=retrieved_at,
                source_payload_sha256=payload_hash,
                effective_at=start_utc.isoformat(),
            )
        )

    return GameIngestionResult(
        canonical_game_id=game_id,
        records=tuple(records),
        unresolved=tuple(unresolved),
        source_payload_sha256=payload_hash,
    )


def merge_records(
    existing: Iterable[SkaterGameSogRecord],
    incoming: Iterable[SkaterGameSogRecord],
) -> tuple[SkaterGameSogRecord, ...]:
    """Deterministic, idempotent merge keyed by canonical_key.

    Re-ingesting the same settled game must never create duplicates. A
    canonical key that already exists with *different* content (a settled
    fact changing) is a data-integrity conflict, never silently overwritten,
    since MLB/WNBA precedent treats settled prediction/outcome identity as
    immutable.
    """
    by_key: dict[tuple[str, str, str], SkaterGameSogRecord] = {r.canonical_key: r for r in existing}
    for record in incoming:
        key = record.canonical_key
        if key in by_key and by_key[key] != record:
            raise NHLSogIngestionError(
                "NHL_SOG_IMMUTABLE_RECORD_CONFLICT",
                f"canonical_key={key} already recorded with different content",
            )
        by_key[key] = record
    return tuple(by_key[k] for k in sorted(by_key))


@dataclass(frozen=True)
class PregameSnapshot:
    """Leakage-safe selection of strictly-prior settled records for one
    player as of a given evaluation instant. This is a selection primitive
    only -- no rolling rate, suppression, or role feature is computed here;
    that belongs to PR 2's feature hydration."""

    player_id: str
    as_of: str
    prior_records: tuple[SkaterGameSogRecord, ...]
    schema_version: str = FEATURE_SCHEMA_VERSION
    can_execute: bool = False


def build_pregame_snapshot(
    player_id: str,
    as_of: str,
    *,
    target_game_start_time: str,
    history: Iterable[SkaterGameSogRecord],
) -> PregameSnapshot:
    """Select only records whose game_start_time is strictly before both
    `as_of` and `target_game_start_time`. Rejects outright if `as_of` is at
    or after the target game's puck-drop time (post-start hydration)."""
    as_of_dt = _aware(as_of)
    target_start_dt = _aware(target_game_start_time)
    if as_of_dt >= target_start_dt:
        raise NHLSogIngestionError("EVENT_ALREADY_STARTED", f"as_of={as_of} >= target_game_start_time={target_game_start_time}")

    prior = tuple(
        sorted(
            (
                r
                for r in history
                if r.player_id == player_id and _aware(r.game_start_time) < target_start_dt and _aware(r.game_start_time) <= as_of_dt
            ),
            key=lambda r: r.game_start_time,
        )
    )
    return PregameSnapshot(player_id=player_id, as_of=as_of, prior_records=prior)


__all__ = [
    "CAN_EXECUTE",
    "PROBABILITY_PUBLISHABLE",
    "RESEARCH_EVIDENCE_ONLY",
    "SOURCE_ID",
    "STAT_TYPE",
    "GLOBAL_STAT_ALIASES",
    "SOURCE_SCOPED_STAT_ALIASES",
    "NHLSogIngestionError",
    "TeamIdentity",
    "SkaterGameSogRecord",
    "UnresolvedSkaterEntry",
    "GameIngestionResult",
    "PregameSnapshot",
    "provider_season_id",
    "season_label",
    "resolve_stat_alias",
    "ingest_settled_game_skater_sog",
    "merge_records",
    "build_pregame_snapshot",
]
