"""Attach sport-specialist Scout team metadata to a V17 Multi-Scout handoff.

This is additive enrichment and research disposition only. It does not score,
calibrate, qualify, approve, or execute any wager.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # package import under pytest/backend runtime
    from v17.market_evidence_snapshot_bridge import attach_snapshot_evidence
    from v17.matchup_discovery import discover_matchup
    from v17.context_change_impact import ALL_MARKET_FAMILIES, ALL_POSITION_SCOPE, assess_context_change_impact, candidate_event_start
    from v17.news_event_research_handoff import reconcile_news_handoff
    from v17.news_fitted_rescore_dispatch import dispatch_news_rescores_for_handoff
    from v17.news_reviewed_fact_producer import attach_reviewed_facts_bundle
    from v17.scout_research_promotion import promote_handoff
    from v17.sport_scout_registry import registry_payload, scout_team_for
    from v17.sport_research_brief import SUPPORTED_RESEARCH_WORKERS, build_research_brief
except ModuleNotFoundError:  # direct `python v17/sport_scout_enrichment.py`
    from market_evidence_snapshot_bridge import attach_snapshot_evidence
    from matchup_discovery import discover_matchup
    from context_change_impact import ALL_MARKET_FAMILIES, ALL_POSITION_SCOPE, assess_context_change_impact, candidate_event_start
    from news_event_research_handoff import reconcile_news_handoff
    from news_fitted_rescore_dispatch import dispatch_news_rescores_for_handoff
    from news_reviewed_fact_producer import attach_reviewed_facts_bundle
    from scout_research_promotion import promote_handoff
    from sport_scout_registry import registry_payload, scout_team_for
    from sport_research_brief import SUPPORTED_RESEARCH_WORKERS, build_research_brief


def enrich_candidate(row: dict[str, Any], lane: str, *, public_news: dict[str, Any] | None = None,
                     public_news_sha256: str | None = None) -> dict[str, Any]:
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
    # Mandatory per-event review request: report what was actually screened,
    # rather than treating a game-script hypothesis as completed research.
    enriched["matchup_discovery"] = discover_matchup(enriched)
    enriched["news_intelligence_status"] = "NEWS_NOT_OBSERVED"
    enriched["news_latest_verified_at_utc"] = None
    enriched["news_model_rescore_verified"] = False
    enriched["news_research_rescore_requested"] = False
    # The optional news artifact is discovery metadata, never fitted-model input.
    # A reconciled research handoff can request (but cannot perform) rescoring.
    discovery = row.get("news_discovery_receipt") if isinstance(row.get("news_discovery_receipt"), dict) else public_news
    if isinstance(discovery, dict):
        facts = row.get("news_reviewed_facts")
        enriched["news_research_handoff"] = reconcile_news_handoff(
            discovery,
            {
                "sport_key": row.get("sport_key"),
                "official_event_id": row.get("official_event_id"),
                "provider_event_id": row.get("provider_event_id"),
                "provider_event_source": row.get("provider_event_source"),
                "official_event_identity_verified": row.get("official_event_identity_verified"),
                "canonical_identity_status": row.get("canonical_identity_status"),
                "event_start_utc": candidate_event_start(row),
            },
            facts if isinstance(facts, list) else [],
            source_receipt_sha256=(
                row.get("news_discovery_sha256")
                if isinstance(row.get("news_discovery_receipt"), dict)
                else public_news_sha256
            ),
        )
        enriched["news_intelligence_status"] = enriched["news_research_handoff"]["research_status"]
        enriched["news_research_rescore_requested"] = enriched["news_research_handoff"]["research_rescore_requested"]
        facts = enriched.get("news_reviewed_facts")
        if isinstance(facts, list) and facts:
            latest = None
            for fact in facts:
                if not isinstance(fact, dict):
                    continue
                stamp = fact.get("independently_reviewed_at_utc")
                if isinstance(stamp, str) and (latest is None or stamp > latest):
                    latest = stamp
            enriched["news_latest_verified_at_utc"] = latest
    # Read-only, event-wide fail-closed handoff. This does not rescore or
    # change the controlling specialist / V17 terminal authority.
    enriched["context_change_impact"] = assess_context_change_impact(
        enriched, enriched.get("news_research_handoff")
    )
    if enriched["context_change_impact"]["research_invalidation_requested"]:
        enriched["research_recommendation_status"] = (
            enriched["context_change_impact"]["impact_status"]
        )
    enriched["can_execute"] = False
    return enriched


def _verified_event_key(row: dict[str, Any]) -> tuple[str, str, str] | None:
    """Exactly bind peers by sport, event ID and timezone-aware start time."""
    sport = str(row.get("sport_key") or row.get("sport") or "").strip()
    event_id = str(row.get("official_event_id") or "").strip()
    raw = candidate_event_start(row)
    if not sport or not event_id or not isinstance(raw, str):
        return None
    try:
        at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if at.utcoffset() is None:
            return None
        return (sport, event_id, at.astimezone(timezone.utc).isoformat())
    except (ValueError, TypeError, OverflowError):
        return None


def _propagate_peer_context_holds(handoff: dict[str, Any]) -> int:
    """Conservatively quarantine every peer role/market for the same event.

    Material research evidence on one starter/bench/specialist must not leave
    same-event opponent/team/player candidates appearing unaffected. This is a
    research publication HOLD only. News assertions cannot validate model use.
    """
    lanes = ("team_event_candidates", "prop_candidates")
    affected: dict[tuple[str, str, str], dict[str, Any]] = {}
    for lane in lanes:
        for row in handoff.get(lane) or []:
            key = _verified_event_key(row)
            impact = row.get("context_change_impact")
            if (key is None or not isinstance(impact, dict)
                    or impact.get("impact_status") not in {
                        "CONTEXT_STALE_RECALC_REQUIRED",
                        "CONTEXT_UNRESOLVED_REVIEW_REQUIRED",
                    } or impact.get("research_invalidation_requested") is not True):
                continue
            evidence = affected.setdefault(key, {"sources": set(), "subjects": set()})
            receipt = impact.get("trigger_research_receipt_sha256")
            if isinstance(receipt, str) and len(receipt) == 64:
                evidence["sources"].add(receipt)
            for subject in impact.get("source_subject_ids") or []:
                if isinstance(subject, str) and subject:
                    evidence["subjects"].add(subject)
    propagated = 0
    for lane in lanes:
        updated = []
        for row in handoff.get(lane) or []:
            key = _verified_event_key(row)
            peer = affected.get(key) if key is not None else None
            impact = row.get("context_change_impact")
            if (peer is None or not isinstance(impact, dict)
                    or impact.get("research_invalidation_requested") is True):
                updated.append(row)
                continue
            clone = dict(row)
            peer_impact = dict(impact)
            peer_impact.update({
                "impact_status": "CONTEXT_STALE_RECALC_REQUIRED",
                "research_invalidation_requested": True,
                "research_rescore_requested": False,
                "fitted_specialist_rescore_verified": False,
                "terminal_invalidation_enforced_by_this_module": False,
                "model_probability_created": False,
                "prediction_authority": False,
                "position_scope": ALL_POSITION_SCOPE,
                "no_position_exclusions": True,
                "market_scope_policy": "EVENT_WIDE_CONSERVATIVE_FAIL_CLOSED",
                "affected_market_families": list(ALL_MARKET_FAMILIES),
                "source_subject_ids": sorted(peer["subjects"]),
                "event_peer_research_receipt_sha256": sorted(peer["sources"]),
                "propagated_from_event_peer": True,
                "can_execute": False,
            })
            clone["context_change_impact"] = peer_impact
            clone["research_recommendation_status"] = "CONTEXT_STALE_RECALC_REQUIRED"
            clone["can_execute"] = False
            updated.append(clone)
            propagated += 1
        handoff[lane] = updated
    return propagated


def enrich_handoff(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("can_execute") is not False:
        raise ValueError("SCOUT_HANDOFF_CAN_EXECUTE_MUST_BE_FALSE")
    out = dict(payload)
    handoff = dict(out.get("model_handoff") or {})
    common_news = out.get("public_news_discovery_receipt")
    news_bridge = out.get("public_news_discovery_bridge")
    digest = news_bridge.get("source_sha256") if isinstance(news_bridge, dict) else None
    def enrich(row: dict[str, Any], lane: str) -> dict[str, Any]:
        # Top-level source is stored exactly once, referenced by digest per event.
        receipt = (common_news if isinstance(common_news, dict) and isinstance(digest, str)
                   and len(digest) == 64 and row.get("public_news_discovery_sha256") == digest
                   and row.get("sport_key") == common_news.get("sport_key") else None)
        return enrich_candidate(
            row, lane, public_news=receipt,
            public_news_sha256=digest if receipt is not None else None,
        )
    handoff["team_event_candidates"] = [enrich(row, "TEAM_EVENT") for row in handoff.get("team_event_candidates", []) or [] if isinstance(row, dict)]
    handoff["prop_candidates"] = [enrich(row, "PROP") for row in handoff.get("prop_candidates", []) or [] if isinstance(row, dict)]
    peer_holds = _propagate_peer_context_holds(handoff)
    out["model_handoff"] = handoff
    # Count source-screened evidence only; a requested feature is not proof
    # that current evidence exists or any fitted specialist consumed it.
    statuses: dict[str, int] = {}
    reviewed_rows = missing_critical_rows = quote_screened_rows = 0
    for lane_key in ("team_event_candidates", "prop_candidates"):
        for candidate in handoff.get(lane_key, []) or []:
            audit = candidate.get("matchup_discovery")
            if not isinstance(audit, dict) or audit.get("review_requested") is not True:
                status = "MATCHUP_REVIEW_NOT_REQUESTED"
            else:
                status = str(audit.get("review_status") or "MATCHUP_REVIEW_STATUS_MISSING")
                reviewed_rows += 1
                missing_critical_rows += bool(audit.get("missing_critical_features"))
                quote_screened_rows += "exact_current_quote" in (audit.get("screened_features") or [])
            statuses[status] = statuses.get(status, 0) + 1
    out["matchup_discovery_summary"] = {
        "schema_version": "wow.v17.matchup_discovery_coverage.v1",
        "team_event_candidates": len(handoff.get("team_event_candidates", []) or []),
        "prop_candidates": len(handoff.get("prop_candidates", []) or []),
        "review_requested_rows": reviewed_rows,
        "review_status_counts": statuses,
        "rows_with_missing_critical_evidence": missing_critical_rows,
        "rows_with_screened_exact_quote": quote_screened_rows,
        "model_rescore_verified": False,
        "prediction_authority": False,
        "can_execute": False,
    }
    # Visibility across both lanes; requested rescores are NOT verified
    # specialist consumption or certified probability updates.
    context_statuses: dict[str, int] = {}
    context_stale_rows = 0
    for lane_key in ("team_event_candidates", "prop_candidates"):
        for candidate in handoff.get(lane_key, []) or []:
            impact = candidate.get("context_change_impact") or {}
            status = str(impact.get("impact_status") or "CONTEXT_AUDIT_MISSING")
            context_statuses[status] = context_statuses.get(status, 0) + 1
            context_stale_rows += bool(impact.get("research_invalidation_requested"))
    out["context_impact_summary"] = {
        "schema_version": "wow.v17.context_impact_coverage.v1",
        "impact_status_counts": context_statuses,
        "rows_research_invalidation_requested": context_stale_rows,
        "same_event_peer_rows_quarantined": peer_holds,
        "all_position_roles_scoped": True,
        "all_applicable_market_families_scoped": True,
        "fitted_specialist_rescore_verified": False,
        "terminal_enforcement_verified": False,
        "can_execute": False,
    }
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
    # Preserve main's authenticated/reconciled-news dispatch before final
    # Scout research promotion; missing fitted bindings remain typed HOLDs.
    out = dispatch_news_rescores_for_handoff(out)
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
                item["public_news_discovery_sha256"] = digest
                touched += 1
            updated.append(item)
        updated_handoff[lane] = updated
    bridge_status = ("NEWS_SOURCE_BLOCKED" if discovery["source_status"] == "BLOCKED"
                     else "NEWS_DISCOVERY_ATTACHED" if touched else "NEWS_NO_MATCHING_SPORT")
    out = status(bridge_status, touched=touched, digest=digest)
    out["public_news_discovery_bridge"].update({
        "source_status": discovery["source_status"],
        "sport_key": discovery["sport_key"],
        "source_code": discovery.get("source_code"),
        "article_count": len(discovery["articles"]),
        "source_observed_at": discovery.get("source_observed_at"),
        "model_rescore_verified": False,
    })
    out["model_handoff"] = updated_handoff
    if touched:
        out["public_news_discovery_receipt"] = discovery
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
    payload = attach_reviewed_facts_bundle(payload, source)
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
        "matchup_review_status_counts": enriched.get("matchup_discovery_summary", {}).get("review_status_counts", {}),
        "matchup_rows_with_missing_critical_evidence": enriched.get("matchup_discovery_summary", {}).get("rows_with_missing_critical_evidence", 0),
        "matchup_rows_with_screened_exact_quote": enriched.get("matchup_discovery_summary", {}).get("rows_with_screened_exact_quote", 0),
        "news_bridge_status": enriched.get("public_news_discovery_bridge", {}).get("status", "NOT_PRESENT"),
        "news_source_code": enriched.get("public_news_discovery_bridge", {}).get("source_code", "NOT_PRESENT"),
        "news_article_count": enriched.get("public_news_discovery_bridge", {}).get("article_count", 0),
        "news_candidates_touched": enriched.get("public_news_discovery_bridge", {}).get("candidates_touched", 0),
        "news_reviewed_facts_bridge_status": enriched.get("news_reviewed_facts_bridge", {}).get("status", "NOT_PRESENT"),
        "news_reviewed_facts_attached": enriched.get("news_reviewed_facts_bridge", {}).get("candidates_attached", 0),
        "news_rescore_considered": enriched.get("news_fitted_rescore_summary", {}).get("candidates_considered", 0),
        "news_rescore_verified": enriched.get("news_fitted_rescore_summary", {}).get("rescore_verified", 0),
        "can_execute": False,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
