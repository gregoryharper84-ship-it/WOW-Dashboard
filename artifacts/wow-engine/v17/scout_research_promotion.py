"""Evidence-only Scout research promotion for WOW V17.

Evaluates already-discovered Scout candidates using research evidence quality.
It never creates model probabilities, never qualifies a wager, and never
executes. Final probability remains the responsibility of the controlling
specialist and can_execute is always false.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

try:
    from v17.scout_source_policy import evidence_quality, source_requirements
except ModuleNotFoundError:
    from scout_source_policy import evidence_quality, source_requirements

RESEARCH_STATUSES = {
    "WATCH",
    "RESEARCH_INTEREST_LOW",
    "RESEARCH_INTEREST_MEDIUM",
    "RESEARCH_INTEREST_HIGH",
    "QUARANTINED",
    "NO_INTEREST",
}

SOURCE_FAMILY_BY_CLASS = {
    "LEAGUE_OFFICIAL": "OFFICIAL",
    "TEAM_OFFICIAL": "OFFICIAL",
    "VENUE_OFFICIAL": "VENUE",
    "PRIMARY_BEAT_REPORTER": "PRIMARY_REPORTING",
    "ESTABLISHED_STATS_PROVIDER": "STRUCTURED_STATS",
    "SPORTSBOOK_FEED": "MARKET",
    "WEATHER_PROVIDER": "WEATHER",
    "SECONDARY_MEDIA": "SECONDARY",
    "SOCIAL_UNVERIFIED": "UNVERIFIED",
}

MARKET_SOURCE_FAMILY = "MARKET"


def _aware(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _age_minutes(value: Any, *, now: datetime) -> float | None:
    parsed = _aware(value)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds() / 60.0)


def _market_rows(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = candidate.get("market_evidence")
    if isinstance(evidence, dict):
        return [evidence]
    if isinstance(evidence, list):
        return [row for row in evidence if isinstance(row, dict)]
    return []


def _domain_rows(candidate: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Return the typed per-domain evidence contract when present.

    research_domain_evidence is intentionally evidence-only. Each domain
    contains one row or a list of rows with source_class, captured_at/effective_at,
    confirmed, and optional source identity fields. No probability fields are
    consumed or produced here.
    """
    raw = candidate.get("research_domain_evidence")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for domain, evidence in raw.items():
        key = str(domain or "").strip().lower()
        if not key:
            continue
        if isinstance(evidence, dict):
            out[key] = [evidence]
        elif isinstance(evidence, list):
            out[key] = [row for row in evidence if isinstance(row, dict)]
    return out


def _source_family(source_class: str) -> str:
    return SOURCE_FAMILY_BY_CLASS.get(str(source_class or "").upper(), "UNVERIFIED")


def _source_identity(row: dict[str, Any], family: str) -> tuple[str, str]:
    """Collapse sportsbook books into one market family.

    Many books provide useful market breadth, but they are not independent
    sporting-information sources. Other evidence families may retain a source
    identity when the producer supplies one.
    """
    if family == MARKET_SOURCE_FAMILY:
        return family, "SPORTSBOOK_MARKET_COMPLEX"
    identity = (
        row.get("source_provider")
        or row.get("source_id")
        or row.get("source")
        or row.get("source_url")
        or row.get("publisher")
        or family
    )
    return family, str(identity)


def _confirmed(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    return str(value).strip().lower() in {"1", "true", "yes", "confirmed"}


def _row_quality(
    row: dict[str, Any],
    *,
    now: datetime,
    default_source_class: str,
    default_evidence_type: str,
) -> dict[str, Any]:
    source_class = str(row.get("source_class") or default_source_class).upper()
    captured_at = (
        row.get("captured_at")
        or row.get("effective_at")
        or row.get("market_last_update")
        or row.get("bookmaker_last_update")
    )
    age = _age_minutes(captured_at, now=now)
    evidence_type = str(row.get("evidence_type") or default_evidence_type)
    quality = evidence_quality(
        source_class,
        age_minutes=age,
        confirmed=_confirmed(row.get("confirmed")),
        evidence_type=evidence_type,
    )
    family = _source_family(source_class)
    return {
        **quality,
        "family": family,
        "identity": _source_identity(row, family),
    }


def _required_domain_state(
    candidate: dict[str, Any],
    *,
    now: datetime,
    market_usable: bool,
) -> tuple[
    list[str],
    list[str],
    dict[str, bool],
    set[str],
    set[tuple[str, str]],
    list[float],
    int,
    int,
]:
    requirements = source_requirements(str(candidate.get("sport_key") or ""))
    required = list(requirements.keys())
    domain_rows = _domain_rows(candidate)
    complete: dict[str, bool] = {}
    families: set[str] = set()
    independent_sources: set[tuple[str, str]] = set()
    freshness_scores: list[float] = []
    usable_rows = 0
    fresh_rows = 0

    dossier = candidate.get("scout_dossier") if isinstance(candidate.get("scout_dossier"), dict) else None
    dossier_coverage = (
        dossier.get("required_domain_coverage")
        if isinstance(dossier, dict) and isinstance(dossier.get("required_domain_coverage"), dict)
        else None
    )
    dossier_ledger = (
        [row for row in dossier.get("evidence_ledger", []) if isinstance(row, dict)]
        if isinstance(dossier, dict)
        else []
    )
    if dossier_coverage is not None:
        complete = {
            domain: bool(dossier_coverage.get(str(domain).lower(), False))
            for domain in required
        }
        for entry in dossier_ledger:
            if str(entry.get("domain") or "").lower() == "market":
                continue
            if entry.get("research_usable") is not True:
                continue
            family = str(entry.get("source_family") or "UNVERIFIED")
            source_id = str(entry.get("source_id") or family)
            families.add(family)
            independent_sources.add((family, source_id))
            usable_rows += 1
            if entry.get("dynamic_stale") is not True:
                fresh_rows += 1
            age = entry.get("age_minutes")
            max_age = entry.get("dynamic_max_age_minutes")
            if isinstance(age, (int, float)) and isinstance(max_age, (int, float)) and max_age > 0:
                freshness_scores.append(max(0.0, min(100.0, 100.0 * (1.0 - float(age) / float(max_age)))))
        if complete.get("market") and market_usable:
            families.add(MARKET_SOURCE_FAMILY)
            independent_sources.add((MARKET_SOURCE_FAMILY, "SPORTSBOOK_MARKET_COMPLEX"))
        missing = [domain for domain in required if not complete.get(str(domain).lower(), False)]
        return (
            required,
            missing,
            complete,
            families,
            independent_sources,
            freshness_scores,
            usable_rows,
            fresh_rows,
        )

    for domain in required:
        key = str(domain).lower()
        allowed_classes = {str(value).upper() for value in requirements.get(domain, [])}
        if key == "market":
            complete[key] = market_usable
            if market_usable:
                families.add(MARKET_SOURCE_FAMILY)
                independent_sources.add((MARKET_SOURCE_FAMILY, "SPORTSBOOK_MARKET_COMPLEX"))
            continue

        rows = domain_rows.get(key, [])
        domain_usable = False
        for row in rows:
            quality = _row_quality(
                row,
                now=now,
                default_source_class="SOCIAL_UNVERIFIED",
                default_evidence_type=key.upper(),
            )
            source_allowed = str(quality["source_class"]).upper() in allowed_classes
            if quality["research_usable"] and source_allowed:
                usable_rows += 1
                domain_usable = True
                families.add(str(quality["family"]))
                independent_sources.add(quality["identity"])
            if not quality["stale"] and source_allowed:
                fresh_rows += 1
            age = quality.get("age_minutes")
            if age is not None and source_allowed:
                max_age = max(float(quality["max_age_minutes"]), 1.0)
                freshness_scores.append(max(0.0, min(100.0, 100.0 * (1.0 - age / max_age))))
        complete[key] = domain_usable

    missing = [domain for domain in required if not complete.get(str(domain).lower(), False)]
    return (
        required,
        missing,
        complete,
        families,
        independent_sources,
        freshness_scores,
        usable_rows,
        fresh_rows,
    )


def evaluate_candidate(candidate: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Return a non-predictive research disposition for one Scout candidate.

    Promotion is based on independent evidence families and sport-required
    research-domain completeness. Sportsbook breadth is retained as useful
    market telemetry but never counts as independent sporting corroboration.
    """
    now = now or datetime.now(timezone.utc)
    rows = _market_rows(candidate)
    blockers = list(candidate.get("market_evidence_source_blockers") or [])
    contradictions = [str(x) for x in (candidate.get("contradictory_evidence") or [])]
    red_flags = [str(x) for x in (candidate.get("red_team_flags") or [])]

    market_usable = 0
    market_fresh = 0
    market_required_usable = False
    books: set[str] = set()
    source_families: set[str] = set()
    independent_sources: set[tuple[str, str]] = set()
    freshness_scores: list[float] = []
    market_allowed_classes = {
        str(value).upper()
        for value in source_requirements(str(candidate.get("sport_key") or "")).get("market", [])
    }
    for row in rows:
        quality = _row_quality(
            row,
            now=now,
            default_source_class="SPORTSBOOK_FEED",
            default_evidence_type="MARKET_EVIDENCE",
        )
        if quality["research_usable"]:
            market_usable += 1
            source_families.add(str(quality["family"]))
            independent_sources.add(quality["identity"])
            if not market_allowed_classes or str(quality["source_class"]).upper() in market_allowed_classes:
                market_required_usable = True
        if not quality["stale"]:
            market_fresh += 1
        age = quality.get("age_minutes")
        if age is not None:
            max_age = max(float(quality["max_age_minutes"]), 1.0)
            freshness_scores.append(max(0.0, min(100.0, 100.0 * (1.0 - age / max_age))))
        book = row.get("bookmaker") or row.get("source_provider") or row.get("bookmaker_title")
        if book:
            books.add(str(book))

    (
        required_domains,
        missing_required_domains,
        domain_complete,
        domain_families,
        domain_sources,
        domain_freshness_scores,
        domain_usable_rows,
        domain_fresh_rows,
    ) = _required_domain_state(candidate, now=now, market_usable=market_required_usable)

    source_families.update(domain_families)
    independent_sources.update(domain_sources)
    freshness_scores.extend(domain_freshness_scores)

    identity_fields = (
        candidate.get("official_event_id"),
        candidate.get("sport_key"),
        candidate.get("commence_time"),
        candidate.get("home_team"),
        candidate.get("away_team"),
    )
    identity_complete = all(v not in (None, "") for v in identity_fields)
    domain_rows = _domain_rows(candidate)
    evidence_count = len(rows) + sum(len(value) for value in domain_rows.values())
    usable = market_usable + domain_usable_rows
    fresh = market_fresh + domain_fresh_rows

    required_total = len(required_domains)
    required_complete = required_total - len(missing_required_domains)
    domain_completeness = (
        round(100.0 * required_complete / required_total, 2)
        if required_total
        else (100.0 if usable > 0 else 0.0)
    )
    data_completeness = round(
        0.25 * (100.0 if identity_complete else 0.0) + 0.75 * domain_completeness,
        2,
    )
    source_freshness_score = (
        round(sum(freshness_scores) / len(freshness_scores), 2)
        if freshness_scores
        else 0.0
    )
    source_family_count = len(source_families)
    independent_source_count = len(independent_sources)
    dossier = candidate.get("scout_dossier") if isinstance(candidate.get("scout_dossier"), dict) else None
    research_worker_barrier_status = (
        str(dossier.get("research_worker_barrier_status") or "PARTIAL")
        if isinstance(dossier, dict)
        else "NOT_PRESENT"
    )

    if contradictions or red_flags:
        status = "QUARANTINED"
        reason = "MATERIAL_CONFLICT_OR_RED_TEAM_FLAG"
    elif not identity_complete:
        status = "WATCH"
        reason = "IDENTITY_INCOMPLETE"
    elif evidence_count == 0:
        status = "WATCH"
        reason = "RESEARCH_EVIDENCE_MISSING"
    elif usable == 0 or fresh == 0:
        status = "WATCH"
        reason = "NO_FRESH_RESEARCH_USABLE_EVIDENCE"
    elif blockers:
        status = "RESEARCH_INTEREST_LOW"
        reason = "PARTIAL_SOURCE_BLOCKED"
    elif (
        required_total
        and not missing_required_domains
        and source_family_count >= 2
        and research_worker_barrier_status in {"READY", "NOT_PRESENT"}
    ):
        status = "RESEARCH_INTEREST_HIGH"
        reason = "REQUIRED_DOMAINS_COMPLETE_INDEPENDENT_EVIDENCE"
    elif required_total and domain_completeness >= 60.0 and source_family_count >= 2:
        status = "RESEARCH_INTEREST_MEDIUM"
        reason = "PARTIAL_REQUIRED_DOMAINS_INDEPENDENT_EVIDENCE"
    else:
        status = "RESEARCH_INTEREST_LOW"
        reason = (
            "REQUIRED_RESEARCH_DOMAINS_INCOMPLETE"
            if required_total and missing_required_domains
            else "INSUFFICIENT_INDEPENDENT_EVIDENCE"
        )

    return {
        "research_status": status,
        "research_reason": reason,
        "data_completeness": data_completeness,
        "research_domain_completeness": domain_completeness,
        "required_research_domains": required_domains,
        "required_domain_coverage": domain_complete,
        "missing_required_domains": missing_required_domains,
        "source_freshness_score": source_freshness_score,
        "research_evidence_count": evidence_count,
        "research_usable_evidence_count": usable,
        "research_fresh_evidence_count": fresh,
        "research_bookmaker_count": len(books),
        "research_source_family_count": source_family_count,
        "research_independent_source_count": independent_source_count,
        "market_observations_are_one_source_family": True,
        "research_worker_barrier_status": research_worker_barrier_status,
        "red_team": {
            "status": "QUARANTINED" if status == "QUARANTINED" else "PASSED",
            "flags": red_flags,
        },
        "probability": None,
        "prediction_authority": False,
        "can_execute": False,
    }


def promote_handoff(payload: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    if payload.get("can_execute") is not False:
        raise ValueError("SCOUT_HANDOFF_CAN_EXECUTE_MUST_BE_FALSE")
    out = dict(payload)
    handoff = dict(out.get("model_handoff") or {})
    counts = {status: 0 for status in RESEARCH_STATUSES}
    for lane in ("team_event_candidates", "prop_candidates"):
        updated: list[dict[str, Any]] = []
        for row in handoff.get(lane, []) or []:
            if not isinstance(row, dict):
                continue
            candidate = dict(row)
            evaluation = evaluate_candidate(candidate, now=now)
            candidate.update(evaluation)
            candidate["research_ceiling"] = "RESEARCH_INTEREST"
            candidate["probability_authority"] = False
            candidate["can_execute"] = False
            counts[evaluation["research_status"]] += 1
            updated.append(candidate)
        handoff[lane] = updated
    out["model_handoff"] = handoff
    out["research_promotion"] = {
        "status_counts": counts,
        "sportsbook_evidence_only": True,
        "market_observations_are_one_source_family": True,
        "promotion_requires_required_domain_coverage": True,
        "final_probability_requires_controlling_specialist": True,
        "research_ceiling": "RESEARCH_INTEREST",
        "can_execute": False,
    }
    out["can_execute"] = False
    return out


__all__ = ["evaluate_candidate", "promote_handoff"]
