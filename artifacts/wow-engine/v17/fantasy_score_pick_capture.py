"""Freeze Fantasy Score candidate evidence before production-artifact preflight.

The ordinary /score-pick-request path intentionally requires a certified
production artifact before acquisition. Fantasy Score has a separate governed
research lifecycle, so that ordering previously prevented its candidate lane
from ever collecting the immutable pregame snapshots required for calibration.

This helper narrowly freezes declared MLB Pitcher Fantasy Score rows when no
certified production artifact exists. It does not score, calibrate, rank,
publish, promote, or execute. Production routes keep their existing preflight.
"""
from __future__ import annotations

from typing import Any, Callable

import pick_request_runtime_core as core
from prop_auto_hydration import PropAutoHydrationError

MLB_PITCHER_FANTASY_SCORE = "PITCHER_FANTASY_SCORE"


def _candidate_row(row: Any, market_api: Any) -> bool:
    sport = str(getattr(row, "sport", "") or "").strip().upper()
    stat = core._canonical_stat(sport, str(getattr(row, "stat_type", "") or ""))
    if (sport, stat) != ("MLB", MLB_PITCHER_FANTASY_SCORE):
        return False
    resolver = getattr(market_api, "_prop_route_artifact", None)
    if callable(resolver):
        try:
            certified = resolver(sport, stat)
        except Exception:
            certified = None
        if isinstance(certified, dict) and certified.get("ok") is True:
            return False
    return True


def capture_fantasy_score_candidate_evidence(
    batch: Any,
    *,
    market_api: Any,
    hydrate: Callable[..., dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Freeze exact candidate snapshots and return row-isolated receipts."""
    receipts: dict[str, dict[str, Any]] = {}
    rows = list(getattr(batch, "rows", []) or [])
    for index, original_row in enumerate(rows):
        row_key = str(getattr(original_row, "row_key", None) or f"row-{index + 1}")
        if not _candidate_row(original_row, market_api):
            continue
        row = original_row
        acquisition: dict[str, Any]
        try:
            if getattr(row, "evidence", None) is None:
                raw = hydrate(
                    sport="MLB",
                    player=row.player,
                    stat_type=MLB_PITCHER_FANTASY_SCORE,
                    event_start_time=row.event_start_time,
                    source_capture_timestamp=row.source_capture_timestamp,
                    source_label=f"{row.source_type}:{row.platform or 'UNKNOWN'}",
                    opponent=getattr(row, "opponent", None),
                )
                row = row.model_copy(update={"evidence": core.RawPropEvidence.model_validate(raw)})
                acquisition = {
                    "mode": "AUTO_HYDRATION",
                    "status": "PASS",
                    "provider": "MLB_STATS_API_OFFICIAL_V1",
                    "source_type": row.source_type,
                    "platform": row.platform,
                    "research_only": True,
                    "can_execute": False,
                }
            else:
                acquisition = {
                    "mode": "CALLER_SUPPLIED_RAW_EVIDENCE",
                    "status": "PASS_PENDING_VALIDATION",
                    "source_type": row.source_type,
                    "platform": row.platform,
                    "research_only": True,
                    "can_execute": False,
                }

            normalized = core._validate_evidence(row, MLB_PITCHER_FANTASY_SCORE)
            acquisition["status"] = "PASS"
            snapshot_id, fingerprint, snapshot = core._snapshot_payload(row, normalized)
            market_api.prod.get_client().table("wow_prop_evidence_snapshots").upsert(
                snapshot,
                on_conflict="source_snapshot_id",
            ).execute()
            acquisition["snapshot_status"] = "FROZEN"
            acquisition["source_snapshot_id"] = snapshot_id
            receipts[row_key] = {
                "row_key": row_key,
                "status": "CAPTURED",
                "code": "FANTASY_SCORE_CANDIDATE_EVIDENCE_FROZEN",
                "source_snapshot_id": snapshot_id,
                "evidence_fingerprint": fingerprint,
                "acquisition": acquisition,
                "probability_publishable": False,
                "rank_eligible": False,
                "can_execute": False,
            }
        except PropAutoHydrationError as exc:
            receipts[row_key] = {
                "row_key": row_key,
                "status": "HELD",
                "code": exc.code,
                "detail": {**exc.detail, "message": str(exc)},
                "probability_publishable": False,
                "rank_eligible": False,
                "can_execute": False,
            }
        except ValueError as exc:
            receipts[row_key] = {
                "row_key": row_key,
                "status": "HELD",
                "code": "MODEL_INPUTS_INSUFFICIENT",
                "detail": {"blocker": str(exc)},
                "probability_publishable": False,
                "rank_eligible": False,
                "can_execute": False,
            }
        except Exception as exc:
            receipts[row_key] = {
                "row_key": row_key,
                "status": "HELD",
                "code": "FANTASY_SCORE_EVIDENCE_PERSISTENCE_UNAVAILABLE",
                "detail": {"error_type": type(exc).__name__},
                "probability_publishable": False,
                "rank_eligible": False,
                "can_execute": False,
            }
    return receipts


__all__ = ["MLB_PITCHER_FANTASY_SCORE", "capture_fantasy_score_candidate_evidence"]
