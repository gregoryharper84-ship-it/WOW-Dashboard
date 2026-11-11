"""Read-only, offline source-coverage and rights audit for V17 research tooling.

Derives its matrix ONLY from the existing entitlement registry
(``model_source_entitlements``) and free-source catalog (``free_source_catalog``).
It is not a registry, fetcher, license grant, training authorization, or
probability source. No network, storage, or secrets are touched. A catalog or
registry entry is never treated as proof of live availability or of a verified
license: both require an explicit receipt, and none is accepted here by default.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping

from v17.free_source_catalog import SOURCES as CATALOG, SPORTS
from v17.model_source_entitlements import SOURCES as REGISTRY, source_readiness

CAN_EXECUTE = False
AUDIT_VERSION = "WOW_V17_SOURCE_COVERAGE_AUDIT_V1"
RESEARCH_TOOLS = ("mlflow", "duckdb", "evidently", "optuna")

B_NO_LIVE_RECEIPT = "LIVE_ENDPOINT_RECEIPT_ABSENT"
B_NO_LICENSE_RECEIPT = "LICENSE_VERIFICATION_RECEIPT_ABSENT"
B_UNCLASSIFIED = "NOT_IN_ENTITLEMENT_REGISTRY"
B_NO_ADAPTER = "ADAPTER_REFERENCE_NOT_FOUND"
B_CERT_REVIEW = "CERTIFICATION_SOURCE_REVIEW_REQUIRED"
B_RIGHTS_UNDOCUMENTED = "RIGHTS_UNDOCUMENTED_FIRST_PARTY"
B_NONCOMMERCIAL = "TERMS_RESTRICT_NONCOMMERCIAL"
B_RECEIPT_WEAK = "LICENSE_RECEIPT_NOT_PROBATIVE"
B_UPSTREAM = "UPSTREAM_RIGHTS_UNVERIFIED"
B_TIMESTAMP_GAP = "PUBLICATION_TIMESTAMP_ABSENT"
RECEIPT_SCHEMA = "WOW_SOURCE_RIGHTS_RECEIPTS_V1"


EVIDENCE_GRADES = frozenset({"RAW_BYTES", "SUMMARY", "SEARCH_SNIPPET", "SECONDARY", "NONE"})
# A probative license receipt must be re-captured at least this recently. Older
# evidence stays on file as history but can no longer clear the license blocker.
MAX_PROBATIVE_AGE = timedelta(days=30)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_STR_FIELDS = ("source_id", "finding", "evidence_grade", "url", "excerpt", "excerpt_sha256", "retrieved_at")


def _parse_retrieved_at(value) -> date:
    if not isinstance(value, str):
        raise ValueError("SOURCE_RIGHTS_RECEIPT_RETRIEVED_AT_INVALID")
    try:
        return date.fromisoformat(value.strip()[:10]) if len(value.strip()) == 10 else datetime.fromisoformat(value.strip()).date()
    except ValueError as exc:
        raise ValueError("SOURCE_RIGHTS_RECEIPT_RETRIEVED_AT_INVALID") from exc


def _digest_matches(item: Mapping) -> bool:
    excerpt, digest = item.get("excerpt"), item.get("excerpt_sha256")
    if not isinstance(excerpt, str) or not excerpt or not isinstance(digest, str) or not _SHA256_RE.match(digest):
        return False
    return hashlib.sha256(excerpt.encode("utf-8")).hexdigest() == digest


def receipt_is_probative(item: Mapping, *, now: datetime | None = None) -> bool:
    """True only when a receipt independently verifies as fresh raw-byte license evidence.

    Declared ``finding``/``evidence_grade`` are claims. This re-derives what can be
    checked offline: the stored excerpt must hash to its declared digest, the
    retrieval date must be a real date that is neither future-dated nor older than
    ``MAX_PROBATIVE_AGE``, and the URL must be https. It does NOT prove the bytes
    were authentically captured from that URL: that requires a server-owned capture
    and remains a tracked open item. Nothing here authorizes training.
    """
    try:
        if item.get("finding") != "PERMITS_TRAINING" or item.get("evidence_grade") != "RAW_BYTES":
            return False
        if not _digest_matches(item):
            return False
        url = item.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            return False
        retrieved = _parse_retrieved_at(item.get("retrieved_at"))
    except (ValueError, AttributeError):
        return False
    today = (now or datetime.now(timezone.utc)).date()
    return today - MAX_PROBATIVE_AGE <= retrieved <= today


def load_rights_receipts(path: str | Path) -> dict[str, list[dict]]:
    """Load typed rights findings keyed by source_id. Fails closed on bad shape.

    Every receipt must be explicitly present and well formed: a missing or non-list
    ``receipts`` collection, a non-object entry, a blank/non-string field, an unknown
    evidence grade, an invalid date or an excerpt whose SHA-256 does not match its
    declared digest rejects the whole file. A lost collection is never an empty audit.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema") != RECEIPT_SCHEMA or raw.get("can_execute") is not False:
        raise ValueError("SOURCE_RIGHTS_RECEIPT_SCHEMA_INVALID")
    if "receipts" not in raw or not isinstance(raw["receipts"], list):
        raise ValueError("SOURCE_RIGHTS_RECEIPTS_COLLECTION_MISSING")
    out: dict[str, list[dict]] = {}
    for item in raw["receipts"]:
        if not isinstance(item, dict):
            raise ValueError("SOURCE_RIGHTS_RECEIPT_NOT_AN_OBJECT")
        for key in _REQUIRED_STR_FIELDS:
            value = item.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"SOURCE_RIGHTS_RECEIPT_FIELD_MISSING:{key}")
        if item["evidence_grade"] not in EVIDENCE_GRADES:
            raise ValueError("SOURCE_RIGHTS_RECEIPT_GRADE_UNKNOWN")
        _parse_retrieved_at(item["retrieved_at"])
        if not _digest_matches(item):
            raise ValueError("SOURCE_RIGHTS_RECEIPT_DIGEST_MISMATCH")
        out.setdefault(item["source_id"], []).append(item)
    return out


def unmatched_receipts(rows: Iterable[dict], receipts: Mapping[str, list[dict]]) -> list[str]:
    known = {r["source_id"] for r in rows}
    return sorted(set(receipts) - known)


def _adapter_exists(ref: str, engine_root: Path) -> bool:
    """Resolve only the explicitly referenced path from either accepted root."""
    if not isinstance(ref, str) or not ref or ref.startswith(("http:", "https:")):
        return False
    relative = Path(ref)
    if relative.is_absolute() or ".." in relative.parts:
        return False
    root = Path(engine_root).resolve()
    if (root / "artifacts" / "wow-engine").is_dir():
        repo_root, engine = root, root / "artifacts" / "wow-engine"
    elif root.name == "wow-engine" and root.parent.name == "artifacts":
        repo_root, engine = root.parent.parent, root
    else:
        # Arbitrary test root: do not search outside it or match by basename.
        repo_root, engine = root, root
    return any((base / relative).is_file() for base in (repo_root, engine))


def build_source_matrix(
    engine_root: str | Path,
    *,
    live_receipts: Mapping[str, str] | None = None,
    license_receipts: Mapping[str, str] | None = None,
    rights_receipts: Mapping[str, list[dict]] | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """One row per (sport, source). Receipts are source_id -> evidence id."""
    root = Path(engine_root).resolve()
    # A receipt id must be a non-blank string; a blank/None id never clears a blocker.
    live = {k: v for k, v in dict(live_receipts or {}).items() if isinstance(v, str) and v.strip()}
    lic = {k: v for k, v in dict(license_receipts or {}).items() if isinstance(v, str) and v.strip()}
    findings = dict(rights_receipts or {})
    for _sid, _items in findings.items():
        # Loader output is keyed by each embedded source_id. Direct callers
        # must satisfy the same boundary: no receipt may be relabeled under
        # another source's key to alter its license or rights blockers.
        if (not isinstance(_sid, str) or not _sid.strip()
                or not isinstance(_items, list)
                or not all(isinstance(f, dict) and f.get("source_id") == _sid
                           for f in _items)):
            raise ValueError("SOURCE_RIGHTS_RECEIPT_MAPPING_INVALID")
    rows: list[dict] = []
    catalog_by_reg = {s.registry_source_id: s for s in CATALOG if s.registry_source_id}
    seen: set[tuple[str, str]] = set()

    def emit(sport: str, source_id: str, cat, reg_id: str | None) -> None:
        if (sport, source_id) in seen:
            return
        seen.add((sport, source_id))
        reg = REGISTRY.get(reg_id) if reg_id else None
        readiness = source_readiness(reg_id) if reg_id else None
        blockers: list[str] = []
        if reg is None:
            blockers.append(B_UNCLASSIFIED)
        else:
            blockers.extend(readiness.blockers)
            if reg.certification_source_review_required:
                blockers.append(B_CERT_REVIEW)
            if reg.use == "CANDIDATE_FIRST_PARTY_UNDOCUMENTED":
                blockers.append(B_RIGHTS_UNDOCUMENTED)
        if cat is None or not _adapter_exists(cat.adapter_reference, root):
            blockers.append(B_NO_ADAPTER)
        if source_id not in live:
            blockers.append(B_NO_LIVE_RECEIPT)
        found = findings.get(source_id, [])
        probative = any(receipt_is_probative(f, now=now) for f in found)
        if source_id not in lic and not probative:
            blockers.append(B_RECEIPT_WEAK if found else B_NO_LICENSE_RECEIPT)
        if any(f.get("finding") == "RESTRICTS_NONCOMMERCIAL" for f in found):
            blockers.append(B_NONCOMMERCIAL)
        if any(f.get("upstream_unverified") for f in found):
            blockers.append(B_UPSTREAM)
        if any(f.get("timestamp_gap") for f in found):
            blockers.append(B_TIMESTAMP_GAP)
        rows.append({
            "sport": sport,
            "source_id": source_id,
            "in_catalog": cat is not None,
            "catalog_integration": getattr(cat, "integration", None),
            "catalog_rights_state": getattr(cat, "rights_state", None),
            "adapter_reference": getattr(cat, "adapter_reference", None),
            "in_registry": reg is not None,
            "registry_use": getattr(reg, "use", None),
            "license_id": getattr(reg, "license_id", None),
            "license_url": getattr(reg, "license_url", None),
            "attribution_required": getattr(reg, "attribution_required", None),
            "candidate_training_ready": bool(readiness and readiness.ready_for_candidate_training),
            "live_endpoint_receipt": live.get(source_id),
            "license_verification_receipt": lic.get(source_id),
            "rights_findings": [f.get("finding") for f in found],
            "blockers": sorted(set(blockers)),
            # Legal, reproducible fitted training needs NO blockers at all.
            "legal_reproducible_fitted_training": not blockers,
            "probability_source": False,
            "can_execute": False,
        })

    for s in CATALOG:
        for sport in s.sports:
            emit(sport, s.source_id, s, s.registry_source_id)
    for reg_id, reg in REGISTRY.items():
        for sport in reg.sports:
            emit(sport, reg_id, catalog_by_reg.get(reg_id), reg_id)
    unknown = {r["sport"] for r in rows} - set(SPORTS)
    if unknown:
        raise RuntimeError(f"SOURCE_COVERAGE_UNKNOWN_SPORT:{sorted(unknown)}")
    return sorted(rows, key=lambda r: (r["sport"], r["source_id"]))


def sport_summary(rows: Iterable[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        s = out.setdefault(r["sport"], {"sources": 0, "candidate_training_ready": 0,
                                        "legal_reproducible_fitted_training": 0})
        s["sources"] += 1
        s["candidate_training_ready"] += int(r["candidate_training_ready"])
        s["legal_reproducible_fitted_training"] += int(r["legal_reproducible_fitted_training"])
    return out


def research_tool_usage(root: str | Path) -> dict[str, list[str]]:
    """AST scan: which .py files actually import each research tool (no exec)."""
    found: dict[str, list[str]] = {t: [] for t in RESEARCH_TOOLS}
    base = Path(root)
    for path in sorted(base.rglob("*.py")):
        if any(p in {"node_modules", ".venv", "venv", "__pycache__", ".git", "site-packages", "build", "dist"} for p in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module.split(".")[0]]
            for n in names:
                if n in found:
                    rel = str(path.relative_to(base))
                    if rel not in found[n]:
                        found[n].append(rel)
    return found


__all__ = ["AUDIT_VERSION", "CAN_EXECUTE", "MAX_PROBATIVE_AGE", "build_source_matrix", "load_rights_receipts", "receipt_is_probative", "unmatched_receipts", "research_tool_usage", "sport_summary"]
