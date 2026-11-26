"""Trusted-collector signed offer/payout integrity (NOT PrizePicks API access).

An internal HMAC validates unchanged captures from a trusted ingestion boundary.
It is NOT proof that PrizePicks endorsed the source or the data-rights claim.
No scraping, source networking, user-supplied credentials or live execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
from typing import Any, Mapping, Sequence

from v17.niche_prop_research import classify_niche_prop

CAN_EXECUTE = False
QUOTE_VERSION = "wow.v17.niche-offer-integrity.v1"
MAX_CAPTURE_AGE_SECONDS = 900
EXACT_FIELDS = (
    "platform", "sport", "offer_id", "official_event_id", "official_player_ids",
    "category", "stat_type", "market_period", "side", "exact_line",
    "projection_type", "event_start_utc", "settlement_basis",
    "official_player_position", "combo_team_ids", "home_team_id", "away_team_id",
)
ALLOWED_TIERS = {"STANDARD", "GOBLIN", "DEMON"}


class CaptureHold(ValueError):
    """Reject untrusted, mismatched, expired or incomplete capture."""


def _hold(code: str) -> None:
    raise CaptureHold(code)


def _json(obj: Any) -> bytes:
    try:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        _hold("CAPTURE_LINE_OR_PAYLOAD_INVALID")


def _digest(obj: Any) -> str:
    return hashlib.sha256(_json(obj)).hexdigest()


def _date(value: Any) -> datetime:
    if not isinstance(value, str):
        _hold("CAPTURE_TIMESTAMP_INVALID")
    try:
        time = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _hold("CAPTURE_TIMESTAMP_INVALID")
    if time.tzinfo is None or time.utcoffset() is None:
        _hold("CAPTURE_TIMESTAMP_UNZONED")
    return time.astimezone(timezone.utc)


def _key(secret: Any) -> bytes:
    if not isinstance(secret, bytes) or len(secret) < 32:
        _hold("SIGNING_SECRET_UNAVAILABLE")
    return secret


def _finite(v: Any, *, min_value: float = 0) -> float:
    if isinstance(v, bool) or not isinstance(v, (float, int)):
        _hold("EXACT_LINE_OR_PAYOUT_INVALID")
    number = float(v)
    if not math.isfinite(number) or not min_value <= number <= 100000:
        _hold("EXACT_LINE_OR_PAYOUT_INVALID")
    return number


def seal_capture_for_trusted_collector(payload: Mapping[str, Any], secret: bytes) -> dict[str, Any]:
    """Signing primitive for a privileged collector; never accept chat claims as collector input.

    A caller controlling the signing key can forge receipts. Runtime key ownership,
    provider license and actual source capture must be independently established.
    """
    key = _key(secret)
    if not isinstance(payload, Mapping):
        _hold("CAPTURE_PAYLOAD_INVALID")
    blob = _json(dict(payload))
    return {
        "version": QUOTE_VERSION,
        "payload": dict(payload),
        "payload_digest": hashlib.sha256(blob).hexdigest(),
        "signature": hmac.new(key, blob, hashlib.sha256).hexdigest(),
        "can_execute": False,
    }


def _verify_envelope(envelope: Mapping[str, Any], secret: bytes) -> dict[str, Any]:
    key = _key(secret)
    if not isinstance(envelope, Mapping) or envelope.get("version") != QUOTE_VERSION:
        _hold("CAPTURE_ENVELOPE_INVALID")
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        _hold("CAPTURE_PAYLOAD_INVALID")
    blob = _json(payload)
    computed = hashlib.sha256(blob).hexdigest()
    signed = hmac.new(key, blob, hashlib.sha256).hexdigest()
    if (not isinstance(envelope.get("signature"), str)
            or not hmac.compare_digest(envelope["signature"], signed)
            or not isinstance(envelope.get("payload_digest"), str)
            or not hmac.compare_digest(envelope["payload_digest"], computed)):
        _hold("CAPTURE_SIGNATURE_OR_DIGEST_INVALID")
    return payload


def _check_rights(payload: Mapping[str, Any], approved_sources: Mapping[str, str]) -> None:
    if not isinstance(approved_sources, Mapping):
        _hold("SOURCE_RIGHTS_APPROVAL_UNAVAILABLE")
    provider = payload.get("source_id")
    rights = payload.get("rights_receipt_id")
    if (not isinstance(provider, str) or not provider
            or not isinstance(rights, str) or not rights
            or approved_sources.get(provider) != rights):
        _hold("SOURCE_RIGHTS_APPROVAL_MISSING_OR_CONFLICT")


def _check_capture_time(captured: Any, start: Any, as_of: Any) -> datetime:
    seen, kickoff, now = _date(captured), _date(start), _date(as_of)
    if seen > now:
        _hold("CAPTURE_FUTURE_DATED")
    if now >= kickoff or seen >= kickoff:
        _hold("CAPTURE_POST_START_INVALID")
    if (now - seen).total_seconds() > MAX_CAPTURE_AGE_SECONDS:
        _hold("CAPTURE_STALE")
    return seen


def _snapshot(payload: Mapping[str, Any]) -> None:
    h = payload.get("source_snapshot_sha256")
    if (not isinstance(h, str) or len(h) != 64
            or any(ch not in "0123456789abcdef" for ch in h.lower())):
        _hold("CAPTURE_SOURCE_SNAPSHOT_DIGEST_MISSING")


def verify_capture(envelope: Mapping[str, Any], secret: bytes,
                   expected: Mapping[str, Any], *,
                   as_of_utc: str, approved_sources: Mapping[str, str]) -> dict[str, Any]:
    """Verify signed *internal* capture and exact offered market identity."""
    payload = _verify_envelope(envelope, secret)
    if payload.get("kind") != "PROP_OFFER" or payload.get("platform") != "PRIZEPICKS":
        _hold("CAPTURE_UNSUPPORTED_OFFER_TYPE")
    _check_rights(payload, approved_sources)
    _snapshot(payload)
    if not isinstance(expected, Mapping) or any(
        payload.get(f) != expected.get(f) for f in EXACT_FIELDS
    ):
        _hold("EXACT_OFFER_IDENTITY_MISMATCH")
    if payload.get("status") != "ACTIVE":
        _hold("CAPTURE_OFFER_NOT_ACTIVE")
    if payload.get("projection_type") not in ALLOWED_TIERS:
        _hold("CAPTURE_PROJECTION_TIER_UNSUPPORTED")
    if not isinstance(payload.get("offer_id"), str) or not payload["offer_id"]:
        _hold("CAPTURE_OFFER_ID_MISSING")
    if not isinstance(payload.get("official_event_id"), str) or not payload["official_event_id"]:
        _hold("CAPTURE_EVENT_ID_MISSING")
    _finite(payload.get("exact_line"))
    _check_capture_time(payload.get("captured_at_utc"),
                        payload.get("event_start_utc"), as_of_utc)
    ids = payload.get("official_player_ids")
    if not isinstance(ids, list) or any(not isinstance(x, str) or not x for x in ids):
        _hold("CAPTURE_PLAYER_ID_MISSING")
    if payload.get("category") == "NFL_1Q_TWO_KICKER_FG_OVER_0_5":
        if len(ids) != 2 or len({x.casefold() for x in ids}) != 2:
            _hold("KICKER_COMBO_PLAYER_IDENTITY_INVALID")
        teams = payload.get("combo_team_ids")
        home, away = payload.get("home_team_id"), payload.get("away_team_id")
        if (not isinstance(teams, list) or len(teams) != 2
                or any(not isinstance(t, str) or not t for t in teams)
                or not isinstance(home, str) or not home
                or not isinstance(away, str) or not away or home == away
                or set(teams) != {home, away}):
            _hold("KICKER_COMBO_TEAM_BINDING_INVALID")
        if (payload.get("stat_type") != "FIELD_GOALS_MADE_COMBO"
                or payload.get("market_period") != "FIRST_QUARTER"
                or payload.get("side") != "MORE"
                or _finite(payload.get("exact_line")) != 0.5):
            _hold("KICKER_COMBO_ROUTE_INVALID")
    elif len(ids) != 1:
        _hold("CAPTURE_SINGLE_PLAYER_MARKET_INVALID")
    if (not isinstance(payload.get("settlement_basis"), str)
            or not payload["settlement_basis"].strip()):
        _hold("EXACT_SETTLEMENT_BASIS_MISSING")
    # A valid signature cannot make a player-points prop a low-volume FTA
    # under, nor make a full-game attempt count a first-quarter QB prop.
    category_match = classify_niche_prop({
        "sport": payload.get("sport"),
        "stat_type": payload.get("stat_type"),
        "market_period": payload.get("market_period"),
        "side": payload.get("side"),
        "exact_line": payload.get("exact_line"),
        "official_event_id": payload.get("official_event_id"),
        "official_player_id": ids[0] if len(ids) == 1 else None,
        "combo_participant_ids": ids if len(ids) == 2 else None,
        "combo_team_ids": payload.get("combo_team_ids"),
        "home_team_id": payload.get("home_team_id"),
        "away_team_id": payload.get("away_team_id"),
        "official_player_position": payload.get("official_player_position"),
    })
    if (category_match["category"] != payload.get("category")
            or category_match["identity_gaps"]):
        _hold("NICHE_ROUTE_OR_PLAYER_IDENTITY_MISMATCH")
    return {
        "status": "INTERNAL_CAPTURE_INTEGRITY_ONLY",
        "quote_integrity_verified": True,
        "offer_binding_digest": envelope["payload_digest"],
        "source_content_independently_verified": False,
        "provider_rights_independently_verified": False,
        "official_provider_attestation": None,
        "can_execute": False,
    }


def verify_lineup_payout(envelope: Mapping[str, Any], secret: bytes,
                         offers: Sequence[Mapping[str, Any]], *,
                         as_of_utc: str,
                         approved_sources: Mapping[str, str]) -> dict[str, Any]:
    """Verify one signed *whole lineup* payout quote, not a generic standard schedule."""
    payload = _verify_envelope(envelope, secret)
    if payload.get("kind") != "LINEUP_PAYOUT" or payload.get("platform") != "PRIZEPICKS":
        _hold("LINEUP_PAYOUT_TYPE_INVALID")
    _check_rights(payload, approved_sources)
    _snapshot(payload)
    if not isinstance(offers, (list, tuple)) or not 2 <= len(offers) <= 6:
        _hold("LINEUP_LEG_COUNT_UNSUPPORTED")
    if payload.get("structure") not in {"FLEX", "POWER"} or type(payload.get("leg_count")) is not int:
        _hold("LINEUP_STRUCTURE_INVALID")
    if payload["leg_count"] != len(offers):
        _hold("LINEUP_LEG_COUNT_MISMATCH")
    for signed in offers:
        original = _verify_envelope(signed, secret)
        verify_capture(signed, secret, original,
                       as_of_utc=as_of_utc, approved_sources=approved_sources)
    offer_payloads = [signed["payload"] for signed in offers]
    binding = [signed["payload_digest"] for signed in offers]
    if payload.get("ordered_offer_binding_digests") != binding or len(set(binding)) != len(binding):
        _hold("LINEUP_BINDING_MISMATCH")
    first_start = min(_date(row["event_start_utc"]) for row in offer_payloads)
    latest_offer = max(_date(row["captured_at_utc"]) for row in offer_payloads)
    capture = _check_capture_time(payload.get("captured_at_utc"),
                                  payload.get("earliest_event_start_utc"), as_of_utc)
    if _date(payload["earliest_event_start_utc"]) != first_start:
        _hold("LINEUP_START_MISMATCH")
    if capture < latest_offer:
        _hold("LINEUP_CAPTURE_BEFORE_OFFERS")
    multipliers = payload.get("payout_by_hits")
    if (not isinstance(multipliers, dict)
            or set(multipliers) != {str(i) for i in range(len(offers) + 1)}):
        _hold("LINEUP_PAYOUT_SCHEDULE_INCOMPLETE")
    safe = {k: _finite(v) for k, v in multipliers.items()}
    if safe[str(len(offers))] <= 0:
        _hold("LINEUP_PAYOUT_TOP_MULTIPLIER_MISSING")
    return {
        "status": "INTERNAL_CAPTURE_INTEGRITY_ONLY",
        "lineup_binding_digest": envelope["payload_digest"],
        "ordered_offer_binding_digests": binding,
        "payout_by_hits": safe,
        "gross_multiplier_if_all_correct": safe[str(len(offers))],
        "source_content_independently_verified": False,
        "provider_rights_independently_verified": False,
        "certified_joint_probability": None,
        "can_execute": False,
    }
