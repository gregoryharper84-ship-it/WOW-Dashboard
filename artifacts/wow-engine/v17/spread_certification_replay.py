"""Governed historical close-proxy replay for spread certification evidence.

The first production-safe source is nflverse's public games.csv schedule/results
asset.  The spread line is used only after the sporting margin artifact is fit;
it is never a model feature or probability source.

This module produces research/certification evidence only.  It cannot certify,
publish, rank, register, or execute a spread selection.
"""
from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Mapping, Sequence

from nfl_event_data_p1 import CapturedAsset, download_asset, schedules_asset
from v17.spread_historical_close_proxy import (
    NFLVERSE_EVIDENCE_CLASS,
    HistoricalCloseProxy,
    evaluate_historical_close_proxy,
    nflverse_close_proxy,
)
from v17.spread_margin_challenger import (
    SpreadChallengerUnavailable,
    train_margin_distribution_candidate,
)
from v17.spread_margin_replay import load_replay_rows

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
GLOBAL_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"


def _dt(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    return parsed if parsed.utcoffset() is not None else parsed.replace(tzinfo=timezone.utc)


def _read_csv(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", errors="strict", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = {str(name or "").strip() for name in (reader.fieldnames or [])}
        required = {"game_id", "home_team", "away_team", "spread_line"}
        missing = sorted(required - columns)
        if missing:
            raise SpreadChallengerUnavailable(
                "SPREAD_NFLVERSE_CLOSE_PROXY_SCHEMA_CHANGED",
                "nflverse games.csv missing required close-proxy columns: " + ",".join(missing),
            )
        return [dict(row) for row in reader]


def bind_nflverse_close_proxies(
    *,
    test_event_ids: Sequence[str],
    source_rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, HistoricalCloseProxy], dict[str, Any]]:
    """Bind one unambiguous nflverse close proxy to each held-out game id."""
    target = {str(value) for value in test_event_ids}
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for raw in source_rows:
        game_id = str(raw.get("game_id") or "").strip()
        if game_id in target:
            grouped.setdefault(game_id, []).append(raw)

    evidence: dict[str, HistoricalCloseProxy] = {}
    blockers: dict[str, str] = {}
    for event_id in sorted(target):
        matches = grouped.get(event_id) or []
        if not matches:
            blockers[event_id] = "SPREAD_NFLVERSE_CLOSE_PROXY_EVENT_MISSING"
            continue
        if len(matches) != 1:
            blockers[event_id] = "SPREAD_NFLVERSE_CLOSE_PROXY_EVENT_AMBIGUOUS"
            continue
        try:
            evidence[event_id] = nflverse_close_proxy(matches[0])
        except SpreadChallengerUnavailable as exc:
            blockers[event_id] = exc.code

    counts = Counter(blockers.values())
    return evidence, {
        "test_event_n": len(target),
        "bound_event_n": len(evidence),
        "coverage": len(evidence) / len(target) if target else 0.0,
        "blocker_counts": dict(sorted(counts.items())),
        "event_identity": "EXACT_NFLVERSE_GAME_ID",
        "source_line_field": "spread_line",
        "source_line_semantics": "POSITIVE_HOME_FAVORITE__V17_HOME_SPREAD_EQUALS_NEGATED_SOURCE_LINE",
        "historical_certification_evidence_only": True,
        "live_card_receipt_eligible": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def _heldout_rows(rows: Sequence[Any], test_n: int) -> list[Any]:
    ordered = sorted(rows, key=lambda row: (_dt(row.event_start_time), row.event_id))
    if test_n <= 0 or test_n >= len(ordered):
        raise SpreadChallengerUnavailable(
            "SPREAD_CERTIFICATION_HOLDOUT_INVALID",
            f"invalid held-out row count {test_n} for {len(ordered)} rows",
        )
    return ordered[-int(test_n):]


def _source_manifest(captured: CapturedAsset) -> dict[str, Any]:
    return {
        "source_provider": "NFLVERSE",
        "dataset": "games.csv",
        "requested_url": captured.requested_url,
        "resolved_url": captured.resolved_url,
        "content_sha256": captured.content_sha256,
        "byte_count": captured.byte_count,
        "row_count": captured.row_count,
        "column_names": list(captured.column_names),
        "fetched_at": captured.fetched_at,
        "etag": captured.etag,
        "last_modified": captured.last_modified,
        "historical_certification_evidence_only": True,
        "live_card_receipt_eligible": False,
        "can_execute": False,
    }


def run_nflverse_close_proxy_replay(
    *,
    client: Any,
    min_rows: int = 300,
    ridge_alpha: float = 4.0,
    download_fn: Any = download_asset,
) -> dict[str, Any]:
    """Fit the existing NFL challenger and evaluate only its untouched holdout."""
    rows = load_replay_rows(client, sport="NFL")
    artifact, synthetic_metrics = train_margin_distribution_candidate(
        rows,
        sport="NFL",
        min_rows=min_rows,
        ridge_alpha=ridge_alpha,
    )
    test_rows = _heldout_rows(rows, artifact.test_rows)

    with TemporaryDirectory(prefix="wow-nflverse-spread-") as directory:
        captured = download_fn(schedules_asset(), directory)
        source_rows = _read_csv(captured.local_path)
        evidence, binding_audit = bind_nflverse_close_proxies(
            test_event_ids=[row.event_id for row in test_rows],
            source_rows=source_rows,
        )
        exact_metrics = evaluate_historical_close_proxy(
            artifact=artifact,
            test_rows=test_rows,
            evidence_by_event=evidence,
            evidence_class=NFLVERSE_EVIDENCE_CLASS,
        )
        source_manifest = _source_manifest(captured)

    return {
        "status": "EXPERIMENT_CREATED",
        "code": "NFL_SPREAD_HISTORICAL_CLOSE_PROXY_REPLAY_COMPLETE",
        "sport": "NFL",
        "model_family": artifact.model_family,
        "training_dataset_hash": artifact.training_dataset_hash,
        "artifact_train_rows": artifact.train_rows,
        "artifact_calibration_rows": artifact.calibration_rows,
        "artifact_test_rows": artifact.test_rows,
        "evidence_class": NFLVERSE_EVIDENCE_CLASS,
        "exact_line_metrics": exact_metrics,
        "binding_audit": binding_audit,
        "source_manifest": source_manifest,
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
    "GLOBAL_TERMINAL_REDUCER",
    "PROBABILITY_PUBLISHABLE",
    "bind_nflverse_close_proxies",
    "run_nflverse_close_proxy_replay",
]
