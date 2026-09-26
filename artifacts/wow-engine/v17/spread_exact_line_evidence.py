"""Exact-line evidence binding/evaluation for the V17 spread challenger.

Sportsbook spreads are query thresholds and evaluation evidence only. They are
never included in the fitted margin model feature vector and are never converted
from sportsbook prices or moneyline probabilities.

The binder is deliberately conservative: only one unambiguous provider event,
main-line pregame quotes, and a deterministic preferred sportsbook are accepted.
Missing or ambiguous evidence remains an explicit blocker rather than being
filled from a synthetic line grid.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
import re
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from v17.spread_margin_challenger import (
    MarginDistributionArtifact,
    MarginTrainingRow,
    SpreadChallengerUnavailable,
    score_home_spread,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
PREFERRED_BOOKS = ("Pinnacle", "Draftkings", "Fanduel")
MAX_EVENT_TIME_DELTA_HOURS = 18.0


@dataclass(frozen=True)
class ExactSpreadEvidence:
    event_id: str
    provider_event_id: str
    sport: str
    event_start_time: str
    sportsbook: str
    quote_timestamp: str
    home_team: str
    away_team: str
    home_spread: float
    market_id: str | None
    snapshot_kind: str

    def payload(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "prediction_authority": False,
            "spread_line_used_as_feature": False,
            "market_probability_substitution_used": False,
            "moneyline_to_spread_conversion_used": False,
            "probability_publishable": False,
            "can_execute": False,
        }


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _team_token(sport: str, value: Any) -> str:
    raw = " ".join(str(value or "").strip().split())
    if not raw:
        return ""
    if str(sport).upper() == "NFL":
        try:
            from v17.nfl_team_event_specialist import NFL_CANONICAL_TEAM_CODES, NFL_TEAM_NAME_TO_ABBREVIATION
            upper = raw.upper()
            if upper in NFL_CANONICAL_TEAM_CODES:
                return upper
            mapped = NFL_TEAM_NAME_TO_ABBREVIATION.get(raw.casefold())
            if mapped:
                return mapped
        except Exception:  # noqa: BLE001 - evidence binding must fail closed below
            return _token(raw)
    return _token(raw)


def _quote_time(row: Mapping[str, Any]) -> datetime | None:
    return _dt(row.get("price_updated_at")) or _dt(row.get("fetched_at"))


def _line_value(row: Mapping[str, Any]) -> float | None:
    try:
        value = float(row.get("line_value"))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _market_is_spread(row: Mapping[str, Any]) -> bool:
    name = _token(row.get("market_name"))
    return str(row.get("market_id") or "") == "2" or name in {"spread", "spreads", "pointspread", "handicap"}


def _group_market_events(rows: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for raw in rows:
        row = dict(raw)
        provider_event_id = str(row.get("provider_event_id") or "").strip()
        if not provider_event_id or not _market_is_spread(row):
            continue
        grouped.setdefault(provider_event_id, []).append(row)
    return grouped


def _event_sides(rows: Sequence[Mapping[str, Any]], sport: str) -> tuple[str, str]:
    home = away = ""
    for row in rows:
        side = str(row.get("participant_type") or "").strip().lower()
        name = row.get("participant_name") or row.get("selection")
        if side == "home" and not home:
            home = _team_token(sport, name)
        elif side == "away" and not away:
            away = _team_token(sport, name)
    return home, away


def _provider_event_start(rows: Sequence[Mapping[str, Any]]) -> datetime | None:
    for row in rows:
        parsed = _dt(row.get("event_start_utc"))
        if parsed is not None:
            return parsed
    return None


def bind_exact_home_spreads(
    *,
    sport: str,
    event_identities: Sequence[Mapping[str, Any]],
    market_rows: Sequence[Mapping[str, Any]],
    preferred_books: Sequence[str] = PREFERRED_BOOKS,
) -> tuple[dict[str, ExactSpreadEvidence], dict[str, Any]]:
    """Bind one deterministic pregame home spread to each unambiguous event."""
    normalized_sport = str(sport).upper()
    provider_events = _group_market_events(market_rows)
    evidence: dict[str, ExactSpreadEvidence] = {}
    blockers: dict[str, str] = {}
    preferred = [str(book).casefold() for book in preferred_books]

    for identity in event_identities:
        event_id = str(identity.get("event_id") or "").strip()
        start = _dt(identity.get("event_start_time"))
        home_name = str(identity.get("home_team") or "").strip()
        away_name = str(identity.get("away_team") or "").strip()
        home = _team_token(normalized_sport, home_name)
        away = _team_token(normalized_sport, away_name)
        if not event_id or start is None or not home or not away:
            if event_id:
                blockers[event_id] = "SPREAD_EVENT_IDENTITY_INCOMPLETE"
            continue

        matches: list[tuple[str, list[dict[str, Any]], datetime]] = []
        for provider_event_id, rows in provider_events.items():
            provider_start = _provider_event_start(rows)
            if provider_start is None:
                continue
            delta_hours = abs((provider_start - start).total_seconds()) / 3600.0
            if delta_hours > MAX_EVENT_TIME_DELTA_HOURS:
                continue
            provider_home, provider_away = _event_sides(rows, normalized_sport)
            if provider_home == home and provider_away == away:
                matches.append((provider_event_id, rows, provider_start))

        if len(matches) != 1:
            blockers[event_id] = "SPREAD_EVENT_NOT_FOUND" if not matches else "SPREAD_EVENT_IDENTITY_AMBIGUOUS"
            continue
        provider_event_id, rows, provider_start = matches[0]

        candidates: list[dict[str, Any]] = []
        for row in rows:
            if row.get("is_live") is True or row.get("is_main_line") is not True:
                continue
            quote = _quote_time(row)
            line = _line_value(row)
            if quote is None or line is None or quote >= provider_start:
                continue
            side = str(row.get("participant_type") or "").strip().lower()
            participant = _team_token(normalized_sport, row.get("participant_name") or row.get("selection"))
            if side != "home" and participant != home:
                continue
            if row.get("is_available") is False and str(row.get("snapshot_kind") or "").upper() not in {"OPEN", "CLOSE"}:
                continue
            candidate = dict(row)
            candidate["_quote_dt"] = quote
            candidate["_line"] = line
            candidates.append(candidate)

        if not candidates:
            blockers[event_id] = "SPREAD_EXACT_LINE_PREGAME_QUOTE_UNAVAILABLE"
            continue

        chosen: dict[str, Any] | None = None
        for book in preferred:
            rows_for_book = [row for row in candidates if str(row.get("sportsbook") or "").casefold() == book]
            if rows_for_book:
                chosen = max(rows_for_book, key=lambda row: row["_quote_dt"])
                break
        if chosen is None:
            chosen = max(candidates, key=lambda row: (row["_quote_dt"], str(row.get("sportsbook") or "")))

        quote = chosen["_quote_dt"]
        evidence[event_id] = ExactSpreadEvidence(
            event_id=event_id,
            provider_event_id=provider_event_id,
            sport=normalized_sport,
            event_start_time=provider_start.isoformat(),
            sportsbook=str(chosen.get("sportsbook") or "UNKNOWN"),
            quote_timestamp=quote.isoformat(),
            home_team=home_name,
            away_team=away_name,
            home_spread=float(chosen["_line"]),
            market_id=str(chosen.get("market_id")) if chosen.get("market_id") is not None else None,
            snapshot_kind=str(chosen.get("snapshot_kind") or "CURRENT").upper(),
        )

    return evidence, {
        "event_identity_n": len(event_identities),
        "provider_event_n": len(provider_events),
        "bound_event_n": len(evidence),
        "coverage": len(evidence) / len(event_identities) if event_identities else 0.0,
        "blocker_counts": {code: list(blockers.values()).count(code) for code in sorted(set(blockers.values()))},
        "preferred_books": list(preferred_books),
        "prediction_authority": False,
        "can_execute": False,
    }


def _ece(probabilities: Sequence[float], outcomes: Sequence[int], bins: int = 10) -> float:
    if not probabilities:
        return 0.0
    p = np.asarray(probabilities, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = len(p)
    out = 0.0
    for idx in range(bins):
        mask = (p >= edges[idx]) & (p <= edges[idx + 1]) if idx == bins - 1 else (p >= edges[idx]) & (p < edges[idx + 1])
        if np.any(mask):
            out += float(np.sum(mask)) / total * abs(float(np.mean(p[mask])) - float(np.mean(y[mask])))
    return float(out)


def evaluate_exact_line_candidate(
    artifact: MarginDistributionArtifact,
    test_rows: Sequence[MarginTrainingRow],
    evidence_by_event: Mapping[str, ExactSpreadEvidence],
) -> dict[str, Any]:
    """Evaluate the held-out candidate only at provider-bound exact spread lines."""
    records: list[dict[str, Any]] = []
    missing: list[str] = []
    three_way: list[float] = []

    for row in test_rows:
        evidence = evidence_by_event.get(row.event_id)
        if evidence is None:
            missing.append(row.event_id)
            continue
        event_start = _dt(row.event_start_time)
        quote = _dt(evidence.quote_timestamp)
        if event_start is None or quote is None or quote >= event_start:
            raise SpreadChallengerUnavailable(
                "SPREAD_EXACT_LINE_POST_START_EVIDENCE",
                f"exact spread evidence must precede event start: {row.event_id}",
            )
        scored = score_home_spread(artifact, row.features, home_spread=evidence.home_spread)
        adjusted = float(row.margin) + float(evidence.home_spread)
        cover = adjusted > 1e-12
        push = abs(adjusted) <= 1e-12
        loss = adjusted < -1e-12
        observed = np.asarray([1.0 if cover else 0.0, 1.0 if push else 0.0, 1.0 if loss else 0.0])
        predicted = np.asarray([scored["p_cover"], scored["p_push"], scored["p_not_cover"]])
        three_way.append(float(np.sum((predicted - observed) ** 2)))
        records.append({
            "event_id": row.event_id,
            "home_spread": evidence.home_spread,
            "sportsbook": evidence.sportsbook,
            "p_cover": scored["p_cover"],
            "p_push": scored["p_push"],
            "p_not_cover": scored["p_not_cover"],
            "p_cover_given_no_push": scored["p_cover_given_no_push"],
            "observed_cover": cover,
            "observed_push": push,
            "quote_timestamp": evidence.quote_timestamp,
            "provider_event_id": evidence.provider_event_id,
        })

    if not records:
        raise SpreadChallengerUnavailable(
            "SPREAD_EXACT_LINE_EVIDENCE_UNAVAILABLE",
            "no provider-bound pregame exact spread lines overlap the held-out replay rows",
        )

    non_push = [record for record in records if not record["observed_push"]]
    p = np.asarray([float(record["p_cover_given_no_push"]) for record in non_push], dtype=float)
    y = np.asarray([1.0 if record["observed_cover"] else 0.0 for record in non_push], dtype=float)
    clipped = np.clip(p, 1e-9, 1.0 - 1e-9)
    brier = float(np.mean((p - y) ** 2)) if len(p) else None
    log_loss = float(np.mean(-(y * np.log(clipped) + (1.0 - y) * np.log(1.0 - clipped)))) if len(p) else None
    ece = _ece(p.tolist(), y.astype(int).tolist()) if len(p) else None

    return {
        "evaluation_mode": "PROVIDER_BOUND_EXACT_SPREAD",
        "test_row_n": len(test_rows),
        "evidence_row_n": len(records),
        "missing_evidence_n": len(missing),
        "exact_line_coverage": len(records) / len(test_rows) if test_rows else 0.0,
        "push_n": sum(1 for record in records if record["observed_push"]),
        "non_push_n": len(non_push),
        "cover_brier": brier,
        "cover_log_loss": log_loss,
        "cover_ece": ece,
        "three_way_brier": float(np.mean(three_way)),
        "observed_cover_rate_non_push": float(np.mean(y)) if len(y) else None,
        "market_features_used": False,
        "spread_line_used_as_feature": False,
        "market_probability_substitution_used": False,
        "moneyline_probability_used": False,
        "automatic_certification": AUTOMATIC_CERTIFICATION,
        "automatic_promotion": AUTOMATIC_PROMOTION,
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "certification_ready": False,
        "next_required_stage": "FORWARD_SHADOW_AND_GOVERNED_CERTIFICATION",
        "can_execute": CAN_EXECUTE,
    }


__all__ = [
    "AUTOMATIC_CERTIFICATION",
    "AUTOMATIC_PROMOTION",
    "CAN_EXECUTE",
    "ExactSpreadEvidence",
    "PREFERRED_BOOKS",
    "PROBABILITY_PUBLISHABLE",
    "bind_exact_home_spreads",
    "evaluate_exact_line_candidate",
]
