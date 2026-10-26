"""Research-only, literal NBA prop offer identity and reconciliation.

This module intentionally does not score, translate threshold ladders into
sportsbook lines, infer directions, or assert current-board provenance.
An authenticated exact-offer/settlement adapter is a distinct governed layer.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

CAN_EXECUTE = False

# Deliberately closed: newly seen labels receive a typed extraction hold.
_STAT_LABELS = {"points": "POINTS", "rebounds": "REBOUNDS", "assists": "ASSISTS"}
_AT_LEAST = re.compile(r"^(?P<count>[1-9][0-9]*)\+ (?P<stat>points|rebounds|assists)$", re.IGNORECASE)
_OVER = re.compile(r"^OVER (?P<line>[0-9]+(?:\.[0-9]+)?) (?P<stat>points|rebounds|assists)$", re.IGNORECASE)


class NBAOfferIdentityError(ValueError):
    """Typed extraction failure, not a governed backend terminal code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class NBAOfferIdentity:
    source_row: str
    player: str
    matchup: str
    original_label: str
    offer_family: str
    stat_type: str
    threshold: str | None
    explicit_direction: str | None
    period: str
    settlement_predicate: str
    host_disposition: str
    source_capture_timestamp: str | None
    platform: str | None
    can_submit_score_action: bool = False
    can_execute: bool = False

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _nonempty_text(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NBAOfferIdentityError(code)
    return value.strip()


def parse_nba_offer(
    *, source_row: str, player: str, matchup: str, original_label: str,
    source_capture_timestamp: str | None = None, platform: str | None = None,
) -> NBAOfferIdentity:
    """Parse observed wording without asserting fresh availability or model authority.

    Capture timestamp and platform are preserved only if supplied by the actual
    source. An absent timestamp remains absent, never replaced by wall-clock now.
    """
    row = _nonempty_text(source_row, "NBA_OFFER_ROW_KEY_MISSING")
    name = _nonempty_text(player, "NBA_OFFER_PLAYER_MISSING")
    game = _nonempty_text(matchup, "NBA_OFFER_MATCHUP_MISSING")
    label = _nonempty_text(original_label, "NBA_OFFER_LABEL_MISSING")
    if source_capture_timestamp is not None:
        source_capture_timestamp = _nonempty_text(source_capture_timestamp, "NBA_OFFER_CAPTURE_INVALID")
    if platform is not None:
        platform = _nonempty_text(platform, "NBA_OFFER_PLATFORM_INVALID")

    plus = _AT_LEAST.fullmatch(label)
    if plus:
        count = int(plus.group("count"))
        if count > 10_000:
            raise NBAOfferIdentityError("NBA_OFFER_THRESHOLD_UNSUPPORTED")
        stat = _STAT_LABELS[plus.group("stat").lower()]
        return NBAOfferIdentity(
            source_row=row, player=name, matchup=game, original_label=label,
            offer_family="AT_LEAST", stat_type=stat, threshold=str(count),
            explicit_direction=None, period="FULL_GAME",
            settlement_predicate=f"{stat} >= {count}",
            host_disposition="AT_LEAST_THRESHOLD_ADAPTER_NOT_VERIFIED",
            source_capture_timestamp=source_capture_timestamp, platform=platform,
        )

    over = _OVER.fullmatch(label)
    if over:
        try:
            numeric = Decimal(over.group("line"))
        except InvalidOperation as exc:
            raise NBAOfferIdentityError("NBA_OFFER_LINE_INVALID") from exc
        if not numeric.is_finite() or numeric <= 0 or numeric > 10_000:
            raise NBAOfferIdentityError("NBA_OFFER_LINE_UNSUPPORTED")
        stat = _STAT_LABELS[over.group("stat").lower()]
        return NBAOfferIdentity(
            source_row=row, player=name, matchup=game, original_label=label,
            offer_family="NUMERIC_OVER", stat_type=stat,
            threshold=over.group("line"), explicit_direction="MORE",
            period="FULL_GAME", settlement_predicate=f"{stat} > {over.group('line')}",
            host_disposition="PREGAME_EVIDENCE_AND_SOURCE_METADATA_UNRESOLVED",
            source_capture_timestamp=source_capture_timestamp, platform=platform,
        )

    if label.casefold() == "double-double":
        return NBAOfferIdentity(
            source_row=row, player=name, matchup=game, original_label=label,
            offer_family="COMPOUND", stat_type="DOUBLE_DOUBLE", threshold=None,
            explicit_direction=None, period="FULL_GAME",
            settlement_predicate="OFFICIAL_DOUBLE_DOUBLE_RULES_REQUIRED",
            host_disposition="COMPOUND_ROUTE_NOT_VERIFIED",
            source_capture_timestamp=source_capture_timestamp, platform=platform,
        )
    raise NBAOfferIdentityError("NBA_OFFER_UNRECOGNIZED_LABEL")


def reconcile_nba_offer_labels(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Fail closed on a row collision; never silently merge ladder offers.

    This inventory is source-reconciliation only. It never yields Action-ready
    inputs because authentic official identities, exact platform availability,
    source capture and certified route readiness must be established later.
    """
    parsed: list[NBAOfferIdentity] = []
    seen: set[str] = set()
    source_instances = 0
    for row in rows:
        source_instances += 1
        if not isinstance(row, Mapping):
            raise NBAOfferIdentityError("NBA_OFFER_ROW_INVALID")
        try:
            offer = parse_nba_offer(**{
                k: row.get(k) for k in (
                    "source_row", "player", "matchup", "original_label",
                    "source_capture_timestamp", "platform",
                )
            })
        except TypeError as exc:
            raise NBAOfferIdentityError("NBA_OFFER_ROW_INVALID") from exc
        if offer.source_row in seen:
            raise NBAOfferIdentityError("NBA_OFFER_SOURCE_ROW_COLLISION")
        seen.add(offer.source_row)
        parsed.append(offer)
    families = {kind: sum(o.offer_family == kind for o in parsed)
                for kind in ("AT_LEAST", "NUMERIC_OVER", "COMPOUND")}
    return {
        "status": "SOURCE_LABELS_RECONCILED_NO_SCORING_AUTHORITY",
        "source_instances": source_instances,
        "source_rows_reconciled": len(parsed),
        "offer_families": families,
        "action_eligible_rows": 0,
        "score_action_invocations": 0,
        "probability_publishable": False,
        "can_execute": False,
        "rows": [o.to_record() for o in parsed],
    }


__all__ = ["CAN_EXECUTE", "NBAOfferIdentityError", "NBAOfferIdentity", "parse_nba_offer", "reconcile_nba_offer_labels"]
