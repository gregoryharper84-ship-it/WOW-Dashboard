"""Attach sport-specialist Scout team metadata to a V17 Multi-Scout handoff.

This is additive enrichment and research disposition only. It does not score,
calibrate, qualify, approve, or execute any wager.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

try:  # package import under pytest/backend runtime
    from v17.market_evidence_snapshot_bridge import attach_snapshot_evidence
    from v17.news_event_research_handoff import reconcile_news_handoff
    from v17.scout_research_promotion import promote_handoff
    from v17.sport_scout_registry import registry_payload, scout_team_for
    from v17.sport_research_brief import SUPPORTED_RESEARCH_WORKERS, build_research_brief
except ModuleNotFoundError:  # direct `python v17/sport_scout_enrichment.py`
    from market_evidence_snapshot_bridge import attach_snapshot_evidence
    from news_event_research_handoff import reconcile_news_handoff
    from scout_research_promotion import promote_handoff
    from sport_scout_registry import registry_payload, scout_team_for
    from sport_research_brief import SUPPORTED_RESEARCH_WORKERS, build_research_brief


def enrich_candidate(row: dict[str, Any], lane: str) -> dict[str, Any]:
    enriched = dict(row)
    team = scout_team_for(str(row.get("sport_key") or ""))
    enriched["sport_scout_team"] = team.to_handoff()
    enriched["scout_team_id"] = team.team_id
    enriched["edge_classes_available"] = list(team.edge_classes)
    enriched["red_team_checks_required"] = list(team.red_team_checks)
    enriched["research_cycle"] = list(team.research_cycle)
    enriched["research_ceiling"] = "RESEARCH_INTEREST"
    enriched["discovery_status"] = "DISCOVERY_ONLY"
    enriched["probability_authority"] = False
    enriched["controlling_specialist_route"] = team.controlling_prop_route if lane == "PROP" else team.controlling_team_event_route
    enriched["route"] = enriched["controlling_specialist_route"]
    enriched["research_worker_briefs"] = {
        worker_id: build_research_brief(enriched, worker_id)
        for worker_id in SUPPORTED_RESEARCH_WORKERS
    }
    enriched["research_workers_may_create_probability"] = False
    # The optional news artifact is discovery metadata, never fitted-model input.
    # A reconciled research handoff can request (but cannot perform) rescoring.
    if isinstance(row.get("news_discovery_receipt"), dict):
        facts = row.get("news_reviewed_facts")
        enriched["news_research_handoff"] = reconcile_news_handoff(
            row["news_discovery_receipt"],
            {
                "sport_key": row.get("sport_key"),
                "official_event_id": row.get("official_event_id"),
                "event_start_utc": (
                    row.get("event_start_utc")
                    or row.get("commence_time")
                    or (row.get("event_request") or {}).get("event_start_time_utc")
                ),
            },
            facts if isinstance(facts, list) else [],
        )
    enriched["can_execute"] = False
    return enriched


def enrich_handoff(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("can_execute") is not False:
        raise ValueError("SCOUT_HANDOFF_CAN_EXECUTE_MUST_BE_FALSE")
    out = dict(payload)
    handoff = dict(out.get("model_handoff") or {})
    handoff["team_event_candidates"] = [enrich_candidate(row, "TEAM_EVENT") for row in handoff.get("team_event_candidates", []) or [] if isinstance(row, dict)]
    handoff["prop_candidates"] = [enrich_candidate(row, "PROP") for row in handoff.get("prop_candidates", []) or [] if isinstance(row, dict)]
    out["model_handoff"] = handoff
    out["sport_scout_registry"] = registry_payload()
    out["research_worker_registry"] = {
        "worker_ids": list(SUPPORTED_RESEARCH_WORKERS),
        "sport_aware_briefs_attached": True,
        "research_ceiling": "RESEARCH_INTEREST",
        "prediction_authority": False,
        "can_execute": False,
    }
    governance = dict(out.get("governance") or {})
    governance.update({
        "sport_scout_probability_authority": False,
        "research_worker_probability_authority": False,
        "sportsbook_implied_probability_is_model_probability": False,
        "final_probability_requires_controlling_specialist": True,
        "v17_terminal_reducer_is_terminal_authority": True,
        "can_execute": False,
    })
    out["governance"] = governance
    out["can_execute"] = False
    return promote_handoff(out)


def _attach_sibling_snapshot(payload: dict[str, Any], source: Path) -> dict[str, Any]:
    """Consume the workflow's sibling market-evidence artifact when present."""
    snapshot_path = source.parent / "market-evidence.json"
    if not snapshot_path.exists():
        return payload
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    return attach_snapshot_evidence(payload, snapshot)


def _attach_sibling_news(payload: dict[str, Any], source: Path) -> dict[str, Any]:
    """Attach one same-run GDELT discovery receipt, never a verified model feature.

    GDELT is a link discovery service, not an injury/lineup oracle. Reviewed
    event facts must still be independently sourced by the controlling lane.
    This additive bridge only places its metadata in the existing Scout
    candidate handoff; failure never authorizes a score or a wager.
    """
    news_path = source.parent / "public-gdelt-receipt.json"
    if not news_path.exists():
        return payload
    def status(code: str, *, touched: int = 0, digest: str | None = None) -> dict[str, Any]:
        out = dict(payload)
        out["public_news_discovery_bridge"] = {
            "status": code, "candidates_touched": touched,
            "source_sha256": digest, "prediction_authority": False,
            "model_feature_eligible": False, "can_execute": False,
        }
        return out

    try:
        # Public metadata receipt is capped; no third-party article bodies.
        if news_path.stat().st_size > 512_000:
            return status("NEWS_RECEIPT_OVERSIZED")
        raw = news_path.read_bytes()
        discovery = json.loads(raw)
    except (OSError, UnicodeDecodeError, ValueError):
        return status("NEWS_RECEIPT_UNREADABLE")
    if (not isinstance(discovery, dict)
            or discovery.get("schema_version") != "wow.v17.public_news_receipt.v1"
            or discovery.get("can_execute") is not False
            or discovery.get("prediction_authority") is not False
            or discovery.get("source_status") not in ("OK", "BLOCKED")
            or not isinstance(discovery.get("sport_key"), str)
            or not isinstance(discovery.get("articles"), list)
            or len(discovery["articles"]) > 30):
        return status("NEWS_RECEIPT_INVALID")
    digest = hashlib.sha256(raw).hexdigest()
    handoff = payload.get("model_handoff")
    if not isinstance(handoff, dict):
        return status("NEWS_NO_SCOUT_HANDOFF", digest=digest)
    updated_handoff = dict(handoff)
    touched = 0
    for lane in ("team_event_candidates", "prop_candidates"):
        rows = handoff.get(lane)
        if not isinstance(rows, list):
            continue
        updated = []
        for row in rows:
            if not isinstance(row, dict):
                updated.append(row)
                continue
            item = dict(row)
            if (item.get("sport_key") == discovery["sport_key"]
                    and "news_discovery_receipt" not in item):
                item["news_discovery_receipt"] = discovery
                touched += 1
            updated.append(item)
        updated_handoff[lane] = updated
    out = status("NEWS_DISCOVERY_ATTACHED" if touched else "NEWS_NO_MATCHING_SPORT",
                 touched=touched, digest=digest)
    out["model_handoff"] = updated_handoff
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = Path(args.input)
    target = Path(args.output)
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload = _attach_sibling_snapshot(payload, source)
    payload = _attach_sibling_news(payload, source)
    enriched = enrich_handoff(payload)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(enriched, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    bridge = enriched.get("market_evidence_snapshot_bridge", {})
    print(json.dumps({
        "status": "SPORT_SCOUT_ENRICHMENT_COMPLETE",
        "team_event_candidates": len(enriched.get("model_handoff", {}).get("team_event_candidates", [])),
        "prop_candidates": len(enriched.get("model_handoff", {}).get("prop_candidates", [])),
        "research_workers": len(SUPPORTED_RESEARCH_WORKERS),
        "research_status_counts": enriched.get("research_promotion", {}).get("status_counts", {}),
        "snapshot_bridge_status": bridge.get("snapshot_status", "NOT_PRESENT"),
        "snapshot_identity_policy": bridge.get("identity_policy", "NOT_PRESENT"),
        "snapshot_evidence_rows_attached": bridge.get("evidence_rows_attached", 0),
        "snapshot_candidates_touched": bridge.get("candidates_touched", 0),
        "news_bridge_status": enriched.get("public_news_discovery_bridge", {}).get("status", "NOT_PRESENT"),
        "news_candidates_touched": enriched.get("public_news_discovery_bridge", {}).get("candidates_touched", 0),
        "can_execute": False,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
