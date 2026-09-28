"""Historical exact-line close proxies for V17 spread/run-line certification replay.

These adapters are deliberately separate from live exact-line receipts.  A
historical close proxy can be used only to evaluate an already-fitted sporting
margin/run distribution on a held-out event.  It cannot enter a live card,
claim an immutable quote timestamp, establish CLV, or create probability
publication authority.

Supported evidence families in this module:
* NFLVERSE_HISTORICAL_CLOSE_PROXY -- nflverse games ``spread_line``.
* ESPN_HISTORICAL_CLOSE_PROXY -- ESPN WNBA final-summary ``pickcenter.details``.
* ESPN_HISTORICAL_RUN_LINE_CLOSE_PROXY -- ESPN MLB final-summary
  ``pickcenter.details`` when a canonical event crosswalk has already been
  independently established by the caller.

No network calls live here.  Callers must freeze/hash the downloaded source
artifact or raw ESPN summary before passing rows to these pure adapters.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from math import isfinite
import re
from typing import Any, Mapping, Sequence

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

NFLVERSE_EVIDENCE_CLASS = "NFLVERSE_HISTORICAL_CLOSE_PROXY"
ESPN_WNBA_EVIDENCE_CLASS = "ESPN_HISTORICAL_CLOSE_PROXY"
ESPN_MLB_EVIDENCE_CLASS = "ESPN_HISTORICAL_RUN_LINE_CLOSE_PROXY"
QUOTE_TIMESTAMP_STATE = "HISTORICAL_FINAL_SUMMARY_CLOSE_PROXY_TIMESTAMP_UNAVAILABLE"


@dataclass(frozen=True)
class HistoricalCloseProxy:
    event_id: str
    sport: str
    home_team: str
    away_team: str
    home_spread: float
    evidence_class: str
    source_provider: str
    source_record_id: str
    source_payload_sha256: str
    source_line_semantics: str
    source_quote_timestamp_state: str = QUOTE_TIMESTAMP_STATE

    def payload(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "historical_certification_evidence_only": True,
            "live_card_receipt_eligible": False,
            "clv_evidence": False,
            "prediction_authority": False,
            "market_features_used": False,
            "spread_line_used_as_feature": False,
            "market_probability_substitution_used": False,
            "moneyline_probability_used": False,
            "probability_publishable": False,
            "rank_eligible": False,
            "automatic_certification": False,
            "automatic_promotion": False,
            "can_execute": False,
        }


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _payload_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), default=str).encode()
    return sha256(encoded).hexdigest()


def _token(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").strip().upper())


def nflverse_close_proxy(row: Mapping[str, Any]) -> HistoricalCloseProxy:
    """Normalize one nflverse game row into V17 signed-home-spread semantics.

    nflverse documents ``spread_line`` as positive when the HOME team is
    favored and negative when the AWAY team is favored.  V17 uses sportsbook
    settlement notation for the home side, where a home favorite is negative.
    Therefore the governed conversion is exactly ``home_spread=-spread_line``.
    """
    event_id = str(row.get("game_id") or "").strip()
    home = str(row.get("home_team") or "").strip().upper()
    away = str(row.get("away_team") or "").strip().upper()
    spread_line = _finite(row.get("spread_line"))
    if not event_id or not home or not away or spread_line is None:
        raise SpreadChallengerUnavailable(
            "SPREAD_HISTORICAL_PROXY_ROW_INCOMPLETE",
            "nflverse row requires game_id, home_team, away_team, and spread_line",
        )
    return HistoricalCloseProxy(
        event_id=event_id,
        sport="NFL",
        home_team=home,
        away_team=away,
        home_spread=-float(spread_line),
        evidence_class=NFLVERSE_EVIDENCE_CLASS,
        source_provider="NFLVERSE",
        source_record_id=event_id,
        source_payload_sha256=_payload_hash(row),
        source_line_semantics=(
            "NFLVERSE_SPREAD_LINE_POSITIVE_HOME_FAVORITE_CONVERTED_TO_"
            "V17_SIGNED_HOME_SPREAD_BY_NEGATION"
        ),
    )


def _pickcenter_entries(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    direct = summary.get("pickcenter")
    if isinstance(direct, list):
        return [dict(row) for row in direct if isinstance(row, Mapping)]
    if isinstance(direct, Mapping):
        return [dict(direct)]
    # Some ESPN summary payloads place pickcenter under a top-level predictor or
    # gameInfo-like container.  Accept only an explicit ``pickcenter`` key;
    # never infer a line from unrelated odds fields.
    for container_key in ("gameInfo", "gameinfo", "odds"):
        container = summary.get(container_key)
        if isinstance(container, Mapping):
            value = container.get("pickcenter")
            if isinstance(value, list):
                return [dict(row) for row in value if isinstance(row, Mapping)]
            if isinstance(value, Mapping):
                return [dict(value)]
    return []


def _details_team_line(details: Any) -> tuple[str, float] | None:
    text = " ".join(str(details or "").strip().upper().split())
    if not text:
        return None
    # Fail closed unless the detail is exactly a team token followed by a
    # conventional signed/unsigned half- or whole-point handicap.
    match = re.fullmatch(r"([A-Z0-9.]+)\s+([+-]?\d+(?:\.\d+)?)", text)
    if not match:
        return None
    line = _finite(match.group(2))
    if line is None or abs(line) >= 50:
        return None
    return _token(match.group(1)), float(line)


def espn_pickcenter_close_proxy(
    *,
    summary: Mapping[str, Any],
    sport: str,
    event_id: str,
    home_team_abbreviation: str,
    away_team_abbreviation: str,
) -> HistoricalCloseProxy:
    """Parse ESPN final-summary pickcenter details without inventing sign rules.

    The team named in ``details`` owns the displayed handicap.  If that team is
    away, the home-side settlement line is the exact arithmetic opposite.  The
    generic numeric ``spread`` field is intentionally not used because provider
    sign semantics are not assumed here.
    """
    normalized_sport = str(sport or "").upper().strip()
    if normalized_sport not in {"WNBA", "MLB"}:
        raise SpreadChallengerUnavailable(
            "SPREAD_HISTORICAL_PROXY_SPORT_UNSUPPORTED",
            f"ESPN pickcenter close proxy unsupported for {normalized_sport or 'UNKNOWN'}",
        )
    canonical_event = str(event_id or "").strip()
    home = _token(home_team_abbreviation)
    away = _token(away_team_abbreviation)
    if not canonical_event or not home or not away or home == away:
        raise SpreadChallengerUnavailable(
            "SPREAD_HISTORICAL_PROXY_EVENT_IDENTITY_INCOMPLETE",
            "exact event plus distinct home/away abbreviations are required",
        )

    parsed: list[tuple[dict[str, Any], float]] = []
    for entry in _pickcenter_entries(summary):
        detail = _details_team_line(entry.get("details"))
        if detail is None:
            continue
        named_team, displayed_line = detail
        if named_team == home:
            home_spread = displayed_line
        elif named_team == away:
            home_spread = -displayed_line
        else:
            continue
        parsed.append((entry, float(home_spread)))

    unique_lines = sorted({round(line, 12) for _entry, line in parsed})
    if len(unique_lines) != 1:
        code = "SPREAD_HISTORICAL_PROXY_LINE_UNAVAILABLE" if not unique_lines else "SPREAD_HISTORICAL_PROXY_LINE_AMBIGUOUS"
        raise SpreadChallengerUnavailable(code, f"ESPN pickcenter yielded {len(unique_lines)} exact home lines")

    chosen_line = float(unique_lines[0])
    source_record_id = str(summary.get("id") or summary.get("gameId") or canonical_event)
    evidence_class = ESPN_WNBA_EVIDENCE_CLASS if normalized_sport == "WNBA" else ESPN_MLB_EVIDENCE_CLASS
    return HistoricalCloseProxy(
        event_id=canonical_event,
        sport=normalized_sport,
        home_team=home,
        away_team=away,
        home_spread=chosen_line,
        evidence_class=evidence_class,
        source_provider="ESPN_FINAL_SUMMARY_PICKCENTER",
        source_record_id=source_record_id,
        source_payload_sha256=_payload_hash(summary),
        source_line_semantics="TEAM_NAMED_IN_PICKCENTER_DETAILS_OWNS_DISPLAYED_HANDICAP",
    )


def _ece(probabilities: Sequence[float], outcomes: Sequence[int], bins: int = 10) -> float | None:
    if not probabilities:
        return None
    p = np.asarray(probabilities, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = len(p)
    value = 0.0
    for idx in range(bins):
        mask = (p >= edges[idx]) & (p <= edges[idx + 1]) if idx == bins - 1 else (p >= edges[idx]) & (p < edges[idx + 1])
        if np.any(mask):
            value += float(np.sum(mask)) / total * abs(float(np.mean(p[mask])) - float(np.mean(y[mask])))
    return float(value)


def _binary_metrics(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    non_push = [row for row in records if not row["observed_push"]]
    if not non_push:
        return {"n": 0, "cover_brier": None, "cover_log_loss": None, "cover_ece": None, "observed_cover_rate": None}
    p = np.asarray([float(row["p_cover_given_no_push"]) for row in non_push], dtype=float)
    y = np.asarray([1.0 if row["observed_cover"] else 0.0 for row in non_push], dtype=float)
    clipped = np.clip(p, 1e-9, 1.0 - 1e-9)
    return {
        "n": len(non_push),
        "cover_brier": float(np.mean((p - y) ** 2)),
        "cover_log_loss": float(np.mean(-(y * np.log(clipped) + (1.0 - y) * np.log(1.0 - clipped)))),
        "cover_ece": _ece(p.tolist(), y.astype(int).tolist()),
        "observed_cover_rate": float(np.mean(y)),
    }


def evaluate_historical_close_proxy(
    *,
    artifact: MarginDistributionArtifact,
    test_rows: Sequence[MarginTrainingRow],
    evidence_by_event: Mapping[str, HistoricalCloseProxy],
    evidence_class: str,
) -> dict[str, Any]:
    """Evaluate held-out sporting rows at frozen historical close-proxy lines."""
    records: list[dict[str, Any]] = []
    three_way: list[float] = []
    for row in test_rows:
        evidence = evidence_by_event.get(row.event_id)
        if evidence is None:
            continue
        if evidence.event_id != row.event_id or evidence.evidence_class != evidence_class:
            raise SpreadChallengerUnavailable(
                "SPREAD_HISTORICAL_PROXY_IDENTITY_MISMATCH",
                f"proxy identity/class mismatch for {row.event_id}",
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
            "p_cover": scored["p_cover"],
            "p_push": scored["p_push"],
            "p_not_cover": scored["p_not_cover"],
            "p_cover_given_no_push": scored["p_cover_given_no_push"],
            "observed_cover": cover,
            "observed_push": push,
            "home_favorite": evidence.home_spread < 0,
            "absolute_line": abs(evidence.home_spread),
        })
    if not records:
        raise SpreadChallengerUnavailable(
            "SPREAD_HISTORICAL_PROXY_EVIDENCE_UNAVAILABLE",
            "no historical close-proxy rows overlap held-out sporting rows",
        )

    overall = _binary_metrics(records)
    cohorts: dict[str, Any] = {}
    cohort_specs = {
        "home_favorite": [row for row in records if row["home_favorite"]],
        "home_underdog_or_pickem": [row for row in records if not row["home_favorite"]],
        "line_0_to_3": [row for row in records if row["absolute_line"] <= 3.0],
        "line_3_5_to_6_5": [row for row in records if 3.0 < row["absolute_line"] <= 6.5],
        "line_7_plus": [row for row in records if row["absolute_line"] >= 7.0],
    }
    for name, subset in cohort_specs.items():
        cohorts[name] = _binary_metrics(subset)

    return {
        "evaluation_mode": evidence_class,
        "evidence_class": evidence_class,
        "test_row_n": len(test_rows),
        "evidence_row_n": len(records),
        "missing_evidence_n": len(test_rows) - len(records),
        "exact_line_coverage": len(records) / len(test_rows) if test_rows else 0.0,
        "push_n": sum(1 for row in records if row["observed_push"]),
        "non_push_n": overall["n"],
        "cover_brier": overall["cover_brier"],
        "cover_log_loss": overall["cover_log_loss"],
        "cover_ece": overall["cover_ece"],
        "three_way_brier": float(np.mean(three_way)),
        "observed_cover_rate_non_push": overall["observed_cover_rate"],
        "cohorts": cohorts,
        "historical_certification_evidence_only": True,
        "live_card_receipt_eligible": False,
        "clv_evidence": False,
        "market_features_used": False,
        "spread_line_used_as_feature": False,
        "market_probability_substitution_used": False,
        "moneyline_probability_used": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


__all__ = [
    "AUTOMATIC_CERTIFICATION",
    "AUTOMATIC_PROMOTION",
    "CAN_EXECUTE",
    "ESPN_MLB_EVIDENCE_CLASS",
    "ESPN_WNBA_EVIDENCE_CLASS",
    "HistoricalCloseProxy",
    "NFLVERSE_EVIDENCE_CLASS",
    "PROBABILITY_PUBLISHABLE",
    "QUOTE_TIMESTAMP_STATE",
    "espn_pickcenter_close_proxy",
    "evaluate_historical_close_proxy",
    "nflverse_close_proxy",
]
