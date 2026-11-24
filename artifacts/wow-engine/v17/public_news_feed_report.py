"""Bounded user-readable WOW newsfeed from one GDELT discovery receipt.

This is a *research headlines feed*, not a verified publisher-fact writer.
No network, models, API credentials, subscription, odds, or execution authority.
One source sample covers only its named sport. All other sport coverage stays
explicitly NOT_SAMPLED_THIS_RUN; a green Actions badge cannot upgrade status.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from v17.public_gdelt_discovery import SPORT_QUERIES

SCHEMA = "wow.v17.public_research_newsfeed.v1"
MAX_RECEIPT_BYTES = 512_000
MAX_ITEMS = 30
SPORTS = tuple(key for key, _ in SPORT_QUERIES)


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _public_https(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > 2048:
        return False
    try:
        p = urlsplit(value)
        return (p.scheme == "https" and bool(p.hostname)
                and p.hostname not in {"localhost", "127.0.0.1", "::1"}
                and not p.username and not p.password and not p.fragment
                and p.port in (None, 443))
    except ValueError:
        return False


def _headline_key(value: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value).casefold()))


def build_feed(raw: bytes, *, run_id: str | None = None,
               at: datetime | None = None) -> dict[str, Any]:
    now = at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("NEWS_FEED_AS_OF_REQUIRES_TIMEZONE")
    now = now.astimezone(timezone.utc)
    base: dict[str, Any] = {
        "schema_version": SCHEMA,
        "created_at_utc": now.isoformat(),
        "run_id": run_id,
        "source": "GDELT",
        "source_receipt_sha256": hashlib.sha256(raw).hexdigest() if raw else None,
        "source_status": "UNAVAILABLE",
        "source_code": "SOURCE_RECEIPT_MISSING",
        "sampled_sport": None,
        "source_observed_at": None,
        "selection_policy": None,
        "article_count_in_receipt": 0,
        "headlines": [],
        "duplicate_or_invalid_articles": 0,
        "sport_coverage": {key: "NOT_SAMPLED_THIS_RUN" for key in SPORTS},
        "coverage_is_exhaustive": False,
        "publisher_facts_verified": False,
        "model_feature_eligible": False,
        "model_rescore_verified": False,
        "probability_authority": False,
        "can_execute": False,
        "status": "SOURCE_RECEIPT_MISSING",
    }
    if not raw:
        return base
    if len(raw) > MAX_RECEIPT_BYTES:
        return {**base, "status": "SOURCE_RECEIPT_OVERSIZED"}
    try:
        receipt = json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        return {**base, "status": "SOURCE_RECEIPT_INVALID"}
    if (not isinstance(receipt, dict)
            or receipt.get("schema_version") != "wow.v17.public_news_receipt.v1"
            or receipt.get("prediction_authority") is not False
            or receipt.get("can_execute") is not False
            or receipt.get("model_feature_eligible") is not False
            or not isinstance(receipt.get("source_code"), str)
            or receipt.get("source_status") not in ("OK", "BLOCKED")
            or not isinstance(receipt.get("articles"), list)
            or len(receipt["articles"]) > MAX_ITEMS):
        return {**base, "status": "SOURCE_RECEIPT_INVALID"}
    sport = receipt.get("sport_key")
    if sport not in SPORTS:
        return {**base, "status": "SOURCE_SPORT_INVALID"}
    observed = _timestamp(receipt.get("source_observed_at"))
    out = {
        **base, "source_status": receipt["source_status"],
        "source_code": receipt["source_code"], "sampled_sport": sport,
        "source_observed_at": receipt.get("source_observed_at"),
        "selection_policy": receipt.get("selection_policy"),
        "article_count_in_receipt": len(receipt["articles"]),
    }
    if (observed is None or observed > now + timedelta(minutes=2)
            or now - observed > timedelta(hours=24)):
        return {**out, "status": "SOURCE_RECEIPT_STALE_OR_TIME_INVALID"}
    if receipt["source_status"] != "OK" or receipt["source_code"] != "SOURCE_FETCH_OK":
        out["sport_coverage"][sport] = "SAMPLED_SOURCE_BLOCKED"
        return {**out, "status": "SOURCE_DEGRADED"}
    if not receipt["articles"]:
        out["sport_coverage"][sport] = "SAMPLED_NO_ARTICLES"
        return {**out, "status": "SOURCE_EMPTY"}

    seen: dict[str, dict[str, Any]] = {}
    seen_urls: set[str] = set()
    rejected = 0
    for row in receipt["articles"]:
        if not isinstance(row, dict):
            rejected += 1
            continue
        url, title = row.get("source_url"), row.get("title")
        stamp = _timestamp(row.get("source_discovered_at_claim"))
        if (not _public_https(url) or not isinstance(title, str)
                or not title.strip() or stamp is None
                or stamp > observed + timedelta(minutes=2)
                or observed - stamp > timedelta(hours=26)
                or row.get("source_provider") != "GDELT"
                or row.get("official_event_identity_verified") is not False
                or row.get("publisher_claim_verified") is not False
                or row.get("evidence_only") is not True):
            rejected += 1
            continue
        key = _headline_key(title[:300])
        if not key or url in seen_urls:
            rejected += 1
            continue
        seen_urls.add(url)
        host = urlsplit(url).hostname
        if key in seen:
            seen[key]["source_instances"].append({"url": url, "publisher_host": host})
            rejected += 1
            continue
        seen[key] = {
            "headline_id": hashlib.sha256(key.encode("utf-8")).hexdigest()[:24],
            "title": " ".join(title.split())[:300],
            "source_url": url,
            "publisher_host": host,
            "discovered_at_claim": stamp.isoformat(),
            "source_instances": [{"url": url, "publisher_host": host}],
            "publisher_claim_verified": False,
            "official_event_identity_verified": False,
            "model_feature_eligible": False,
        }
    items = sorted(seen.values(), key=lambda x: (-_timestamp(x["discovered_at_claim"]).timestamp(), x["headline_id"]))
    out.update({
        "headlines": items,
        "duplicate_or_invalid_articles": rejected,
        "status": "SOURCE_NONEMPTY_RESEARCH_ONLY" if items else "SOURCE_NO_VALID_HEADLINES",
    })
    out["sport_coverage"][sport] = ("SAMPLED_NONEMPTY_RESEARCH_ONLY" if items
                                    else "SAMPLED_NO_VALID_HEADLINES")
    return out


def render_markdown(feed: dict[str, Any]) -> str:
    def safe(value: Any) -> str:
        plain = " ".join(str(value or "").split())[:300]
        return re.sub(r"([\\\x60*_{}\[\]()#+.!|>])", r"\\\1", plain)

    lines = [
        "# WOW V17 — Public Sports Research Newsfeed", "",
        f"Source status: `{feed['status']}` · Provider: GDELT · "
        f"Sampled sport: `{feed.get('sampled_sport') or 'NONE'}`",
        f"Observed: `{feed.get('source_observed_at') or 'UNAVAILABLE'}` · "
        f"Source code: `{feed['source_code']}`",
        f"Headline groups: **{len(feed['headlines'])}** · "
        f"Rejected or duplicate articles: **{feed['duplicate_or_invalid_articles']}**", "",
        "**Research headlines only.** Links and titles are unverified leads, not "
        "official injury, lineup, event, or model evidence. This is one sport "
        "sample per collection, not all-sport daily coverage.", "",
    ]
    if feed["headlines"]:
        for row in feed["headlines"]:
            # URL is validated as public HTTPS; escape Markdown link terminators.
            url = row["source_url"].replace("(", "%28").replace(")", "%29")
            lines.append(f"- **{safe(row['title'])}** — [{safe(row['publisher_host'])}]({url})"
                         f" · {len(row['source_instances'])} source instance(s)")
    else:
        lines.append(f"No usable headlines in this collection. Native status: `{feed['status']}`.")
    lines += ["", "## Coverage in this collection", "",
              "| Sport | Observed state |", "|---|---|"]
    lines += [f"| `{sport}` | `{state}` |"
              for sport, state in feed["sport_coverage"].items()]
    lines += ["", "No verified news-triggered fitted probabilities. "
              "`can_execute=false`. Generated from a SHA-256-bound source receipt.", ""]
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--output-json", required=True)
    p.add_argument("--output-md", required=True)
    p.add_argument("--run-id")
    args = p.parse_args()
    source = Path(args.input)
    try:
        raw = source.read_bytes() if source.exists() and source.stat().st_size <= MAX_RECEIPT_BYTES else (
            b"x" * (MAX_RECEIPT_BYTES + 1) if source.exists() else b""
        )
    except OSError:
        raw = b""
    feed = build_feed(raw, run_id=args.run_id)
    json_dest, md_dest = Path(args.output_json), Path(args.output_md)
    json_dest.parent.mkdir(parents=True, exist_ok=True)
    md_dest.parent.mkdir(parents=True, exist_ok=True)
    json_dest.write_text(json.dumps(feed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_dest.write_text(render_markdown(feed), encoding="utf-8")
    print(json.dumps({"newsfeed_status": feed["status"],
                      "sampled_sport": feed["sampled_sport"],
                      "headlines": len(feed["headlines"]),
                      "source_code": feed["source_code"],
                      "can_execute": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
