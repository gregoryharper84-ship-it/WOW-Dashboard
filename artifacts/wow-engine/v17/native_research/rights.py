"""WOW Source Rights Intelligence.

Turns licence and source-use documentation into structured evidence for
independent review. It never authorizes production use, and a software
licence is never read as a licence for the underlying sports data.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any, Mapping

from .contract import sha256_json

SOFTWARE_LICENSES = re.compile(r"^(MIT|Apache-2\.0|GPL-.*|LGPL-.*|AGPL-.*|BSD-.*|MPL-2\.0|ISC|Unlicense)$", re.I)
DATA_LICENSES = (
    ("CC0-1.0", re.compile(r"\bCC0\b|public domain dedication", re.I)),
    ("ODbL-1.0", re.compile(r"\bODbL\b|open database licen[cs]e", re.I)),
    ("CC-BY-SA-4.0", re.compile(r"\bCC[ -]BY[ -]SA\b|attribution[- ]sharealike", re.I)),
    ("CC-BY-4.0", re.compile(r"\bCC[ -]BY\b(?![ -](NC|SA))|creative commons attribution(?![- ](non|share))", re.I)),
    ("CC-BY-NC", re.compile(r"\bCC[ -]BY[ -]NC\b|attribution[- ]noncommercial", re.I)),
)
NON_COMMERCIAL = re.compile(r"non[- ]?commercial|may not (sell|sub-?licen[cs]e|charge)|personal use only|"
                            r"not for commercial", re.I)
NO_SCRAPING = re.compile(r"(no|not permit\w*|prohibit\w*)\s+(automated|scraping|crawl)", re.I)
ATTRIBUTION = re.compile(r"\battribut(e|ion)\b", re.I)
# Upstream providers whose data terms a compilation cannot re-license.
THIRD_PARTY_UPSTREAMS = ("ESPN", "FotMob", "Soccerway", "Opta", "Sofascore", "Transfermarkt", "FBref",
                         "WhoScored", "Flashscore", "Understat", "football-data.co.uk", "Sportradar",
                         "StatsBomb", "Genius Sports")
OPEN_UPSTREAMS = ("OpenLigaDB", "openfootball", "fixturedownload.com", "Wikidata")


def _found(pattern: re.Pattern[str], text: str) -> str | None:
    m = pattern.search(text)
    if not m:
        return None
    return text[max(0, m.start() - 80): m.end() + 80].replace("\n", " ").strip()


def assess_source_rights(source_id: str, *, software_spdx: str | None, terms_text: str | None,
                         terms_url: str | None, robots_permitted: bool, assessed_at: datetime) -> dict[str, Any]:
    text = terms_text or ""
    data_label = next((label for label, pat in DATA_LICENSES if pat.search(text)), None)
    non_commercial = bool(NON_COMMERCIAL.search(text)) or data_label == "CC-BY-NC"
    if non_commercial and data_label is None:
        data_label = "CUSTOM_NON_COMMERCIAL"
    upstream = [u for u in THIRD_PARTY_UPSTREAMS if re.search(re.escape(u), text, re.I)]
    open_upstream = [u for u in OPEN_UPSTREAMS if re.search(re.escape(u), text, re.I)]
    software_only = bool(software_spdx and SOFTWARE_LICENSES.match(software_spdx)) and data_label is None
    unresolved_upstream = bool(upstream)
    if data_label is None:
        storage = training = production = "UNRESOLVED"
    elif non_commercial:
        storage, training, production = "NON_COMMERCIAL_ONLY", "NON_COMMERCIAL_ONLY", "RESTRICTED_NON_COMMERCIAL"
    elif data_label == "ODbL-1.0" or data_label == "CC-BY-SA-4.0":
        storage, training, production = "YES", "YES", "SHARE_ALIKE_REVIEW_REQUIRED"
    else:
        storage, training, production = "YES", "YES", "YES_PENDING_INDEPENDENT_REVIEW"
    if unresolved_upstream and production.startswith("YES"):
        production = "UPSTREAM_RIGHTS_UNRESOLVED"
        training = "UPSTREAM_RIGHTS_UNRESOLVED"
    automated = "NO" if NO_SCRAPING.search(text) else ("YES" if robots_permitted else "NO")
    evidence = []
    label_pattern = next((pat for label, pat in DATA_LICENSES if label == data_label), None)
    for label, pattern in (("data_license", label_pattern), ("non_commercial", NON_COMMERCIAL),
                           ("attribution", ATTRIBUTION)):
        if pattern is not None and (snippet := _found(pattern, text)):
            evidence.append({"kind": label, "excerpt": snippet[:600]})
    record = {
        "source_id": source_id,
        "assessed_at": assessed_at.isoformat(),
        "terms_url": terms_url,
        "terms_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None,
        "software_license_spdx": software_spdx,
        "data_license_label": data_label or "NONE_FOUND",
        "software_license_not_data_license": software_only,
        "research_access": "PUBLIC_NO_AUTH" if robots_permitted else "NOT_PERMITTED",
        "storage_permitted": storage,
        "automated_acquisition_permitted": automated,
        "model_training_permitted": training,
        "production_use_permitted": production,
        "attribution_required": bool(ATTRIBUTION.search(text)) or (data_label or "").startswith("CC-BY"),
        "commercial_restrictions": ["NON_COMMERCIAL_ONLY"] if non_commercial else [],
        "upstream_sources": upstream + open_upstream,
        "unresolved_upstream_rights": unresolved_upstream,
        "evidence": evidence,
        "review_status": "PENDING_INDEPENDENT_REVIEW",
        "production_use_authorized": False,
    }
    record["rights_id"] = sha256_json({k: record[k] for k in sorted(record) if k != "assessed_at"})
    return record


def storage_status(rights: Mapping[str, Any] | None) -> str:
    """Document bodies are stored only when the source's terms clearly permit storage."""
    if rights and rights.get("storage_permitted") == "YES" and not rights.get("unresolved_upstream_rights"):
        return "STORAGE_PERMITTED"
    if rights and rights.get("storage_permitted") == "NON_COMMERCIAL_ONLY":
        return "METADATA_ONLY_NON_COMMERCIAL"
    return "METADATA_ONLY_RIGHTS_UNRESOLVED"


def training_permitted(rights: Mapping[str, Any] | None) -> bool:
    return bool(rights) and rights.get("model_training_permitted") == "YES" \
        and not rights.get("unresolved_upstream_rights") and not rights.get("commercial_restrictions")
