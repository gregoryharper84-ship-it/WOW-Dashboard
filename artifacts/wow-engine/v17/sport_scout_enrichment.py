"""Attach sport-specialist Scout team metadata to a V17 Multi-Scout handoff.

This is additive enrichment and research disposition only. It does not score,
calibrate, qualify, approve, or execute any wager.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:  # package import under pytest/backend runtime
    from v17.market_evidence_snapshot_bridge import attach_snapshot_evidence
    from v17.matchup_discovery import discover_matchup
    from v17.news_event_research_handoff import reconcile_news_handoff
    from v17.news_fitted_rescore_dispatch import dispatch_news_rescores_for_handoff
    from v17.news_reviewed_fact_producer import attach_reviewed_facts_bundle
    from v17.scout_research_promotion import promote_handoff
    from v17.sport_scout_registry import registry_payload, scout_team_for
    from v17.sport_research_brief import SUPPORTED_RESEARCH_WORKERS, build_research_brief
except ModuleNotFoundError:  # direct `python v17/sport_scout_enrichment.py`
    from market_evidence_snapshot_bridge import attach_snapshot_evidence
    from matchup_discovery import discover_matchup
    from news_event_research_handoff import reconcile_news_handoff
    from news_fitted_rescore_dispatch import dispatch_news_rescores_for_handoff
    from news_reviewed_fact_producer import attach_reviewed_facts_bundle
    from scout_research_promotion import promote_handoff
    from sport_scout_registry import registry_payload, scout_team_for
    from sport_research_brief import SUPPORTED_RESEARCH_WORKERS, build_research_brief




def request_one_verified_native_swarm_pilot(
    payload: dict[str, Any], *, now: datetime | None = None,
) -> dict[str, Any]:
    """Explicit CLI-only admission: one canonical future event, research-only.

    The scheduled caller opts in at the CLI boundary. No provider event ID is
    promoted to an official identity, no source rights are inferred, and no
    existing candidate is silently removed from the normal Scout handoff.
    """
    if not isinstance(payload, dict) or payload.get("can_execute") is not False:
        raise ValueError("SCOUT_HANDOFF_CAN_EXECUTE_MUST_BE_FALSE")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("NATIVE_SWARM_PILOT_TIME_MUST_BE_AWARE")
    now = now.astimezone(timezone.utc)
    handoff = payload.get("model_handoff")
    handoff = handoff if isinstance(handoff, dict) else {}
    lanes = ("team_event_candidates", "prop_candidates")
    existing = sum(
        row.get("native_swarm_requested") is True
        for lane in lanes for row in (handoff.get(lane) or [])
        if isinstance(row, dict)
    )
    base = {
        "schema_version": "wow.v17.native_swarm_pilot_admission.v1",
        "mode": "ONE_CANONICAL_PREGAME_EVENT",
        "requested_count": 0,
        "paid_provider_calls": 0,
        "may_publish_or_qualify": False,
        "can_execute": False,
    }
    if existing:
        return {**payload, "native_swarm_pilot": {
            **base, "status": "EXPLICIT_CANDIDATE_OPT_IN_ALREADY_PRESENT",
            "requested_count": existing,
        }}
    eligible: list[tuple[datetime, str, str, int]] = []
    for lane in lanes:
        rows = handoff.get(lane)
        if not isinstance(rows, list):
            continue
        for index, row in enumerate(rows):
            # The raw nightly Scout candidate normally omits this field;
            # the outer handoff is already explicitly nonexecuting. Reject
            # any row that asserts or malforms its own execution bit.
            if not isinstance(row, dict) or (("can_execute" in row)
                                             and row["can_execute"] is not False):
                continue
            official_id = row.get("official_event_id")
            if (row.get("official_event_identity_verified") is not True
                    or row.get("canonical_identity_status") != "CANONICAL_RESOLVED"
                    or not isinstance(official_id, str) or not (0 < len(official_id) <= 160)
                    or row.get("canonical_event_id") != official_id
                    or not isinstance(row.get("sport_key"), str)
                    or not row["sport_key"].strip()):
                continue
            try:
                value = row.get("commence_time")
                if not isinstance(value, str):
                    continue
                start = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if start.utcoffset() is None:
                    continue
                start = start.astimezone(timezone.utc)
            except ValueError:
                continue
            # Never launch an asynchronous pregame pilot at the event boundary.
            if start > now + timedelta(minutes=20):
                eligible.append((start, official_id, lane, index))
    if not eligible:
        return {**payload, "native_swarm_pilot": {
            **base, "status": "NO_VERIFIED_FUTURE_EVENT",
        }}
    _, event_id, lane, index = min(eligible)
    updated_handoff = dict(handoff)
    updated_rows = list(handoff[lane])
    updated_rows[index] = {**updated_rows[index], "can_execute": False,
                           "native_swarm_requested": True}
    updated_handoff[lane] = updated_rows
    return {
        **payload,
        "model_handoff": updated_handoff,
        "native_swarm_pilot": {
            **base, "status": "VERIFIED_EVENT_OPTED_IN",
            "requested_count": 1,
            "official_event_id": event_id,
            "sport_key": updated_rows[index]["sport_key"],
        },
    }


def _native_swarm_receipt_valid(
    receipt: dict[str, Any], expected_manifest: dict[str, Any],
    worker_ids: tuple[str, ...], validate_submission: Any,
) -> bool:
    """Bound a local receipt before promoting *any* field to public metadata."""
    try:
        digest = receipt.get("receipt_sha256")
        run_id = receipt.get("run_id")
        event = receipt.get("event")
        workers = receipt.get("worker_reports")
        reviews = receipt.get("wave_reviews")
        handoff = receipt.get("handoff")
        hexdigits = "0123456789abcdef"
        if (not isinstance(digest, str) or len(digest) != 64
                or any(c not in hexdigits for c in digest)
                or not isinstance(run_id, str) or len(run_id) != 20
                or any(c not in hexdigits for c in run_id)
                or not isinstance(event, dict)
                or event != expected_manifest.get("event")
                or run_id != expected_manifest.get("run_id")
                or event.get("can_execute") is not False
                or receipt.get("max_parallel") != 2
                or receipt.get("waves_completed") != 3
                or not isinstance(workers, list) or len(workers) != len(worker_ids)
                or not isinstance(reviews, list) or len(reviews) != 3
                or not isinstance(handoff, dict)
                or not isinstance(handoff.get("source_screened_research_facts"), list)):
            return False
        manifest = expected_manifest
        for i, report in enumerate(workers):
            if (not isinstance(report, dict)
                    or report.get("worker_id") != worker_ids[i]
                    or report.get("run_id") != run_id
                    or report.get("official_event_id") != event["official_event_id"]
                    or report.get("sport_key") != event["sport_key"]
                    or report.get("research_ceiling") != "RESEARCH_INTEREST"
                    or report.get("can_execute") is not False
                    or validate_submission(manifest, report).get("valid_report") is not True):
                return False
        for i, wave in enumerate(reviews):
            expected = list(worker_ids[i * 2:i * 2 + 2])
            if (not isinstance(wave, dict)
                    or wave.get("wave") != i + 1
                    or wave.get("workers_checked") != expected
                    or wave.get("all_structurally_valid") is not True):
                return False
            verdicts = wave.get("verdicts")
            if not isinstance(verdicts, list) or len(verdicts) != len(expected):
                return False
            for verdict, worker in zip(verdicts, workers[i * 2:i * 2 + 2]):
                if (not isinstance(verdict, dict)
                        or verdict.get("valid_report") is not True
                        or verdict.get("research_only") is not True
                        or verdict.get("may_publish") is not False
                        or verdict.get("can_execute") is not False
                        or verdict.get("facts_screened_structurally") != len(worker["facts"])):
                    return False
        # Reconstruct every aggregate from canonical worker evidence once;
        # never let a return receipt hide an independent worker HOLD.
        domain_facts = [fact for worker in workers[1:] for fact in worker["facts"]]
        missing = sorted({reason for worker in workers for reason in worker["missing_evidence"]})
        conflicts = sorted({reason for worker in workers for reason in worker["conflicts"]})
        audit = receipt.get("matchup_discovery")
        if not isinstance(audit, dict) or not isinstance(audit.get("missing_critical_features"), list):
            return False
        expected_status = ("RESEARCH_ONLY_HOLD" if missing or conflicts or audit["missing_critical_features"]
                           else "RESEARCH_ONLY_SCREENED")
        return (all(isinstance(fact, dict) and fact.get("source_rights_status") == "CLEARED_FOR_RESEARCH"
                    for fact in domain_facts)
                and handoff["source_screened_research_facts"] == domain_facts
                and handoff.get("missing_evidence") == missing
                and handoff.get("conflicts") == conflicts
                and receipt.get("status") == expected_status)
    except (KeyError, TypeError, ValueError, AttributeError):
        # A malformed/untrusted receipt is a typed research hold, never metadata.
        return False


def _native_swarm_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    """Optional, bounded summary-only native research; no raw source payload export.

    Opt-in is explicit per candidate; absence leaves existing Scout behavior
    untouched. Even a successful Swarm receipt has no scoring or release power.
    """
    base = {
        "schema_version": "wow.v17.native_swarm_handoff_summary.v1",
        "status": "RESEARCH_ONLY_HOLD",
        "reason_code": None,
        "receipt_sha256": None,
        "research_facts_screened": 0,
        "missing_evidence_count": 0,
        "conflicts_count": 0,
        "workers_completed": 0,
        "external_llm_calls": 0,
        "paid_provider_calls": 0,
        "independent_source_verification": False,
        "specialist_rescore_verified": False,
        "may_publish_or_qualify": False,
        "prediction_authority": False,
        "can_execute": False,
    }
    if candidate.get("can_execute") is not False:
        return {**base, "reason_code": "NATIVE_SWARM_INPUT_INVALID"}
    event_request = candidate.get("event_request")
    event_request = event_request if isinstance(event_request, dict) else {}
    start = (candidate.get("commence_time") or candidate.get("event_start_utc")
             or candidate.get("event_start_time_utc")
             or event_request.get("event_start_time_utc"))
    normalized = {**candidate, "commence_time": start}
    try:
        try:
            from tools.wow_specialized_swarm import WORKERS, build_manifest, run_swarm, validate_submission
        except ModuleNotFoundError as exc:
            if exc.name != "tools":
                raise
            # The existing direct-script CLI loads from /v17, not engine root.
            import sys
            engine_root = str(Path(__file__).resolve().parents[1])
            if engine_root not in sys.path:
                sys.path.insert(0, engine_root)
            from tools.wow_specialized_swarm import WORKERS, build_manifest, run_swarm, validate_submission
        # The manifest is recomputed from the caller's canonical event, not
        # from the untrusted return. Invalid source identity/time is INPUT.
        expected_manifest = build_manifest(normalized, max_parallel=2)
    except (ValueError, TypeError, KeyError):
        return {**base, "reason_code": "NATIVE_SWARM_INPUT_INVALID"}
    except Exception:
        return {**base, "reason_code": "NATIVE_SWARM_LOCAL_RUNTIME_FAILED"}
    try:
        receipt = run_swarm(normalized, max_parallel=2)
    except Exception:
        # Once admitted, all worker/verification faults are runtime contract
        # failures, even if the coordinator raises ValueError.
        return {**base, "reason_code": "NATIVE_SWARM_LOCAL_RUNTIME_FAILED"}
    if not isinstance(receipt, dict):
        return {**base, "reason_code": "NATIVE_SWARM_LOCAL_RUNTIME_FAILED"}
    handoff = receipt.get("handoff")
    policy = receipt.get("policy")
    workers, reviews = receipt.get("worker_reports"), receipt.get("wave_reviews")
    if (receipt.get("status") not in ("RESEARCH_ONLY_HOLD", "RESEARCH_ONLY_SCREENED")
            or not isinstance(policy, dict) or policy.get("can_execute") is not False
            or receipt.get("external_llm_calls") != 0
            or receipt.get("paid_provider_calls") != 0
            or receipt.get("network_calls") != 0
            or receipt.get("persisted_to_supabase") is not False
            or receipt.get("production_verified") is not False
            or not isinstance(handoff, dict)
            or handoff.get("can_execute") is not False
            or handoff.get("may_publish_or_qualify") is not False
            or handoff.get("probability_authority") is not False
            or handoff.get("specialist_rescore_verified") is not False
            or not isinstance(workers, list) or len(workers) != 5
            or not isinstance(reviews, list) or len(reviews) < 1
            or not all(isinstance(v, dict) and v.get("all_structurally_valid") is True for v in reviews)
            or not isinstance(handoff.get("source_screened_research_facts"), list)
            or not isinstance(handoff.get("missing_evidence"), list)
            or not isinstance(handoff.get("conflicts"), list)
            or not _native_swarm_receipt_valid(receipt, expected_manifest, WORKERS, validate_submission)):
        return {**base, "reason_code": "NATIVE_SWARM_LOCAL_RUNTIME_FAILED"}
    return {
        **base, "status": receipt["status"],
        "receipt_sha256": receipt.get("receipt_sha256"),
        "research_facts_screened": len(handoff["source_screened_research_facts"]),
        "missing_evidence_count": len(handoff["missing_evidence"]),
        "conflicts_count": len(handoff["conflicts"]),
        "workers_completed": len(workers),
    }


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
    if row.get("native_swarm_requested") is True:
        enriched["native_swarm_summary"] = _native_swarm_summary(enriched)
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
                "event_start_utc": (
                    row.get("canonical_identity_event_start_utc")
                    or row.get("event_start_utc")
                    or row.get("commence_time")
                    or (row.get("event_request") or {}).get("event_start_time_utc")
                ),
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
    enriched["can_execute"] = False
    return enriched


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
    # Attempt news-triggered fitted rescores only after research reconciliation.
    # Default scorer binding is absent → MODEL_UNAVAILABLE typed receipt, never
    # a synthetic probability. Callers may inject a real score_fn in tests or
    # production runners.
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
    parser.add_argument("--native-swarm-verified-pilot", action="store_true",
                        help="explicitly opt in exactly one verified future research-only event")
    args = parser.parse_args()
    source = Path(args.input)
    target = Path(args.output)
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload = _attach_sibling_snapshot(payload, source)
    payload = _attach_sibling_news(payload, source)
    payload = attach_reviewed_facts_bundle(payload, source)
    if args.native_swarm_verified_pilot:
        payload = request_one_verified_native_swarm_pilot(payload)
    enriched = enrich_handoff(payload)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(enriched, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    bridge = enriched.get("market_evidence_snapshot_bridge", {})
    print(json.dumps({
        "status": "SPORT_SCOUT_ENRICHMENT_COMPLETE",
        "team_event_candidates": len(enriched.get("model_handoff", {}).get("team_event_candidates", [])),
        "native_swarm_pilot_status": enriched.get("native_swarm_pilot", {}).get("status", "NOT_REQUESTED"),
        "native_swarm_pilot_requested": enriched.get("native_swarm_pilot", {}).get("requested_count", 0),
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
