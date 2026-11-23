"""Produce independently reviewed news facts linked to a discovery receipt.

GDELT/article metadata alone never becomes a reviewed fact. An external
authorized reviewer must supply publisher/league/team claims with publication
time, event linkage and an independent review reference. This producer only
validates shape/provenance, rejects conflicts and syndicated duplicates, and
attaches accepted rows onto Multiscout candidates. It never scores, never
authenticates reviewer cryptography, and never sets can_execute=true.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

try:
    from v17.scout_source_policy import HIGH_IMPACT_EVIDENCE as HIGH_IMPACT
except ModuleNotFoundError:  # pragma: no cover
    from scout_source_policy import HIGH_IMPACT_EVIDENCE as HIGH_IMPACT

HIGH_IMPACT = HIGH_IMPACT | frozenset({"INJURY_STATUS", "STARTING_GOALIE_STATUS", "ROTATION_STATUS"})
OFFICIAL_CLASSES = frozenset({"LEAGUE_OFFICIAL", "TEAM_OFFICIAL"})
ALLOWED_CLASSES = OFFICIAL_CLASSES | frozenset({"PRIMARY_BEAT_REPORTER", "SECONDARY_MEDIA"})
SCHEMA = "wow.v17.news_reviewed_facts_bundle.v1"
CAN_EXECUTE = False


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
        parts = urlsplit(url)
        return (
            parts.scheme == "https"
            and bool(parts.hostname)
            and not parts.username
            and not parts.password
            and not parts.fragment
        )
    except ValueError:
        return False


def _headline_key(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = unicodedata.normalize("NFKC", value).casefold()
    tokens = re.findall(r"\w+", normalized, flags=re.UNICODE)
    return " ".join(tokens) if tokens else None


def _hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def produce_reviewed_facts(
    discovery: Mapping[str, Any],
    claims: Sequence[Mapping[str, Any]],
    *,
    observed_at: datetime | None = None,
    source_receipt_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate independent review claims against one discovery receipt.

    Returns an auditable bundle. Rejected claims stay listed with typed reasons;
    accepted facts are the only ones eligible for Scout attachment.
    """
    now = observed_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("NEWS_REVIEW_TIME_MUST_BE_AWARE")
    now = now.astimezone(timezone.utc)

    raw_sha = (
        source_receipt_sha256
        if isinstance(source_receipt_sha256, str) and re.fullmatch(r"[0-9a-f]{64}", source_receipt_sha256)
        else None
    )
    base: dict[str, Any] = {
        "schema_version": SCHEMA,
        "produced_at_utc": now.isoformat(),
        "source_receipt_sha256": raw_sha,
        "discovery_source_code": discovery.get("source_code"),
        "discovery_sport_key": discovery.get("sport_key"),
        "accepted_facts": [],
        "rejected_claims": [],
        "conflicts": [],
        "prediction_authority": False,
        "model_feature_eligible": False,
        "model_rescore_verified": False,
        "can_execute": False,
    }

    def finish(status: str) -> dict[str, Any]:
        out = dict(base)
        out["producer_status"] = status
        out["receipt_sha256"] = _hash(out)
        return out

    if source_receipt_sha256 is not None and raw_sha is None:
        return finish("NEWS_REVIEW_SOURCE_DIGEST_INVALID")
    if discovery.get("can_execute") is not False or discovery.get("prediction_authority") is not False:
        return finish("NEWS_REVIEW_DISCOVERY_AUTHORITY_INVALID")
    if discovery.get("source_status") != "OK":
        return finish("NEWS_REVIEW_DISCOVERY_BLOCKED")
    articles = discovery.get("articles")
    if not isinstance(articles, list):
        return finish("NEWS_REVIEW_DISCOVERY_SHAPE_INVALID")

    article_urls = {
        article.get("source_url")
        for article in articles
        if isinstance(article, dict) and _valid_https(article.get("source_url"))
    }
    headlines_by_url = {
        article["source_url"]: _headline_key(article.get("title"))
        for article in articles
        if isinstance(article, dict) and _valid_https(article.get("source_url"))
    }
    if not article_urls:
        return finish("NEWS_REVIEW_NO_ATTRIBUTED_ARTICLES")

    accepted: list[dict[str, Any]] = []
    for index, claim in enumerate(claims):
        if not isinstance(claim, Mapping):
            base["rejected_claims"].append({"index": index, "reason": "CLAIM_NOT_OBJECT"})
            continue
        url = claim.get("source_url")
        if not isinstance(url, str) or not _valid_https(url) or url not in article_urls:
            base["rejected_claims"].append({"index": index, "reason": "SOURCE_URL_NOT_IN_DISCOVERY"})
            continue
        sport = claim.get("sport_key")
        event_id = claim.get("official_event_id")
        if not isinstance(sport, str) or sport != discovery.get("sport_key"):
            base["rejected_claims"].append({"index": index, "reason": "SPORT_MISMATCH"})
            continue
        if not isinstance(event_id, str) or not event_id.strip():
            base["rejected_claims"].append({"index": index, "reason": "OFFICIAL_EVENT_ID_MISSING"})
            continue
        if claim.get("confirmed") is not True:
            base["rejected_claims"].append({"index": index, "reason": "CLAIM_NOT_CONFIRMED"})
            continue
        published = _utc(claim.get("publisher_published_at_utc"))
        reviewed = _utc(claim.get("independently_reviewed_at_utc"))
        if published is None or reviewed is None or not published <= reviewed <= now:
            base["rejected_claims"].append({"index": index, "reason": "TIMESTAMP_INVALID"})
            continue
        if not isinstance(claim.get("independent_review_ref"), str) or not claim["independent_review_ref"].strip():
            base["rejected_claims"].append({"index": index, "reason": "INDEPENDENT_REVIEW_REF_MISSING"})
            continue
        if not isinstance(claim.get("source_group"), str) or not claim["source_group"].strip():
            base["rejected_claims"].append({"index": index, "reason": "SOURCE_GROUP_MISSING"})
            continue
        source_class = claim.get("source_class")
        if not isinstance(source_class, str) or source_class not in ALLOWED_CLASSES:
            base["rejected_claims"].append({"index": index, "reason": "UNSUPPORTED_PUBLISHER_CLASS"})
            continue
        evidence_kind = claim.get("evidence_kind")
        fact_value = claim.get("fact_value")
        if not isinstance(evidence_kind, str) or not 0 < len(evidence_kind) <= 80:
            base["rejected_claims"].append({"index": index, "reason": "EVIDENCE_KIND_INVALID"})
            continue
        if not isinstance(fact_value, str) or not 0 < len(fact_value) <= 250:
            base["rejected_claims"].append({"index": index, "reason": "FACT_VALUE_INVALID"})
            continue
        accepted.append({
            "source_url": url,
            "sport_key": sport,
            "official_event_id": event_id.strip(),
            "publisher_published_at_utc": published.isoformat(),
            "independently_reviewed_at_utc": reviewed.isoformat(),
            "independent_review_ref": str(claim["independent_review_ref"]).strip(),
            "source_group": str(claim["source_group"]).strip(),
            "source_class": source_class,
            "confirmed": True,
            "evidence_kind": evidence_kind,
            "subject_id": str(claim.get("subject_id") or ""),
            "fact_value": fact_value,
            "material_change": claim.get("material_change") is True,
            "source_receipt_sha256": raw_sha,
            "discovery_headline_key": headlines_by_url.get(url),
        })

    # Collapse duplicates and quarantine conflicts / syndicated high-impact copies.
    # Dedup/conflict keys are scoped to official_event_id so multi-event bundles
    # do not silently drop or cross-quarantine unrelated games.
    unique: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    url_groups: dict[str, str] = {}
    for fact in accepted:
        url = fact["source_url"]
        if url in url_groups and url_groups[url] != fact["source_group"]:
            base["conflicts"].append("SOURCE_URL_MULTI_GROUP")
            return finish("NEWS_REVIEW_SOURCE_CONFLICT")
        url_groups[url] = fact["source_group"]
        key = (
            fact["official_event_id"],
            fact["source_group"],
            fact["evidence_kind"],
            fact["subject_id"],
        )
        prior = unique.get(key)
        if prior is not None:
            if prior["fact_value"] != fact["fact_value"] or prior["material_change"] != fact["material_change"]:
                base["conflicts"].append("INTRA_SOURCE_CONFLICT")
                return finish("NEWS_REVIEW_SOURCE_CONFLICT")
            # Deterministic collapse of syndicated duplicates in the same group.
            if (fact["source_url"], fact["independent_review_ref"]) < (
                prior["source_url"], prior["independent_review_ref"]
            ):
                unique[key] = fact
            else:
                base["rejected_claims"].append({
                    "index": "duplicate",
                    "reason": "SYNDICATED_OR_DUPLICATE_CLAIM",
                    "source_url": fact["source_url"],
                })
        else:
            unique[key] = fact

    rows = sorted(
        unique.values(),
        key=lambda r: (
            r["official_event_id"],
            r["evidence_kind"],
            r["subject_id"],
            r["source_group"],
            r["source_url"],
        ),
    )
    by_subject: dict[tuple[str, str, str], set[str]] = {}
    for row in rows:
        by_subject.setdefault(
            (row["official_event_id"], row["evidence_kind"], row["subject_id"]),
            set(),
        ).add(row["fact_value"])
    if any(len(values) > 1 for values in by_subject.values()):
        base["conflicts"].append("CONTRADICTORY_SOURCE_FACTS")
        return finish("NEWS_REVIEW_SOURCE_CONFLICT")

    # High-impact secondary claims require distinct non-syndicated corroboration
    # unless an official league/team class already confirmed the same fact.
    material = [r for r in rows if r["material_change"]]
    for row in material:
        if row["evidence_kind"] not in HIGH_IMPACT or row["source_class"] in OFFICIAL_CLASSES:
            continue
        confirmations = [
            r for r in rows
            if r["official_event_id"] == row["official_event_id"]
            and r["evidence_kind"] == row["evidence_kind"]
            and r["subject_id"] == row["subject_id"]
            and r["fact_value"] == row["fact_value"]
        ]
        if any(r["source_class"] in OFFICIAL_CLASSES for r in confirmations):
            continue
        if (
            len({r["source_url"] for r in confirmations}) < 2
            or len({r["source_group"] for r in confirmations}) < 2
        ):
            base["rejected_claims"].append({
                "index": "high_impact",
                "reason": "HIGH_IMPACT_CONFIRMATION_REQUIRED",
                "subject_id": row["subject_id"],
            })
            return finish("NEWS_REVIEW_HIGH_IMPACT_CONFIRMATION_REQUIRED")
        headline_keys = {r.get("discovery_headline_key") for r in confirmations}
        if None in headline_keys or len(headline_keys) < 2:
            base["conflicts"].append("SYNDICATED_OR_UNATTRIBUTED_HEADLINES")
            return finish("NEWS_REVIEW_HIGH_IMPACT_CONFIRMATION_REQUIRED")

    base["accepted_facts"] = rows
    if not rows:
        return finish("NEWS_REVIEW_NO_ACCEPTED_FACTS")
    return finish("NEWS_REVIEW_FACTS_ACCEPTED")


def attach_reviewed_facts_bundle(payload: dict[str, Any], source: Path) -> dict[str, Any]:
    """Attach a same-run reviewed-facts sibling onto matching Multiscout rows."""
    facts_path = source.parent / "news-reviewed-facts.json"
    if not facts_path.exists():
        return payload

    def status(code: str, *, attached: int = 0) -> dict[str, Any]:
        out = dict(payload)
        out["news_reviewed_facts_bridge"] = {
            "status": code,
            "candidates_attached": attached,
            "prediction_authority": False,
            "model_feature_eligible": False,
            "model_rescore_verified": False,
            "can_execute": False,
        }
        return out

    try:
        if facts_path.stat().st_size > 512_000:
            return status("NEWS_REVIEW_BUNDLE_OVERSIZED")
        bundle = json.loads(facts_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return status("NEWS_REVIEW_BUNDLE_UNREADABLE")

    if (
        not isinstance(bundle, dict)
        or bundle.get("schema_version") != SCHEMA
        or bundle.get("can_execute") is not False
        or bundle.get("prediction_authority") is not False
        or bundle.get("producer_status") != "NEWS_REVIEW_FACTS_ACCEPTED"
        or not isinstance(bundle.get("accepted_facts"), list)
    ):
        return status("NEWS_REVIEW_BUNDLE_INVALID")

    facts = [f for f in bundle["accepted_facts"] if isinstance(f, dict)]
    if not facts:
        return status("NEWS_REVIEW_BUNDLE_EMPTY")

    handoff = payload.get("model_handoff")
    if not isinstance(handoff, dict):
        return status("NEWS_REVIEW_NO_SCOUT_HANDOFF")

    # Same-run provenance: reviewed facts may only attach when their source
    # receipt digest matches the current discovery bridge digest.
    bridge = payload.get("public_news_discovery_bridge")
    current_digest = (
        bridge.get("source_sha256")
        if isinstance(bridge, dict) and isinstance(bridge.get("source_sha256"), str)
        else None
    )
    bundle_digest = bundle.get("source_receipt_sha256")
    if (
        not isinstance(current_digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", current_digest)
        or not isinstance(bundle_digest, str)
        or bundle_digest != current_digest
    ):
        return status("NEWS_REVIEW_SOURCE_DIGEST_MISMATCH")
    if any(
        not isinstance(fact.get("source_receipt_sha256"), str)
        or fact.get("source_receipt_sha256") != current_digest
        for fact in facts
    ):
        return status("NEWS_REVIEW_FACT_DIGEST_MISMATCH")

    by_event: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for fact in facts:
        sport = fact.get("sport_key")
        event_id = fact.get("official_event_id")
        if isinstance(sport, str) and isinstance(event_id, str):
            by_event.setdefault((sport, event_id), []).append(fact)

    updated = dict(handoff)
    attached = 0
    for lane in ("team_event_candidates", "prop_candidates"):
        rows = handoff.get(lane)
        if not isinstance(rows, list):
            continue
        new_rows = []
        for row in rows:
            if not isinstance(row, dict):
                new_rows.append(row)
                continue
            item = dict(row)
            key = (str(item.get("sport_key") or ""), str(item.get("official_event_id") or ""))
            matched = by_event.get(key)
            row_digest = item.get("public_news_discovery_sha256")
            if (
                matched
                and item.get("official_event_identity_verified") is True
                and row_digest == current_digest
            ):
                item["news_reviewed_facts"] = list(matched)
                attached += 1
            new_rows.append(item)
        updated[lane] = new_rows

    out = status("NEWS_REVIEWED_FACTS_ATTACHED" if attached else "NEWS_REVIEW_NO_MATCHING_EVENT", attached=attached)
    out["model_handoff"] = updated
    out["news_reviewed_facts_bundle"] = bundle
    return out


__all__ = [
    "CAN_EXECUTE",
    "SCHEMA",
    "attach_reviewed_facts_bundle",
    "produce_reviewed_facts",
]
