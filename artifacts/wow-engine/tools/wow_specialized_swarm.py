"""WOW-native, zero-provider-call, research-only specialist swarm.

The coordinator runs bounded local Python specialists against an existing,
canonical event snapshot. It does not use Antigravity, an LLM, the network,
production storage, a fitted scorer, or a betting/trading interface.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

_ENGINE_ROOT = str(Path(__file__).resolve().parents[1])
if _ENGINE_ROOT not in sys.path:
    sys.path.insert(0, _ENGINE_ROOT)

SCHEMA = "wow.v17.native_swarm.research.v1"
POLICY = {
    "custom_gpt_identity": "WOW_BETTING_ENGINE",
    "runtime_generation": "V17_ACTIVE",
    "terminal_authority": "V17_TERMINAL_REDUCER",
    "can_execute": False,
    "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS": True,
    "research_ceiling": "RESEARCH_INTEREST",
    "may_fit_or_estimate_probability": False,
    "may_certify_or_qualify": False,
    "may_approve_protected_change": False,
}
# Reuse IDs from v17.sport_research_brief, never add a second registry.
WORKERS = (
    "wow.source-provenance-researcher",
    "wow.participant-status-researcher",
    "wow.matchup-context-researcher",
    "wow.history-comparables-researcher",
    "wow.market-settlement-researcher",
)
FORBIDDEN_KEYS = {
    "probability", "win_probability", "model_probability", "fitted_probability",
    "estimated_probability", "calibration", "certification", "qualification",
    "rank_eligible", "stake", "order", "wager", "approval", "approved",
    "model_score", "model_prediction", "predicted_winner", "confidence",
    "model_confidence", "estimated_edge", "fitted_edge",
}
MAX_FILE_BYTES = 256_000
MAX_METRICS = 128
MAX_OBSERVATIONS_PER_METRIC = 32


def _time(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601 with a UTC offset") from exc
    if parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _now(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(timezone.utc)


def _prohibited_feature_name(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return (normalized in FORBIDDEN_KEYS or "probability" in normalized
            or "confidence" in normalized or normalized.startswith("predicted_")
            or normalized.startswith("model_prediction") or normalized.endswith("_edge"))


def _has_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if (normalized in FORBIDDEN_KEYS or "probability" in normalized
                    or "confidence" in normalized or normalized.endswith("_edge")):
                return True
            if _has_forbidden_key(item):
                return True
    elif isinstance(value, list):
        return any(_has_forbidden_key(item) for item in value)
    return False


NRE_RECEIPT_SCHEMA = "WOW_NRE_SWARM_RECEIPT_V1"
_NRE_SPORT_PREFIX = {"SOCCER": ("soccer_",), "NFL": ("americanfootball_nfl",), "NCAAF": ("americanfootball_ncaaf",),
                     "NBA": ("basketball_nba",), "NCAAB": ("basketball_ncaab",), "WNBA": ("basketball_wnba",),
                     "NHL": ("icehockey_nhl",), "MLB": ("baseball_mlb",), "TENNIS": ("tennis_",)}
_NRE_SOCCER_LEAGUE = {"soccer_epl": "EPL", "soccer_spain_la_liga": "LALIGA", "soccer_germany_bundesliga": "BUNDESLIGA",
                      "soccer_italy_serie_a": "SERIE_A", "soccer_france_ligue_one": "LIGUE_1"}


def _nre_digest(body: dict[str, Any]) -> str:
    # Same canonical form as v17.native_research.contract.sha256_json; kept
    # local so the Swarm imports nothing that could reach the network.
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
                          .encode("utf-8")).hexdigest()


def _nre_rejection(receipt: Any, sport_key: str) -> str | None:
    if not isinstance(receipt, dict) or receipt.get("schema_version") != NRE_RECEIPT_SCHEMA:
        return "NRE_RECEIPT_SCHEMA_INVALID"
    body = {k: v for k, v in receipt.items() if k != "receipt_sha256"}
    if receipt.get("receipt_sha256") != _nre_digest(body):
        return "NRE_RECEIPT_DIGEST_MISMATCH"
    if (receipt.get("evidence_only") is not True or receipt.get("prediction_authority") is not False
            or receipt.get("can_execute") is not False):
        return "NRE_RECEIPT_AUTHORITY_INVALID"
    if _has_forbidden_key(receipt):
        return "NRE_RECEIPT_FORBIDDEN_FIELD"
    sport = str(receipt.get("sport") or "")
    if not str(sport_key).startswith(_NRE_SPORT_PREFIX.get(sport, ("\0",))):
        return "NRE_RECEIPT_SPORT_MISMATCH"
    league = _NRE_SOCCER_LEAGUE.get(str(sport_key))
    if sport == "SOCCER" and league and receipt.get("league") not in (None, league):
        return "NRE_RECEIPT_LEAGUE_MISMATCH"
    return None


def consume_research_receipts(receipts: list[Any] | None, sport_key: str) -> dict[str, Any]:
    """Read persisted Native Research receipts. Network-free; evidence context only."""
    consumed, rejected = [], []
    for receipt in receipts or []:
        reason = _nre_rejection(receipt, sport_key)
        if reason:
            rejected.append(reason)
            continue
        consumed.append({k: receipt[k] for k in ("task_id", "sport", "league", "season", "blocker_code",
                                                 "outcome", "progress_stage", "remaining_blockers", "sources",
                                                 "receipt_sha256")})
    return {"consumed": consumed, "rejected": rejected,
            "remaining_blockers": sorted({b for c in consumed for b in c["remaining_blockers"]}),
            "evidence_only": True, "network_calls": 0}


def _canonical_event(event: dict[str, Any], now: datetime) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise ValueError("candidate must be an object")
    sport = event.get("sport_key")
    official_id = event.get("official_event_id")
    if (not isinstance(sport, str) or not sport.strip()
            or not isinstance(official_id, str) or not official_id.strip()
            or len(official_id) > 160):
        raise ValueError("canonical sport_key and official_event_id required")
    if event.get("can_execute") is not False:
        raise ValueError("can_execute=false must be explicit")
    starts = _time(event.get("commence_time"))
    if starts <= now:
        raise ValueError("pregame Swarm requires future event")
    return {
        "sport_key": sport.strip(),
        "official_event_id": official_id.strip(),
        "commence_time": starts.isoformat().replace("+00:00", "Z"),
        "home_team": str(event.get("home_team") or ""),
        "away_team": str(event.get("away_team") or ""),
        "can_execute": False,
    }


def build_manifest(event: dict[str, Any], *, now: datetime | None = None,
                   max_parallel: int = 2) -> dict[str, Any]:
    """Plan local bounded workers; do not acquire data or launch a model."""
    now = _now(now)
    if (not isinstance(max_parallel, int) or isinstance(max_parallel, bool)
            or max_parallel not in (1, 2, 3)):
        raise ValueError("max_parallel must be 1, 2, or 3")
    canonical = _canonical_event(event, now)
    from v17.matchup_discovery import profile_for
    from v17.sport_scout_registry import scout_team_for
    from v17.sport_research_brief import SUPPORTED_RESEARCH_WORKERS, build_research_brief

    if profile_for(canonical["sport_key"]) is None:
        raise ValueError("sport has no registered matchup-discovery profile")
    if any(worker not in SUPPORTED_RESEARCH_WORKERS for worker in WORKERS):
        raise ValueError("canonical research worker contract changed")
    team = scout_team_for(canonical["sport_key"])
    run_id = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()[:20]
    briefs = []
    for worker in WORKERS:
        brief = build_research_brief(canonical, worker, now=now)
        if brief.get("prediction_authority") is not False or brief.get("can_execute") is not False:
            raise ValueError("canonical research brief asserts prohibited authority")
        briefs.append({"worker_id": worker, "brief": brief,
                       "allowed_activity": "LOCAL_READ_ONLY_RESEARCH"})
    waves = [
        {"wave": i // max_parallel + 1, "worker_ids": list(WORKERS[i:i + max_parallel])}
        for i in range(0, len(WORKERS), max_parallel)
    ]
    return {
        "schema_version": SCHEMA, "run_id": run_id, "event": canonical,
        "scout_team_id": team.team_id,
        "coverage_type": ("GENERIC_RESEARCH_ONLY" if team.sport_key == "*"
                          else "SPORT_SPECIFIC_RESEARCH"),
        "max_parallel": max_parallel, "waves": waves, "workers": briefs,
        "provider": "WOW_NATIVE_PYTHON", "paid_provider_calls": 0,
        "network_calls": 0, "policy": dict(POLICY),
    }


def validate_submission(manifest: dict[str, Any], submission: Any,
                        *, now: datetime | None = None) -> dict[str, Any]:
    """Fail-closed *structural* validation, not independent fact verification."""
    now = _now(now)
    errors: list[str] = []
    if not isinstance(submission, dict):
        return {"valid_report": False, "errors": ["submission must be an object"],
                "research_only": True, "may_publish": False, "can_execute": False}
    event = manifest["event"]
    if submission.get("run_id") != manifest["run_id"]:
        errors.append("wrong local run ID")
    if submission.get("official_event_id") != event["official_event_id"]:
        errors.append("wrong official event ID")
    if submission.get("sport_key") != event["sport_key"]:
        errors.append("wrong sport")
    worker_ids = {w["worker_id"] for w in manifest["workers"]}
    worker_id = submission.get("worker_id")
    if not isinstance(worker_id, str) or worker_id not in worker_ids:
        errors.append("unassigned worker")
    if submission.get("research_ceiling") != "RESEARCH_INTEREST":
        errors.append("research ceiling missing or altered")
    if submission.get("can_execute") is not False:
        errors.append("execution flag missing or altered")
    allowed = {"run_id", "sport_key", "official_event_id", "worker_id",
               "research_ceiling", "can_execute", "facts", "missing_evidence", "conflicts"}
    if set(submission) - allowed or _has_forbidden_key(submission):
        errors.append("unauthorized fields or predicted/protected authority")
    facts, missing, conflicts = (submission.get(k) for k in
                                 ("facts", "missing_evidence", "conflicts"))
    if (not isinstance(facts, list) or not isinstance(missing, list)
            or not isinstance(conflicts, list)):
        errors.append("facts, missing_evidence and conflicts must be arrays")
        facts = []
    if not facts and not missing and not errors:
        errors.append("empty report cannot assert completed research")
    for label, values in (("missing_evidence", missing), ("conflicts", conflicts)):
        if isinstance(values, list) and not all(isinstance(s, str) and s for s in values):
            errors.append(f"{label} must contain nonempty text reasons")
    cutoff = _time(event["commence_time"])
    for index, fact in enumerate(facts):
        if not isinstance(fact, dict):
            errors.append(f"fact {index}: expected object")
            continue
        permitted = {"feature", "value", "source_id", "source_class", "captured_at",
                     "official_event_id", "source_uri", "source_rights_status", "conflict"}
        if set(fact) - permitted or _has_forbidden_key(fact):
            errors.append(f"fact {index}: unauthorized fields")
        if _prohibited_feature_name(fact.get("feature")):
            errors.append(f"fact {index}: prohibited prediction feature")
        if fact.get("official_event_id") != event["official_event_id"]:
            errors.append(f"fact {index}: cross-event evidence")
        if not all(isinstance(fact.get(k), str) and fact[k].strip()
                   for k in ("feature", "source_id", "source_class")):
            errors.append(f"fact {index}: source or feature missing")
        value = fact.get("value")
        if (value is None or value == {} or value == []
                or (isinstance(value, str) and not value.strip())):
            errors.append(f"fact {index}: measurement missing")
        if "conflict" in fact and fact["conflict"] is not False:
            errors.append(f"fact {index}: unresolved or malformed evidence conflict")
        try:
            captured = _time(fact.get("captured_at"))
            if captured >= cutoff or captured > now:
                errors.append(f"fact {index}: non-pregame or future timestamp")
        except ValueError:
            errors.append(f"fact {index}: source timestamp invalid")
        if fact.get("source_rights_status") not in ("CLEARED_FOR_RESEARCH", "REVIEW_REQUIRED"):
            errors.append(f"fact {index}: source rights must be explicit")
    return {
        "valid_report": not errors, "errors": errors,
        "facts_screened_structurally": len(facts) if not errors else 0,
        "missing_evidence_count": len(missing) if isinstance(missing, list) else None,
        "research_only": True, "independent_source_verification": False,
        "may_publish": False, "may_fit_probability": False, "can_execute": False,
    }


def _worker_features(requirements: dict[str, tuple[str, ...]]) -> dict[str, list[str]]:
    """Each canonical matchup feature is owned by exactly one local specialist."""
    groups = {key: list(values) for key, values in requirements.items()}
    assigned = {worker: [] for worker in WORKERS}
    personnel_tokens = ("goalie", "lineup", "starting", "minutes", "rotation",
                        "weigh_in", "weight_cut", "injury", "medical", "short_notice",
                        "camp_changes", "pitcher", "bullpen")
    for group, fields in groups.items():
        for feature in fields:
            if _prohibited_feature_name(feature):
                continue
            if group == "MARKET":
                worker = WORKERS[4]
            elif group == "PERSONNEL" or (group == "SPORT_SPECIFIC" and
                                           any(t in feature for t in personnel_tokens)):
                worker = WORKERS[1]
            elif group == "BASELINE":
                worker = WORKERS[3]
            else:
                worker = WORKERS[2]
            if feature not in assigned[worker]:
                assigned[worker].append(feature)
    return assigned


def _raw_observations(candidate: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    from v17.matchup_discovery import _metric_rows
    raw = _metric_rows(candidate)
    if not isinstance(raw, dict) or len(raw) > MAX_METRICS:
        raise ValueError("candidate evidence exceeds bounded metric contract")
    observations: dict[str, list[dict[str, Any]]] = {}
    for feature, value in raw.items():
        rows = value if isinstance(value, list) else [value]
        if len(rows) > MAX_OBSERVATIONS_PER_METRIC:
            raise ValueError("metric exceeds bounded evidence contract")
        observations[feature] = [row for row in rows if isinstance(row, dict)]
    return observations


def _to_fact(feature: str, row: dict[str, Any], event_id: str) -> dict[str, Any]:
    measurement = row.get("value")
    if measurement is None:
        measurement = row.get("values")
    fact = {
        "feature": feature, "value": measurement,
        "source_id": row.get("source_id"),
        "source_class": row.get("source_class"),
        "captured_at": row.get("captured_at"),
        "official_event_id": row.get("official_event_id"),
        "source_rights_status": row.get("source_rights_status", "REVIEW_REQUIRED"),
    }
    if row.get("source_uri"):
        fact["source_uri"] = row["source_uri"]
    if row.get("conflict") is True:
        fact["conflict"] = True
    return fact


def _local_worker(worker_id: str, manifest: dict[str, Any],
                  assigned: dict[str, list[str]], observations: dict[str, list[dict[str, Any]]],
                  audit: dict[str, Any]) -> dict[str, Any]:
    """No network, prompts, fitted probabilities, mutable DB, or external calls."""
    screened = set(audit["screened_features"])
    invalid = audit.get("invalid_or_stale_features") or {}
    facts: list[dict[str, Any]] = []
    missing: list[str] = []
    conflicts: list[str] = []
    if worker_id == WORKERS[0]:
        # Cross-cutting provenance worker. Enumerate already-screened sources;
        # don't pretend to independently attest source rights or data truth.
        features = [feature for feature in observations if feature in screened]
    else:
        features = assigned[worker_id]
    for feature in features:
        if feature not in screened:
            code = invalid.get(feature)
            missing.append(f"{feature}:{code or 'NOT_SCREENED'}")
            if code == "CONFLICT_QUARANTINED":
                conflicts.append(f"{feature}:SOURCE_CONFLICT")
            continue
        eligible_found = False
        for row in observations.get(feature, []):
            if row.get("source_rights_status") != "CLEARED_FOR_RESEARCH":
                missing.append(f"{feature}:SOURCE_RIGHTS_REVIEW_REQUIRED")
                continue
            fact = _to_fact(feature, row, manifest["event"]["official_event_id"])
            # Never assume the source-screened feature means every raw row
            # passed: independently recheck structural identity/chronology.
            if (_has_forbidden_key(fact) or _prohibited_feature_name(feature)
                    or fact.get("official_event_id") != manifest["event"]["official_event_id"]):
                conflicts.append(f"{feature}:INVALID_OR_CROSS_EVENT_FACT")
                continue
            facts.append(fact)
            eligible_found = True
        if not eligible_found:
            missing.append(f"{feature}:NO_RIGHTS_CLEARED_EVIDENCE")
    if worker_id == WORKERS[0] and not features:
        missing.append("NO_SCREENED_SOURCE_EVIDENCE")
    return {
        "run_id": manifest["run_id"], "sport_key": manifest["event"]["sport_key"],
        "official_event_id": manifest["event"]["official_event_id"],
        "worker_id": worker_id, "research_ceiling": "RESEARCH_INTEREST",
        "can_execute": False, "facts": facts,
        "missing_evidence": sorted(set(missing)), "conflicts": sorted(set(conflicts)),
    }


def run_swarm(candidate: dict[str, Any], *, now: datetime | None = None,
              max_parallel: int = 2, research_receipts: list[Any] | None = None) -> dict[str, Any]:
    """Execute genuine bounded local workers and a per-wave fail-closed barrier.

    No call to the existing fitted specialist is performed. All findings remain
    research metadata. The caller owns any explicitly governed downstream path.
    """
    now = _now(now)
    manifest = build_manifest(candidate, now=now, max_parallel=max_parallel)
    from v17.matchup_discovery import discover_matchup, feature_requirements, _screen_row
    audit = discover_matchup(candidate, now=now)
    if (audit.get("official_event_id") != manifest["event"]["official_event_id"]
            or audit.get("can_execute") is not False
            or audit.get("prediction_authority") is not False):
        raise ValueError("canonical matchup audit returned invalid authority or identity")
    raw_observations = _raw_observations(candidate)
    # The canonical audit can mark a feature screened when at least one row
    # passes. An invalid sibling row must never ride along into the handoff.
    cutoff = _time(manifest["event"]["commence_time"])
    observations: dict[str, list[dict[str, Any]]] = {}
    rejected: dict[str, list[str]] = {}
    for feature, rows in raw_observations.items():
        observations[feature] = []
        rejected[feature] = []
        for row in rows:
            reason = _screen_row(row, candidate, cutoff, now, feature)
            if reason == "RESEARCH_SOURCE_SCREENED":
                observations[feature].append(row)
            else:
                rejected[feature].append(reason)
    assigned = _worker_features(feature_requirements(manifest["event"]["sport_key"]))
    results: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    for wave in manifest["waves"]:
        # Each wave is a barrier; no next wave can begin before review.
        with ThreadPoolExecutor(max_workers=min(len(wave["worker_ids"]), max_parallel)) as pool:
            futures = [pool.submit(_local_worker, worker_id, manifest, assigned,
                                   observations, audit) for worker_id in wave["worker_ids"]]
            submissions = [future.result() for future in futures]
        for submission in submissions:
            relevant = list(observations) if submission["worker_id"] == WORKERS[0] else assigned[submission["worker_id"]]
            for feature in relevant:
                for reason in rejected.get(feature, []):
                    submission["missing_evidence"].append(f"{feature}:{reason}")
                    if reason in ("EVENT_ID_MISMATCH", "PARTICIPANT_MISMATCH", "CONFLICT_QUARANTINED"):
                        submission["conflicts"].append(f"{feature}:{reason}")
            submission["missing_evidence"] = sorted(set(submission["missing_evidence"]))
            submission["conflicts"] = sorted(set(submission["conflicts"]))
        verdicts = [validate_submission(manifest, item, now=now) for item in submissions]
        reviews.append({"wave": wave["wave"],
                        "all_structurally_valid": all(v["valid_report"] for v in verdicts),
                        "workers_checked": list(wave["worker_ids"]),
                        "verdicts": verdicts})
        if not all(v["valid_report"] for v in verdicts):
            # Never allow an invalid report into the consolidated handoff.
            raise ValueError("local Swarm worker produced structurally invalid research")
        results.extend(submissions)
    # The source worker cross-checks metadata; only domain-owned facts are
    # forwarded, without duplicated source-worker observations.
    forwarded = [fact for result in results if result["worker_id"] != WORKERS[0]
                 for fact in result["facts"]]
    missing = sorted({reason for result in results for reason in result["missing_evidence"]})
    conflicts = sorted({reason for result in results for reason in result["conflicts"]})
    status = ("RESEARCH_ONLY_HOLD" if missing or conflicts or audit["missing_critical_features"]
              else "RESEARCH_ONLY_SCREENED")
    native = consume_research_receipts(research_receipts, manifest["event"]["sport_key"])
    digests = [c["receipt_sha256"] for c in native["consumed"]]
    receipt_id = hashlib.sha256(json.dumps({
        "manifest_id": manifest["run_id"], "as_of": now.isoformat(),
        "workers": results, "audit": audit, **({"native_research": digests} if digests else {})},
        sort_keys=True, default=str).encode()).hexdigest()
    return {
        "schema_version": SCHEMA, "status": status, "receipt_sha256": receipt_id,
        "run_id": manifest["run_id"], "event": manifest["event"],
        "scout_team_id": manifest["scout_team_id"],
        "coverage_type": manifest["coverage_type"],
        "max_parallel": max_parallel, "waves_completed": len(reviews),
        "worker_reports": results, "wave_reviews": reviews,
        "matchup_discovery": audit,
        "native_research": native,
        "handoff": {
            "destination": "existing V17 sport_scout_enrichment / research handoff",
            "source_screened_research_facts": forwarded,
            "source_rights_review_required": any("SOURCE_RIGHTS" in x for x in missing),
            "missing_evidence": missing, "conflicts": conflicts,
            "specialist_rescore_requested": False, "specialist_rescore_verified": False,
            "probability_authority": False, "may_publish_or_qualify": False,
            "can_execute": False,
        },
        "external_llm_calls": 0, "paid_provider_calls": 0, "network_calls": 0,
        "persisted_to_supabase": False, "production_verified": False,
        "policy": dict(POLICY),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="WOW native no-credit multi-specialist research")
    parser.add_argument("--candidate", required=True, help="local existing candidate snapshot JSON")
    parser.add_argument("--max-parallel", type=int, choices=(1, 2, 3), default=2)
    parser.add_argument("--output", help="optional local JSON receipt file; never writes to production")
    parser.add_argument("--research-receipt", action="append", default=[],
                        help="persisted Native Research receipt JSON (repeatable); read locally, never fetched")
    args = parser.parse_args()
    try:
        candidate_path = Path(args.candidate)
        if candidate_path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("candidate exceeds bounded size limit")
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        research = []
        for item in args.research_receipt:
            path = Path(item)
            if path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("research receipt exceeds bounded size limit")
            research.append(json.loads(path.read_text(encoding="utf-8")))
        receipt = run_swarm(candidate, max_parallel=args.max_parallel, research_receipts=research)
        payload = json.dumps(receipt, sort_keys=True, indent=2, allow_nan=False)
        if args.output:
            Path(args.output).write_text(payload + "\n", encoding="utf-8")
        else:
            print(payload)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, f"WOW native Swarm research hold: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())