"""Build and validate read-only Antigravity Swarm briefs for existing WOW Scouts.

This adapter emits a plan and a paste-ready /swarm prompt. It does not launch
agents, query external sources, write production data, or score a market.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

SCHEMA = "wow.v17.specialized_swarm.research.v1"
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
# These already exist in sport_research_brief.WORKER_ROLE; do not create a
# competing specialist identity or an independent execution queue.
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
    "model_score", "model_prediction", "predicted_winner",
}


def _time(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise ValueError("timestamp must be ISO-8601 with a UTC offset") from exc
    if parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _has_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in FORBIDDEN_KEYS or normalized.endswith("_probability"):
                return True
            if _has_forbidden_key(item):
                return True
    elif isinstance(value, list):
        return any(_has_forbidden_key(item) for item in value)
    return False


def build_manifest(event: dict[str, Any], *, now: datetime | None = None,
                   max_parallel: int = 2) -> dict[str, Any]:
    """Create a bounded plan, reusing the existing research worker contracts."""
    if not isinstance(event, dict):
        raise ValueError("event must be an object")
    sport = str(event.get("sport_key") or "").strip()
    official_id = str(event.get("official_event_id") or "").strip()
    if not sport or not official_id or len(official_id) > 160:
        raise ValueError("canonical sport_key and official_event_id are required")
    if not isinstance(max_parallel, int) or isinstance(max_parallel, bool) or max_parallel not in (1, 2, 3):
        raise ValueError("max_parallel must be 1, 2, or 3")
    if event.get("can_execute") is not False:
        raise ValueError("can_execute=false must be explicit")
    starts_raw = event.get("commence_time")
    starts = _time(starts_raw)
    now = now or datetime.now(timezone.utc)
    if now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)
    if starts <= now:
        raise ValueError("pregame Swarm requires a future event; postmortems use existing learning lane")

    # Import in the repository context, not a standalone/second registry.
    from v17.matchup_discovery import profile_for
    from v17.sport_scout_registry import scout_team_for
    from v17.sport_research_brief import SUPPORTED_RESEARCH_WORKERS, build_research_brief

    if profile_for(sport) is None:
        raise ValueError("sport has no registered matchup-discovery profile")
    if any(worker not in SUPPORTED_RESEARCH_WORKERS for worker in WORKERS):
        raise ValueError("canonical research worker contract changed; reconcile before use")
    team = scout_team_for(sport)
    canonical_event = {
        "sport_key": sport,
        "official_event_id": official_id,
        "commence_time": starts.isoformat().replace("+00:00", "Z"),
        "home_team": str(event.get("home_team") or ""),
        "away_team": str(event.get("away_team") or ""),
        "can_execute": False,
    }
    # No new identity authority. The hash keys this local plan only, not a
    # durable execution lease, event resolution, or production receipt.
    run_id = hashlib.sha256(json.dumps(canonical_event, sort_keys=True).encode()).hexdigest()[:20]
    briefs = []
    for worker in WORKERS:
        brief = build_research_brief(canonical_event, worker, now=now)
        if brief.get("can_execute") is not False or brief.get("prediction_authority") is not False:
            raise ValueError("canonical research brief unexpectedly asserts prediction authority")
        briefs.append({
            "worker_id": worker,
            "brief": brief,
            "allowed_activity": "READ_ONLY_RESEARCH",
            "output": "event-scoped evidence, missing evidence, conflicts, and source timestamps",
        })
    waves = [
        {"wave": i // max_parallel + 1,
         "worker_ids": list(WORKERS[i:i + max_parallel]),
         "verify_before_next_wave": True}
        for i in range(0, len(WORKERS), max_parallel)
    ]
    return {
        "schema_version": SCHEMA,
        "run_id": run_id,
        "event": canonical_event,
        "scout_team_id": team.team_id,
        "coverage_type": ("GENERIC_RESEARCH_ONLY" if team.sport_key == "*" else "SPORT_SPECIFIC_RESEARCH"),
        "max_parallel": max_parallel,
        "waves": waves,
        "workers": briefs,
        "verifier": {
            "role": "independent read-only evidence verifier (not model-certification authority)",
            "check": ["exact event ID", "pregame UTC chronology", "source identity and rights",
                      "missing/conflicting evidence", "no research probability/qualification claim"],
        },
        "handoff": {
            "destination": "existing v17 sport_scout_enrichment / research handoff",
            "may_request_controlling_fitted_specialist_rescore": True,
            "may_trigger_specialist_or_terminal_reducer": False,
            "may_publish_or_qualify": False,
        },
        "policy": dict(POLICY),
    }


def validate_submission(manifest: dict[str, Any], submission: dict[str, Any],
                        *, now: datetime | None = None) -> dict[str, Any]:
    """Structural fail-closed screen; not an independent source/model verifier."""
    errors: list[str] = []
    now = now or datetime.now(timezone.utc)
    if now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if not isinstance(submission, dict):
        return {"valid_report": False, "errors": ["submission must be an object"],
                "research_only": True, "may_publish": False}
    expected = manifest["event"]
    if submission.get("run_id") != manifest["run_id"]:
        errors.append("wrong local run ID")
    if submission.get("official_event_id") != expected["official_event_id"]:
        errors.append("wrong official event ID")
    if submission.get("sport_key") != expected["sport_key"]:
        errors.append("wrong sport")
    worker_ids = {w["worker_id"] for w in manifest["workers"]}
    if submission.get("worker_id") not in worker_ids:
        errors.append("unassigned worker")
    if submission.get("research_ceiling") != "RESEARCH_INTEREST":
        errors.append("research ceiling missing or altered")
    if submission.get("can_execute") is not False:
        errors.append("execution flag missing or altered")
    allowed = {"run_id", "sport_key", "official_event_id", "worker_id",
               "research_ceiling", "can_execute", "facts", "missing_evidence", "conflicts"}
    if set(submission) - allowed or _has_forbidden_key(submission):
        errors.append("unauthorized fields or predicted/protected authority")
    facts = submission.get("facts")
    missing = submission.get("missing_evidence")
    conflicts = submission.get("conflicts")
    if not isinstance(facts, list) or not isinstance(missing, list) or not isinstance(conflicts, list):
        errors.append("facts, missing_evidence and conflicts must be arrays")
        facts = []
    if not facts and not missing and not errors:
        errors.append("empty report cannot assert a completed research screen")
    cutoff = _time(expected["commence_time"])
    for index, fact in enumerate(facts):
        if not isinstance(fact, dict):
            errors.append(f"fact {index}: expected object")
            continue
        permitted_fact = {"feature", "value", "source_id", "source_class", "captured_at",
                          "official_event_id", "source_uri", "source_rights_status", "conflict"}
        if set(fact) - permitted_fact or _has_forbidden_key(fact):
            errors.append(f"fact {index}: unauthorized fields")
        if fact.get("official_event_id") != expected["official_event_id"]:
            errors.append(f"fact {index}: cross-event evidence")
        if not all(isinstance(fact.get(k), str) and fact[k].strip()
                   for k in ("feature", "source_id", "source_class")):
            errors.append(f"fact {index}: source or feature missing")
        if fact.get("value") is None or fact.get("value") == {}:
            errors.append(f"fact {index}: observed measurement missing")
        if fact.get("conflict") is True:
            errors.append(f"fact {index}: unresolved evidence conflict")
        try:
            captured = _time(fact.get("captured_at"))
            if captured >= cutoff or captured > now.astimezone(timezone.utc):
                errors.append(f"fact {index}: non-pregame or future timestamp")
        except ValueError:
            errors.append(f"fact {index}: missing timezone-aware source timestamp")
        # A provider may permit browsing but not training/copying. Unreviewed
        # rights remain explicit holds for downstream consumption.
        if fact.get("source_rights_status") not in ("CLEARED_FOR_RESEARCH", "REVIEW_REQUIRED"):
            errors.append(f"fact {index}: source rights must be explicit")
    return {
        "valid_report": not errors,
        "errors": errors,
        "facts_screened_structurally": len(facts) if not errors else 0,
        "missing_evidence_count": len(missing) if isinstance(missing, list) else None,
        "research_only": True,
        "independent_source_verification": False,
        "may_publish": False,
        "may_fit_probability": False,
        "can_execute": False,
    }


def antigravity_prompt(manifest: dict[str, Any]) -> str:
    """Render a paste-ready native Antigravity /swarm task, not an API call."""
    event = manifest["event"]
    names = ", ".join(
        f"@{alias}={worker}" for alias, worker in zip(
            ("source", "personnel", "matchup", "history", "market"),
            (item["worker_id"] for item in manifest["workers"]),
        )
    )
    return (
        "/swarm WOW Specialized Swarm — PRE-GAME RESEARCH ONLY. "
        f"Official event: {event['official_event_id']} ({event['sport_key']}), "
        f"starts {event['commence_time']} UTC. Local plan ID {manifest['run_id']}. "
        "Use repository skill wow-specialized-swarm and existing "
        "v17/sport_research_brief.py and v17/sport_scout_registry.py. "
        f"Named workers: {names}. Run at most {manifest['max_parallel']} workers at once "
        "in ordered waves; pause for an independent read-only verifier after each wave. "
        "Brief each worker with a distinct evidence domain and exact event ID. "
        "Shared board posts must include source identity, source rights status, "
        "UTC captured-at, evidence, missing information, and conflicts. "
        "No hallucinated facts, odds-derived or LLM-fitted probabilities, score, confidence, "
        "qualification, orders, credentials, protected changes, or provider retries on quota 429. "
        "Report evidence and blocked domains to the existing sport_scout_enrichment "
        "handoff only; do not call any model, terminal reducer, production API, or deployment. "
        "A task being assigned or reported is not verification or production acceptance."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="WOW read-only research Swarm manifest")
    parser.add_argument("--sport", required=True, help="canonical sport_key, e.g. basketball_nba")
    parser.add_argument("--event-id", required=True, help="official canonical event ID")
    parser.add_argument("--starts", required=True, help="pregame event UTC ISO-8601")
    parser.add_argument("--home", default="")
    parser.add_argument("--away", default="")
    parser.add_argument("--max-parallel", type=int, default=2, choices=(1, 2, 3))
    parser.add_argument("--format", choices=("json", "prompt"), default="prompt")
    args = parser.parse_args()
    manifest = build_manifest({"sport_key": args.sport, "official_event_id": args.event_id,
                               "commence_time": args.starts, "home_team": args.home,
                               "away_team": args.away, "can_execute": False},
                              max_parallel=args.max_parallel)
    print(antigravity_prompt(manifest) if args.format == "prompt"
          else json.dumps(manifest, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
