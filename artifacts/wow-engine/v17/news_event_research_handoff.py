"""Fail-closed GDELT discovery -> event-specific research handoff.

GDELT metadata is only a discovery lead. This module does not validate publisher
claims, authenticate an independent reviewer, build fitted features, or rescore
models. A downstream specialist must explicitly re-score and freeze a new receipt.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

try:
    from v17.scout_source_policy import HIGH_IMPACT_EVIDENCE as HIGH_IMPACT
except ModuleNotFoundError:
    from scout_source_policy import HIGH_IMPACT_EVIDENCE as HIGH_IMPACT
OFFICIAL_CLASSES = frozenset({"LEAGUE_OFFICIAL", "TEAM_OFFICIAL"})


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None or dt.utcoffset() is None:
        return None
    return dt.astimezone(timezone.utc)


def _valid_https(url: Any) -> bool:
    if not isinstance(url, str) or len(url) > 2048:
        return False
    try:
        p = urlsplit(url)
        return p.scheme == "https" and bool(p.hostname) and not p.username and not p.password and not p.fragment
    except ValueError:
        return False


def _hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def reconcile_news_handoff(
    discovery: Mapping[str, Any],
    event: Mapping[str, Any],
    reviewed_facts: Sequence[Mapping[str, Any]] = (),
    *,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Produce one auditable, non-authorizing event-specific news research receipt.

    reviewed_facts are OUTSIDE attestations; their signatures and truth are not
    verified here. A matching article, official event ID, exact timestamps and
    independent-review reference only establish a research handoff candidate.
    """
    now = observed_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("NEWS_RECEIPT_TIME_MUST_BE_AWARE")
    now = now.astimezone(timezone.utc)
    sport = event.get("sport_key")
    event_id = event.get("official_event_id")
    kickoff = _utc(event.get("event_start_utc"))
    base = {
        "schema_version": "wow.v17.news_event_research_handoff.v1",
        "sport_key": sport,
        "official_event_id": event_id,
        "source_code": discovery.get("source_code"),
        "research_status": "NEWS_INPUT_INVALID",
        "news_context": [],
        "source_conflicts": [],
        "research_rescore_requested": False,
        "model_rescore_verified": False,
        "model_feature_eligible": False,
        "historical_pregame_eligible": False,
        "calibrated_probability": None,
        "prediction_authority": False,
        "can_execute": False,
    }

    def finish(status: str, items: list[dict[str, Any]] | None = None,
               conflicts: list[str] | None = None, request: bool = False) -> dict[str, Any]:
        out = dict(base)
        out["research_status"] = status
        out["news_context"] = items or []
        out["source_conflicts"] = conflicts or []
        out["research_rescore_requested"] = request
        out["receipt_sha256"] = _hash(out)
        return out

    if not isinstance(sport, str) or not sport or not isinstance(event_id, str) or not event_id or kickoff is None:
        return finish("NEWS_CANONICAL_EVENT_UNVERIFIED")
    if now >= kickoff:
        return finish("NEWS_EVENT_ALREADY_STARTED")
    if discovery.get("can_execute") is not False or discovery.get("prediction_authority") is not False:
        return finish("NEWS_DISCOVERY_AUTHORITY_INVALID")
    if discovery.get("source_status") != "OK":
        return finish("NEWS_DISCOVERY_BLOCKED")
    if discovery.get("sport_key") != sport:
        return finish("NEWS_SPORT_MISMATCH")
    source_seen = _utc(discovery.get("source_observed_at"))
    if source_seen is None or source_seen > now or source_seen >= kickoff:
        return finish("NEWS_DISCOVERY_TIME_INVALID")

    articles = discovery.get("articles")
    if not isinstance(articles, list):
        return finish("NEWS_DISCOVERY_SHAPE_INVALID")
    article_urls = {
        article.get("source_url")
        for article in articles
        if isinstance(article, dict) and _valid_https(article.get("source_url"))
    }
    if not article_urls:
        return finish("NEWS_NO_ATTRIBUTED_ARTICLES")

    verified = []
    for fact in reviewed_facts:
        if not isinstance(fact, Mapping):
            continue
        url = fact.get("source_url")
        if not isinstance(url, str) or not _valid_https(url) or url not in article_urls:
            continue
        if fact.get("official_event_id") != event_id or fact.get("sport_key") != sport:
            continue
        published = _utc(fact.get("publisher_published_at_utc"))
        reviewed = _utc(fact.get("independently_reviewed_at_utc"))
        if published is None or reviewed is None or not published <= reviewed <= now or published >= kickoff or reviewed >= kickoff:
            continue
        if not isinstance(fact.get("independent_review_ref"), str) or not fact["independent_review_ref"].strip():
            continue
        if not isinstance(fact.get("source_group"), str) or not fact["source_group"].strip():
            continue
        if not isinstance(fact.get("source_class"), str) or fact["source_class"] not in OFFICIAL_CLASSES | {"PRIMARY_BEAT_REPORTER", "SECONDARY_MEDIA"}:
            continue
        if fact.get("confirmed") is not True:
            continue
        if not isinstance(fact.get("evidence_kind"), str) or not 0 < len(fact["evidence_kind"]) <= 80:
            continue
        if not isinstance(fact.get("fact_value"), str) or not 0 < len(fact["fact_value"]) <= 250:
            continue
        max_age_mins = {"LEAGUE_OFFICIAL": 720, "TEAM_OFFICIAL": 360,
                        "PRIMARY_BEAT_REPORTER": 240, "SECONDARY_MEDIA": 360}[fact["source_class"]]
        if (now - published).total_seconds() > max_age_mins * 60:
            continue
        verified.append({
            "source_url": url,
            "source_group": str(fact["source_group"]),
            "source_class": fact["source_class"],
            "independent_review_ref": str(fact["independent_review_ref"]),
            "publisher_published_at_utc": published.isoformat(),
            "evidence_kind": fact["evidence_kind"],
            "subject_id": str(fact.get("subject_id") or ""),
            "fact_value": fact["fact_value"],
            "material_change": fact.get("material_change") is True,
        })
    if not verified:
        return finish("NEWS_DISCOVERY_UNVERIFIED")

    # One group cannot masquerade as multiple independent confirmations.
    unique = {}
    for fact in verified:
        key = (fact["source_group"], fact["evidence_kind"], fact["subject_id"])
        if key in unique and unique[key]["fact_value"] != fact["fact_value"]:
            return finish("NEWS_SOURCE_CONFLICT", conflicts=["INTRA_SOURCE_CONFLICT"])
        unique[key] = fact
    rows = sorted(unique.values(), key=lambda r: (
        r["evidence_kind"], r["subject_id"], r["source_group"], r["source_url"]
    ))
    by_subject = {}
    for row in rows:
        by_subject.setdefault((row["evidence_kind"], row["subject_id"]), set()).add(row["fact_value"])
    if any(len(values) > 1 for values in by_subject.values()):
        return finish("NEWS_SOURCE_CONFLICT", conflicts=["CONTRADICTORY_SOURCE_FACTS"])

    material = [r for r in rows if r["material_change"]]
    if not material:
        return finish("NEWS_CONTEXT_RESEARCH_ONLY", rows)
    for row in material:
        if row["evidence_kind"] in HIGH_IMPACT and row["source_class"] not in OFFICIAL_CLASSES:
            groups = {
                r["source_group"] for r in rows
                if r["evidence_kind"] == row["evidence_kind"]
                and r["subject_id"] == row["subject_id"]
                and r["fact_value"] == row["fact_value"]
            }
            if len(groups) < 2:
                return finish("NEWS_HIGH_IMPACT_CONFIRMATION_REQUIRED", rows)
    return finish("NEWS_RESEARCH_RESCORE_REQUESTED", rows, request=True)


__all__ = ["reconcile_news_handoff"]
