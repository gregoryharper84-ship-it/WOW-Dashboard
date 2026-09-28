"""ESPN final-summary close-proxy acquisition for V17 spread certification.

This source is used only for historical certification replay.  ESPN final-summary
``pickcenter`` does not expose an immutable quote timestamp here, so every row is
explicitly tagged as a timestamp-unavailable historical close proxy and can never
satisfy live-card exact-line admission or CLV claims.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Callable, Mapping, Sequence

import httpx

from v17.spread_historical_close_proxy import (
    ESPN_WNBA_EVIDENCE_CLASS,
    HistoricalCloseProxy,
    espn_pickcenter_close_proxy,
    evaluate_historical_close_proxy,
)
from v17.spread_margin_challenger import SpreadChallengerUnavailable, train_margin_distribution_candidate
from v17.spread_margin_replay import load_replay_rows

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
GLOBAL_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"
ESPN_WNBA_SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/summary"
PAGE_SIZE = 1000


def _dt(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    return parsed if parsed.utcoffset() is not None else parsed.replace(tzinfo=timezone.utc)


def _espn_id(value: Any) -> str:
    text = str(value or "").strip()
    return text.split("-", 1)[1] if text.lower().startswith("espn-") else text


def _completed_competition(summary: Mapping[str, Any]) -> Mapping[str, Any]:
    header = summary.get("header")
    if not isinstance(header, Mapping):
        raise SpreadChallengerUnavailable("SPREAD_ESPN_SUMMARY_HEADER_MISSING", "ESPN summary header is unavailable")
    competitions = header.get("competitions")
    if not isinstance(competitions, list) or len(competitions) != 1 or not isinstance(competitions[0], Mapping):
        raise SpreadChallengerUnavailable("SPREAD_ESPN_SUMMARY_COMPETITION_AMBIGUOUS", "ESPN summary must contain one exact competition")
    competition = competitions[0]
    status_type = ((competition.get("status") or {}).get("type") or {}) if isinstance(competition.get("status"), Mapping) else {}
    completed = status_type.get("completed") is True or str(status_type.get("state") or "").lower() == "post"
    if not completed:
        raise SpreadChallengerUnavailable("SPREAD_ESPN_SUMMARY_NOT_FINAL", "historical close proxy requires a final ESPN event")
    return competition


def extract_espn_team_abbreviations(
    *,
    summary: Mapping[str, Any],
    expected_event_id: str,
    expected_home_team_id: str,
    expected_away_team_id: str,
) -> tuple[str, str]:
    """Prove exact ESPN event/home/away identity and return abbreviations."""
    competition = _completed_competition(summary)
    expected_event = _espn_id(expected_event_id)
    header = summary.get("header") or {}
    observed_event = str(header.get("id") or competition.get("id") or "").strip()
    if observed_event and observed_event != expected_event:
        raise SpreadChallengerUnavailable("SPREAD_ESPN_SUMMARY_EVENT_ID_MISMATCH", "ESPN summary event identity mismatch")

    expected = {
        "home": _espn_id(expected_home_team_id),
        "away": _espn_id(expected_away_team_id),
    }
    found: dict[str, str] = {}
    competitors = competition.get("competitors")
    if not isinstance(competitors, list):
        raise SpreadChallengerUnavailable("SPREAD_ESPN_SUMMARY_TEAMS_MISSING", "ESPN summary competitors are unavailable")
    for competitor in competitors:
        if not isinstance(competitor, Mapping):
            continue
        side = str(competitor.get("homeAway") or "").lower()
        if side not in {"home", "away"}:
            continue
        team = competitor.get("team") or {}
        if not isinstance(team, Mapping):
            continue
        team_id = str(team.get("id") or competitor.get("id") or "").strip()
        abbreviation = str(team.get("abbreviation") or "").strip().upper()
        if team_id != expected[side] or not abbreviation:
            continue
        found[side] = abbreviation
    if set(found) != {"home", "away"}:
        raise SpreadChallengerUnavailable("SPREAD_ESPN_SUMMARY_TEAM_IDENTITY_MISMATCH", "ESPN summary does not match expected home/away team ids")
    return found["home"], found["away"]


def close_proxy_from_wnba_summary(
    *,
    summary: Mapping[str, Any],
    event_id: str,
    home_team_id: str,
    away_team_id: str,
) -> HistoricalCloseProxy:
    home_abbr, away_abbr = extract_espn_team_abbreviations(
        summary=summary,
        expected_event_id=event_id,
        expected_home_team_id=home_team_id,
        expected_away_team_id=away_team_id,
    )
    return espn_pickcenter_close_proxy(
        summary=summary,
        sport="WNBA",
        event_id=event_id,
        home_team_abbreviation=home_abbr,
        away_team_abbreviation=away_abbr,
    )


def _load_wnba_identities(client: Any) -> dict[str, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        batch = (
            client.table("wow_wnba_training_games")
            .select("game_id,game_date,home_team_id,away_team_id,settled")
            .eq("settled", True)
            .order("game_id")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute().data
            or []
        )
        rows.extend(dict(row) for row in batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return {str(row["game_id"]): row for row in rows if row.get("game_id")}


def _default_summary_loader(http: httpx.Client, provider_event_id: str) -> Mapping[str, Any]:
    response = http.get(ESPN_WNBA_SUMMARY_URL, params={"event": provider_event_id})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise SpreadChallengerUnavailable("SPREAD_ESPN_SUMMARY_PAYLOAD_INVALID", "ESPN summary response is not an object")
    return payload


def acquire_wnba_close_proxies(
    *,
    identities: Mapping[str, Mapping[str, Any]],
    test_event_ids: Sequence[str],
    summary_loader: Callable[[str], Mapping[str, Any]],
) -> tuple[dict[str, HistoricalCloseProxy], dict[str, Any]]:
    evidence: dict[str, HistoricalCloseProxy] = {}
    blockers: dict[str, str] = {}
    source_hashes: list[str] = []
    for event_id in sorted({str(value) for value in test_event_ids}):
        identity = identities.get(event_id)
        if not identity:
            blockers[event_id] = "SPREAD_WNBA_CLOSE_PROXY_EVENT_IDENTITY_MISSING"
            continue
        try:
            summary = summary_loader(_espn_id(event_id))
            close = close_proxy_from_wnba_summary(
                summary=summary,
                event_id=event_id,
                home_team_id=str(identity.get("home_team_id") or ""),
                away_team_id=str(identity.get("away_team_id") or ""),
            )
        except SpreadChallengerUnavailable as exc:
            blockers[event_id] = exc.code
            continue
        except Exception as exc:  # noqa: BLE001
            blockers[event_id] = f"SPREAD_ESPN_SUMMARY_ACQUISITION_FAILED:{type(exc).__name__}"
            continue
        evidence[event_id] = close
        source_hashes.append(f"{event_id}:{close.source_payload_sha256}")

    counts = Counter(blockers.values())
    aggregate_hash = sha256("\n".join(sorted(source_hashes)).encode()).hexdigest() if source_hashes else None
    return evidence, {
        "test_event_n": len(set(test_event_ids)),
        "bound_event_n": len(evidence),
        "coverage": len(evidence) / len(set(test_event_ids)) if test_event_ids else 0.0,
        "blocker_counts": dict(sorted(counts.items())),
        "aggregate_source_payload_sha256": aggregate_hash,
        "event_identity": "EXACT_PERSISTED_ESPN_GAME_AND_TEAM_IDS",
        "source_provider": "ESPN_FINAL_SUMMARY_PICKCENTER",
        "source_quote_timestamp_state": "HISTORICAL_FINAL_SUMMARY_CLOSE_PROXY_TIMESTAMP_UNAVAILABLE",
        "historical_certification_evidence_only": True,
        "live_card_receipt_eligible": False,
        "clv_evidence": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def _heldout_rows(rows: Sequence[Any], test_n: int) -> list[Any]:
    ordered = sorted(rows, key=lambda row: (_dt(row.event_start_time), row.event_id))
    if test_n <= 0 or test_n >= len(ordered):
        raise SpreadChallengerUnavailable("SPREAD_CERTIFICATION_HOLDOUT_INVALID", "invalid WNBA held-out row count")
    return ordered[-int(test_n):]


def run_wnba_espn_close_proxy_replay(
    *,
    client: Any,
    min_rows: int = 300,
    ridge_alpha: float = 4.0,
    timeout_seconds: float = 12.0,
) -> dict[str, Any]:
    rows = load_replay_rows(client, sport="WNBA")
    artifact, synthetic_metrics = train_margin_distribution_candidate(
        rows,
        sport="WNBA",
        min_rows=min_rows,
        ridge_alpha=ridge_alpha,
    )
    test_rows = _heldout_rows(rows, artifact.test_rows)
    identities = _load_wnba_identities(client)
    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as http:
        evidence, binding_audit = acquire_wnba_close_proxies(
            identities=identities,
            test_event_ids=[row.event_id for row in test_rows],
            summary_loader=lambda provider_event_id: _default_summary_loader(http, provider_event_id),
        )
    exact_metrics = evaluate_historical_close_proxy(
        artifact=artifact,
        test_rows=test_rows,
        evidence_by_event=evidence,
        evidence_class=ESPN_WNBA_EVIDENCE_CLASS,
    )
    return {
        "status": "EXPERIMENT_CREATED",
        "code": "WNBA_SPREAD_HISTORICAL_CLOSE_PROXY_REPLAY_COMPLETE",
        "sport": "WNBA",
        "model_family": artifact.model_family,
        "training_dataset_hash": artifact.training_dataset_hash,
        "artifact_train_rows": artifact.train_rows,
        "artifact_calibration_rows": artifact.calibration_rows,
        "artifact_test_rows": artifact.test_rows,
        "evidence_class": ESPN_WNBA_EVIDENCE_CLASS,
        "exact_line_metrics": exact_metrics,
        "binding_audit": binding_audit,
        "synthetic_grid_diagnostic": synthetic_metrics,
        "market_features_used": False,
        "spread_line_used_as_feature": False,
        "market_probability_substitution_used": False,
        "moneyline_probability_used": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "global_terminal_reducer": GLOBAL_TERMINAL_REDUCER,
        "can_execute": False,
    }


__all__ = [
    "AUTOMATIC_CERTIFICATION",
    "AUTOMATIC_PROMOTION",
    "CAN_EXECUTE",
    "ESPN_WNBA_SUMMARY_URL",
    "GLOBAL_TERMINAL_REDUCER",
    "PROBABILITY_PUBLISHABLE",
    "acquire_wnba_close_proxies",
    "close_proxy_from_wnba_summary",
    "extract_espn_team_abbreviations",
    "run_wnba_espn_close_proxy_replay",
]
