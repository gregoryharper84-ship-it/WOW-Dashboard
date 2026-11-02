"""Read-only LLP event-level view of independent, governed market receipts.

Preserves each market's controlling specialist. Does not train, score, blend,
calibrate, certify, rank, execute, or assign terminal status. A market appears
as governed-evidence-visible only when its *upstream* receipt says publishable,
rank eligible, is terminal-authoritative, and carries the required numeric proof.
Research/provisional/mismatched evidence is held independently per market.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Iterable, Mapping

CAN_EXECUTE = False
CONTROLLING_ENGINE = "LLP_TEAM_BETTING_ENGINE"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
CONTRACT = "LLP_TEAM_CROSS_MARKET_INTELLIGENCE_V1"
SUPPORTED_SPORTS = frozenset({"NFL", "NBA", "NHL"})
MARKETS = frozenset({"OUTRIGHT_WINNER", "POINT_SPREAD", "GAME_TOTAL"})


def _str(value: Any) -> str:
    return str(value or "").strip()


def _utc(value: Any) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(_str(value).replace("Z", "+00:00"))
        return stamp.astimezone(timezone.utc) if stamp.utcoffset() is not None else None
    except (TypeError, ValueError):
        return None


def _prob(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    val = float(value)
    return val if isfinite(val) and 0 <= val <= 1 else None


def _line(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    val = float(value)
    return val if isfinite(val) else None


def _triplet_valid(lower: Any, point: Any, upper: Any) -> bool:
    p = [_prob(x) for x in (lower, point, upper)]
    return all(x is not None for x in p) and p[0] <= p[1] <= p[2]


def _distribution_valid(values: Iterable[Any]) -> bool:
    parts = [_prob(v) for v in values]
    return all(v is not None for v in parts) and abs(sum(parts) - 1.0) <= 1e-9


def build_llp_team_intelligence_bundle(
    *, event: Mapping[str, Any], receipts: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Assemble a non-authoritative, read-only event view from already-scored receipts.

    Every supplied receipt has the same required canonical event identity. A
    mismatched receipt is visible as held but cannot pass probabilities into the
    current event. Exact market line and settlement rules remain distinct.
    """
    sport = _str(event.get("sport")).upper()
    event_id = _str(event.get("official_event_id"))
    home = _str(event.get("home_team")).upper()
    away = _str(event.get("away_team")).upper()
    start = _utc(event.get("event_start_time_utc"))
    if (sport not in SUPPORTED_SPORTS or not event_id or not home or not away
            or home == away or start is None):
        raise ValueError("LLP_BUNDLE_CANONICAL_EVENT_INVALID")

    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    winner_source: str | None = None
    for raw in receipts:
        market = _str(raw.get("market_family")).upper()
        if market not in MARKETS:
            raise ValueError("LLP_BUNDLE_MARKET_UNSUPPORTED")
        side = _str(raw.get("side")).upper()
        line = _line(raw.get("exact_line")) if market != "OUTRIGHT_WINNER" else None
        line_key = str(line) if line is not None else ""
        key = (market, side, line_key)
        if key in seen:
            raise ValueError("LLP_BUNDLE_DUPLICATE_MARKET_THESIS")
        seen.add(key)
        reasons: list[str] = []
        if (_str(raw.get("sport")).upper() != sport
                or _str(raw.get("official_event_id")) != event_id
                or _str(raw.get("home_team")).upper() != home
                or _str(raw.get("away_team")).upper() != away
                or _utc(raw.get("event_start_time_utc")) != start):
            reasons.append("CROSS_MARKET_EVENT_IDENTITY_MISMATCH")
        source = _str(raw.get("source_receipt_id"))
        if not source:
            reasons.append("SOURCE_RECEIPT_MISSING")
        if (raw.get("probability_publishable") is not True
                or raw.get("rank_eligible") is not True):
            reasons.append("UPSTREAM_UNCERTIFIED_OR_HELD")
        if _str(raw.get("terminal_authority")) != TERMINAL_AUTHORITY:
            reasons.append("TERMINAL_RECEIPT_NOT_VERIFIED")
        if _str(raw.get("controlling_engine_identity")) != CONTROLLING_ENGINE:
            reasons.append("WRONG_CONTROLLING_ENGINE")
        if not _str(raw.get("model_version")) or not _str(raw.get("calibration_version")):
            reasons.append("MODEL_CALIBRATION_PROVENANCE_MISSING")
        model_at = _utc(raw.get("model_timestamp"))
        source_at = _utc(raw.get("source_snapshot_timestamp"))
        if model_at is None or source_at is None or source_at > model_at or model_at >= start:
            reasons.append("PREGAME_MODEL_FRESHNESS_UNPROVEN")
        if market == "OUTRIGHT_WINNER":
            if side not in {home, away}:
                reasons.append("WINNER_SIDE_INVALID")
            if not _triplet_valid(
                raw.get("calibrated_probability_lower_bound"),
                raw.get("calibrated_probability"),
                raw.get("calibrated_probability_upper_bound"),
            ):
                reasons.append("WINNER_CALIBRATED_BOUNDS_MISSING")
        elif market == "POINT_SPREAD":
            if side not in {"HOME", "AWAY"} or line is None or not _str(raw.get("settlement_basis")):
                reasons.append("SPREAD_EXACT_LINE_OR_SETTLEMENT_MISSING")
            if not _distribution_valid((raw.get("p_cover"),raw.get("p_push"),raw.get("p_not_cover"))):
                reasons.append("SPREAD_DISTRIBUTION_INVALID")
            if not _triplet_valid(
                raw.get("calibrated_probability_lower_bound"),
                raw.get("p_cover"),
                raw.get("calibrated_probability_upper_bound"),
            ):
                reasons.append("SPREAD_BOUNDS_MISSING")
        else:
            if side not in {"OVER", "UNDER"} or line is None or line < 0 or not _str(raw.get("settlement_basis")):
                reasons.append("TOTAL_EXACT_LINE_OR_SETTLEMENT_MISSING")
            if not _distribution_valid((raw.get("p_over"),raw.get("p_under"),raw.get("p_push"))):
                reasons.append("TOTAL_DISTRIBUTION_INVALID")
            target = raw.get("p_over") if side == "OVER" else raw.get("p_under")
            if not _triplet_valid(raw.get("calibrated_probability_lower_bound"), target,
                                  raw.get("calibrated_probability_upper_bound")):
                reasons.append("TOTAL_BOUNDS_MISSING")
        # Never promote a research/shadow row by accepting fabricated booleans.
        if _str(raw.get("model_class")).upper() != "CERTIFIED_FITTED":
            reasons.append("MODEL_CLASS_NOT_CERTIFIED_FITTED")
        if raw.get("can_execute") is not False:
            reasons.append("EXECUTION_INVARIANT_UNPROVEN")
        eligible = not reasons
        projected = {
            "market_family": market, "side": side or None, "exact_line": line,
            "status": "GOVERNED_RECEIPT_VISIBLE" if eligible else "HELD_EVIDENCE_ONLY",
            "reason_codes": reasons, "source_receipt_id": source or None,
            "model_version": _str(raw.get("model_version")) or None,
            "calibration_version": _str(raw.get("calibration_version")) or None,
            "settlement_basis": _str(raw.get("settlement_basis")) or None,
            "upstream_probability_publishable": raw.get("probability_publishable") is True,
            "upstream_rank_eligible": raw.get("rank_eligible") is True,
            "evidence_visible": eligible,
            "can_execute": False,
        }
        if eligible:
            if market == "OUTRIGHT_WINNER":
                winner_source = source
                projected["calibrated_probability"] = raw["calibrated_probability"]
            elif market == "POINT_SPREAD":
                projected.update({k:raw[k] for k in ("p_cover", "p_push", "p_not_cover")})
            else:
                projected.update({k:raw[k] for k in ("p_over", "p_under", "p_push")})
            projected["calibrated_probability_lower_bound"] = raw["calibrated_probability_lower_bound"]
            projected["calibrated_probability_upper_bound"] = raw["calibrated_probability_upper_bound"]
        result.append(projected)
    return {
        "contract": CONTRACT, "controlling_engine_identity": CONTROLLING_ENGINE,
        "sport": sport, "official_event_id": event_id,
        "home_team": home, "away_team": away,
        "event_start_time_utc": start.isoformat(),
        "markets": result, "governed_winner_receipt_id": winner_source,
        "probabilities_blended": False, "terminal_decision_generated": False,
        "can_execute": False,
    }
